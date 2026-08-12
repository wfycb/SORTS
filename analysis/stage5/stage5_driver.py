#!/usr/bin/env python3
"""STAGE5 무인 드라이버 — 부하 스윕 본 배치.

  1. 사전: 동결 6파일 md5 · precheck · DSB 컨테이너 기동시각 스냅샷(72개)
  2. 샘플러 기동(.12 CPU / S1 CPU+NIC, pidfile 방식 — I-15)
  3. run_all.py 서브프로세스로 매니페스트 실행, 15 s 폴링
     정지: 컨테이너 재시작 · 연속 3런 실패 · 디스크 부족 · 생성기 갈래(a) 3런 이상
  4. 종료: 샘플러 수거 → radio clear → cleanup_all → precheck → s5_analyze
"""
import json
import os
import shutil
import subprocess
import sys
import time

EXP = "/home/user/exp"
sys.path.insert(0, EXP)
import run_all  # noqa: E402

MAN = os.environ.get("S5_MANIFEST", os.path.join(EXP, "manifest_stage5.json"))
OUT = os.environ.get("S5_OUT", os.path.join(EXP, "runs/stage5-20260812"))
RUNNER_LOG = os.path.join(OUT, "runner.log")
SITES = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
LOADGEN = "192.168.0.12"
MIN_DISK_GB = 20
FREEZE = {"sorts_ctl.py": "97d63b83044b07a3bba969a2d7f8614f",
          "obs.py": "b9fac68d079b017acf99d451cd9ddbae",
          "sorts.yaml.tmpl": "9467651b8374bb2e92ce9d1cd093d639",
          "run_all.py": "79babd3db8b83f051e496168e6dff2a4",
          "gen_envoy_v10.py": "ace343788b45fdcfada23908fac237a9",
          "envoy_keys.json": "071cf575ed7ec9d9700c3c312de01b35"}
_status = {"start_ts": time.time(), "runs": {}, "stops": [], "containers": {}}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def save():
    _status["heartbeat"] = time.time()
    tmp = os.path.join(OUT, "driver_status.json.tmp")
    json.dump(_status, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(OUT, "driver_status.json"))


def sh(cmd, timeout=90):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def freeze_check():
    bad = []
    for f, want in FREEZE.items():
        rc, o, _ = sh(f"md5sum {os.path.join(EXP, f)}")
        got = o.split()[0] if o else "?"
        if got != want:
            bad.append(f"{f} {got} != {want}")
    return bad


def container_state():
    """사이트별 (개수, 기동시각 집합 해시) — 재시작 감지용 (지시 §7)."""
    st = {}
    for ip, name in SITES.items():
        rc, o, _ = sh(f"ssh -o ConnectTimeout=8 {ip} "
                      f"'docker ps -q | wc -l; "
                      f"docker inspect --format \"{{{{.State.StartedAt}}}}\" "
                      f"$(docker ps -q) | sort | md5sum'")
        parts = o.split("\n")
        st[name] = {"n": parts[0].strip() if parts else "?",
                    "started_md5": parts[1].split()[0] if len(parts) > 1 else "?"}
    return st


SAMPLER = (
    "nohup bash -c 'echo $$ > /var/tmp/s5_sample.pid; while true; do "
    "printf \"%s \" $(date +%s); grep ^cpu\\  /proc/stat | tr -s \" \" \" \"; "
    "__NET__ echo; sleep 2; done' > /var/tmp/s5_sample.txt 2>&1 < /dev/null &")
NET = ('for f in /sys/class/net/enp1s0/statistics/rx_bytes '
       '/sys/class/net/enp1s0/statistics/tx_bytes; do '
       'printf \"%s=%s \" $(basename $f) $(cat $f); done;')


def sampler(host, on, tag=None, net=False):
    if on:
        subprocess.run(["ssh", "-n", "-o", "ConnectTimeout=8", host,
                        SAMPLER.replace("__NET__", NET if net else "")], timeout=60)
        log(f"샘플러 기동 {host}")
    else:
        subprocess.run(["ssh", "-n", "-o", "ConnectTimeout=8", host,
                        'PF=/var/tmp/s5_sample.pid; [ -f $PF ] && '
                        'kill "$(cat $PF)" 2>/dev/null; rm -f $PF; true'],
                       timeout=60)
        subprocess.run(["scp", "-q", f"{host}:/var/tmp/s5_sample.txt",
                        os.path.join(OUT, f"sample_{tag}.txt")], timeout=120)
        log(f"샘플러 정지·수거 {host} -> sample_{tag}.txt")


def run_brief(rd):
    """DONE 런의 한 줄 요약 + P-S5-5′ 갈래(달성률 기준 1차)."""
    try:
        s = json.load(open(os.path.join(rd, "summary.json")))
        d = s["sections"]["during"]
        tgt = s.get("target_total_rps") or 1
        ach = d.get("achieved_rps") or 0
        return {"achieved_rps": ach, "achieved_pct": round(100 * ach / tgt, 1),
                "s1_share": d.get("s1_share"), "join": s.get("join_rate")}
    except Exception as e:                            # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def main():
    os.makedirs(OUT, exist_ok=True)
    man = json.load(open(MAN))
    log(f"STAGE5 드라이버 시작 — {len(man['runs'])}런, outdir={OUT}")

    bad = freeze_check()
    if bad:
        log(f"동결 md5 불일치 -> 중단: {bad}")
        return 2
    log("동결 6파일 md5 일치")

    pre = run_all.precheck(run_all.cohort_ips())
    if pre:
        log(f"precheck 실패 -> 중단: {pre}")
        return 2
    log("precheck[pre] 통과")

    base_ct = container_state()
    _status["containers"]["baseline"] = base_ct
    log(f"컨테이너 기준선: {base_ct}")
    if any(v["n"] != "24" for v in base_ct.values()):
        log("컨테이너 24/사이트 아님 -> 중단")
        return 2
    save()

    sampler(LOADGEN, True)
    sampler("192.168.0.3", True, net=True)

    proc = subprocess.Popen([sys.executable, os.path.join(EXP, "run_all.py"),
                             "--manifest", MAN, "--outdir", OUT],
                            stdout=open(RUNNER_LOG, "a"),
                            stderr=subprocess.STDOUT)
    log(f"러너 기동 pid={proc.pid}")
    checked, stop_why, last_ct = set(), None, time.time()
    while proc.poll() is None:
        time.sleep(15)
        save()
        if shutil.disk_usage(EXP).free / 1e9 < MIN_DISK_GB:
            stop_why = "디스크 부족"
        if time.time() - last_ct > 120:               # 2분마다 컨테이너 대조
            last_ct = time.time()
            try:
                now = container_state()
                if now != base_ct:
                    _status["containers"]["diff"] = now
                    stop_why = f"DSB 컨테이너 상태 변화(재시작 정황): {now}"
            except Exception as e:                    # noqa: BLE001
                log(f"컨테이너 대조 실패(무시): {e}")
        pp = os.path.join(OUT, "progress.json")
        prog = {}
        if os.path.exists(pp):
            try:
                prog = json.load(open(pp))
            except ValueError:
                pass
        st = prog.get("runs", {})
        seq = [st[x["run_id"]]["status"] for x in man["runs"] if x["run_id"] in st]
        for i in range(len(seq) - 2):
            if all(s in ("FAILED", "SKIPPED") for s in seq[i:i + 3]):
                stop_why = "연속 3런 실패/스킵"
        for rid in list(st):
            rd = os.path.join(OUT, rid)
            if rid in checked or not os.path.exists(os.path.join(rd, "DONE")):
                continue
            checked.add(rid)
            b = run_brief(rd)
            _status["runs"][rid] = b
            log(f"런 완료 {rid}: {b}")
        if stop_why:
            log(f"무인 중단: {stop_why}")
            _status["stops"].append({"why": stop_why, "ts": time.time()})
            save()
            proc.terminate()
            try:
                proc.wait(180)
            except subprocess.TimeoutExpired:
                proc.kill()
            open(os.path.join(OUT, "BATCH_SUSPECT"), "w").write(stop_why + "\n")
            break
    rc = proc.poll()
    _status["batch"] = {"runner_rc": rc, "stopped": bool(stop_why)}
    log(f"배치 종료 rc={rc}")
    save()

    for host, tag, net in ((LOADGEN, "loadgen", False), ("192.168.0.3", "s1", True)):
        try:
            sampler(host, False, tag=tag, net=net)
        except Exception as e:                        # noqa: BLE001
            log(f"샘플러 수거 실패 {host}: {e}")

    # ---- 원복
    try:
        ips = run_all.cohort_ips()
        run_all.radio(None, None, ips)
        run_all.cleanup_all(ips)
        log("원복: radio clear + cleanup_all 완료")
        post = run_all.precheck(ips)
        _status["restore"] = {"precheck": "통과" if not post else post}
        log(f"precheck[post]: {'통과' if not post else post}")
    except Exception as e:                            # noqa: BLE001
        _status["restore"] = {"error": str(e)}
        log(f"원복 실패: {e}")
    try:
        fin = container_state()
        _status["containers"]["final"] = fin
        log(f"컨테이너 최종: {'기준선 동일' if fin == base_ct else fin}")
    except Exception as e:                            # noqa: BLE001
        log(f"컨테이너 최종 확인 실패: {e}")
    _status["freeze_post"] = freeze_check() or "6/6 일치"
    save()

    # ---- 자동 분석
    try:
        r = subprocess.run([sys.executable,
                            os.path.join(EXP, "analysis/stage5/s5_analyze.py"), OUT],
                           capture_output=True, text=True, timeout=3600)
        open(os.path.join(OUT, "AUTO_RESULTS.md"), "w").write(
            "# STAGE5 자동 분석 (s5_analyze.py)\n\n```\n"
            + r.stdout + "\n```\n" + (("\nstderr:\n```\n" + r.stderr + "\n```\n")
                                      if r.stderr.strip() else ""))
        log("자동 분석 완료 -> AUTO_RESULTS.md")
    except Exception as e:                            # noqa: BLE001
        log(f"자동 분석 실패: {e}")

    open(os.path.join(OUT, "DRIVER_DONE"), "w").write(f"rc={rc}\n")
    log(f"드라이버 종료 rc={rc} stop={stop_why}")
    save()
    return 0


if __name__ == "__main__":
    sys.exit(main())

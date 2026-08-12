#!/usr/bin/env python3
"""STAGE4 본 배치 드라이버 — stage2 판 확장: run 별 shim 재구성 + 게이트 분기.

흐름:
  1. 표준 precheck (실패 시 기동 거부)
  2. .43 컨트롤러 CPU 샘플러 기동 (pidfile 정지 — pkill 자기-킬 함정 I-15)
  3. run_all.py 를 서브프로세스로 실행하며
     - 런마다 tc 폴러 예약(주입 5 s 전 ~ +9 s, 8 ms) -> tcpoll_<rid>.csv
       (v3 에서 가시화 = 발효이므로 이 파일이 '물리적 발효 시각'의 원천)
     - 런 DONE 마다 **신규 앵커 P-S2-0'** 검사 (anchor_spec.json):
         (a) A1(발효) 대역, (b) 결정 내용(d_acc·feasible·changed),
         (c) 반응 지연 < T + 10 ms
       이탈 -> 러너 정지 + BATCH_SUSPECT + 원복 후 종료
     - 오버런 폭주 / 연속 3런 실패 / 디스크 < 20 GB -> 정지
  4. 원복(cleanup_all) + precheck + 샘플러 수거
  5. s2_analyze -> AUTO_RESULTS.md
"""
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

EXP = "/home/user/exp"
sys.path.insert(0, EXP)
import run_all  # noqa: E402

MAN = os.environ.get("S2_MANIFEST", os.path.join(EXP, "manifest_stage4.json"))
OUT = os.environ.get("S2_OUTDIR", os.path.join(EXP, "runs", "stage4-20260812"))
SPEC = os.path.join(EXP, "analysis", "stage2", "anchor_spec.json")
DSTART = float(os.environ.get("S2_DSTART", "120"))
ST = os.path.join(OUT, "driver_status.json")
LOG = os.path.join(OUT, "driver.log")
RUNNER_LOG = os.path.join(OUT, "runner.log")
MIN_DISK_GB = 20

_status = {"start_ts": time.time(), "anchor": {}, "stops": [], "batch": None}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def save():
    _status["heartbeat"] = time.time()
    tmp = ST + ".tmp"
    json.dump(_status, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, ST)


# ---------------------------------------------------------------- shim 스레드
def shim_thread(stop_evt, shim_of):
    """러너 로그의 '[i/N] rid' 를 감지해 그 런의 shim 파라미터를 적용한다.
    렌더·precheck(~30 s)가 부하 기동보다 먼저라 적용 시점은 충분히 이르다."""
    seen = 0
    while not stop_evt.is_set():
        try:
            txt = open(RUNNER_LOG).read() if os.path.exists(RUNNER_LOG) else ""
        except OSError:
            txt = ""
        rids = re.findall(r"\]\s*\[\d+/\d+\]\s+(\S+)", txt)
        if len(rids) > seen:
            rid = rids[seen]
            seen += 1
            p = shim_of.get(rid)
            if p:
                r = subprocess.run(["ssh", "user@192.168.0.43",
                                    "sudo -n /usr/local/sbin/tb-obshim.sh start "
                                    f"{p['delay']:g} {p['avg']} {p['disc']} "
                                    f"{p['eps']:g} {p['seed']}"],
                                   capture_output=True, text=True, timeout=60)
                log(f"shim 구성 {rid}: {(r.stdout or r.stderr).strip()}")
        time.sleep(2)


# ---------------------------------------------------------------- 폴러 스레드
def poll_thread(stop_evt):
    """러너 로그의 '본측정=' 을 런 순서대로 읽어 런마다 폴러를 띄운다."""
    seen = 0
    while not stop_evt.is_set():
        try:
            txt = open(RUNNER_LOG).read() if os.path.exists(RUNNER_LOG) else ""
        except OSError:
            txt = ""
        tm = re.findall(r"본측정=([0-9.]+)", txt)
        rids = re.findall(r"\]\s*\[\d+/\d+\]\s+(\S+)", txt)
        if len(tm) > seen:
            i = seen
            seen += 1
            t_meas = float(tm[i])
            rid = rids[i] if i < len(rids) else f"run{i+1}"
            start = t_meas + DSTART - 5
            log(f"폴러 예약 {rid} (주입 {t_meas + DSTART:.3f})")
            while time.time() < start and not stop_evt.is_set():
                time.sleep(0.5)
            if stop_evt.is_set():
                return
            try:
                with open(os.path.join(OUT, f"tcpoll_{rid}.csv"), "w") as f:
                    subprocess.run(["ssh", "user@192.168.0.43",
                                    "python3 /var/tmp/s2_tcpoll.py 14 0.008"],
                                   stdout=f, stderr=subprocess.DEVNULL, timeout=60)
                log(f"폴러 완료 {rid}")
            except Exception as e:
                log(f"폴러 실패 {rid}: {e}")
        time.sleep(2)


# ---------------------------------------------------------------- 앵커 검사
def effect_time(rid):
    p = os.path.join(OUT, f"tcpoll_{rid}.csv")
    if not os.path.exists(p):
        return None
    for r in csv.DictReader(open(p)):
        try:
            if int(r["n_netem_c1"]) >= 1:
                return float(r["ts"])
        except (ValueError, TypeError):
            continue
    return None


def anchor_check(rid, rd, spec):
    """P-S2-0' (PREREG_S2 §8): (a) A1 대역 (b) 결정 내용 (c) 반응 상한."""
    meta = json.load(open(os.path.join(rd, "meta.json")))
    d43 = meta["clock"]["d43_s"]
    mk = [m for m in meta["marks"] if m.get("phase") == "start"][0]
    T0 = mk["t_issue"] + d43
    T = float(meta.get("arm", {}).get("effective", {}).get("ctl_period_s", 1.0))
    A1 = effect_time(rid)
    det = None
    for r in csv.DictReader(open(os.path.join(rd, "decisions.csv"))):
        if (r["cohort"] == "c1" and r["class"] == "search"
                and r["changed"] == "1" and float(r["ts"]) >= T0 - 0.5):
            det = r
            break
    info = {"A1_rel_s": None if A1 is None else round(A1 - T0, 3),
            "react_from_effect_s": None if (det is None or A1 is None)
            else round(float(det["ts"]) - A1, 3),
            "d_acc_ms": det and det["d_acc_ms"],
            "feasible_set": det and det["feasible_set"],
            "changed": det and det["changed"], "T_s": T}
    bad = []
    if A1 is None:
        bad.append("폴러 결측 — 발효 시각 미상")
    else:
        lo, hi = spec["A1_band_s"]
        if not (lo <= info["A1_rel_s"] <= hi):
            bad.append(f"(a) A1 {info['A1_rel_s']} ∉ [{lo}, {hi}]")
    is_ideal = rid.startswith("s4_ideal") or not rid.startswith("s4_")
    if det is None:
        if is_ideal:
            bad.append("(b) 교란 후 c1:search 전환 없음")
    elif is_ideal:
        if det["d_acc_ms"] != spec["d_acc_ms"]:
            bad.append(f"(b) d_acc {det['d_acc_ms']} != {spec['d_acc_ms']}")
        # G2 §2.2: feasible_set 은 게이트가 아니라 **관측 항목**(경계 함수).
        # 구 사양(anchor_spec.v1.json)에 키가 있으면 하위 호환으로만 검사한다.
        if "feasible_set" in spec and det["feasible_set"] != spec["feasible_set"]:
            bad.append(f"(b) feasible {det['feasible_set']} != {spec['feasible_set']}")
        if spec.get("require_changed") and det["changed"] != spec["require_changed"]:
            bad.append(f"(b) changed {det['changed']} != {spec['require_changed']}")
    if info["react_from_effect_s"] is not None and is_ideal:
        if info["react_from_effect_s"] > T + 0.010:
            bad.append(f"(c) 반응 {info['react_from_effect_s']} > T+10ms")
        if info["react_from_effect_s"] < -0.001:
            bad.append(f"(c) 반응 음수 {info['react_from_effect_s']} — 발효 전 감지")
    return info, bad


def overrun_all(rd, period):
    ts = sorted({float(r["ts"]) for r in
                 csv.DictReader(open(os.path.join(rd, "decisions.csv")))})
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    return bool(gaps) and all(g > period * 1.1 for g in gaps)


def precheck_full(tag):
    bad = list(run_all.precheck(run_all.cohort_ips()) or [])
    r = subprocess.run("sudo -n iptables-save 2>/dev/null | grep -c sorts-fault",
                       shell=True, capture_output=True, text=True)
    if (r.stdout or "0").strip() != "0":
        bad.append(f"iptables sorts-fault 잔재 {r.stdout.strip()}")
    log(f"precheck[{tag}]: {'통과' if not bad else bad}")
    return bad


def main():
    os.makedirs(OUT, exist_ok=True)
    save()
    spec = json.load(open(SPEC))
    log(f"앵커 사양: {json.dumps(spec, ensure_ascii=False)}")
    if precheck_full("pre"):
        _status["stops"].append({"why": "precheck 실패"})
        save()
        return 1

    subprocess.run(["scp", "-q", os.path.join(EXP, "analysis/stage2/s2_tcpoll.py"),
                    "user@192.168.0.43:/var/tmp/s2_tcpoll.py"], timeout=60)
    sampler = r'''PF=/var/tmp/s2_cpusample.pid
[ -f $PF ] && kill "$(cat $PF)" 2>/dev/null; rm -f $PF
cat > /var/tmp/s2_cpusample.sh <<"EOF"
#!/bin/sh
echo $$ > /var/tmp/s2_cpusample.pid
for i in $(seq 1 10800); do
  pid=$(pgrep -f "sorts_ctl[.]py" | head -1)
  if [ -n "$pid" ] && [ -r /proc/$pid/stat ]; then
    echo "$(date +%s.%N) $pid $(awk "{print \$14, \$15}" /proc/$pid/stat)"
  fi
  sleep 1
done
EOF
chmod +x /var/tmp/s2_cpusample.sh
nohup /var/tmp/s2_cpusample.sh > /var/tmp/s2_cpu.log 2>&1 & echo sampler_pid=$!'''
    r = subprocess.run(["ssh", "user@192.168.0.43", sampler],
                       capture_output=True, text=True, timeout=60)
    log(f"CPU 샘플러: {(r.stdout or r.stderr).strip()}")

    man = json.load(open(MAN))
    periods = {x["run_id"]: float(x["ctl_period_s"]) for x in man["runs"]}
    shim_of = {x["run_id"]: x.get("shim") for x in man["runs"]}
    subprocess.run(["ssh", "user@192.168.0.43",
                    "sudo -n /usr/local/sbin/tb-obshim.sh setup"], timeout=60)
    stop_evt = threading.Event()
    th = threading.Thread(target=poll_thread, args=(stop_evt,), daemon=True)
    th.start()
    th2 = threading.Thread(target=shim_thread, args=(stop_evt, shim_of), daemon=True)
    th2.start()

    proc = subprocess.Popen([sys.executable, os.path.join(EXP, "run_all.py"),
                             "--manifest", MAN, "--outdir", OUT],
                            stdout=open(RUNNER_LOG, "a"),
                            stderr=subprocess.STDOUT)
    log(f"러너 기동 pid={proc.pid} ({len(man['runs'])}런)")
    checked, stop_why = set(), None
    while proc.poll() is None:
        time.sleep(15)
        save()
        if shutil.disk_usage(EXP).free / 1e9 < MIN_DISK_GB:
            stop_why = "디스크 부족"
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
            if all(s == "FAILED" for s in seq[i:i + 3]):
                stop_why = "연속 3런 실패"
        for rid in list(st):
            rd = os.path.join(OUT, rid)
            if rid in checked or not os.path.exists(os.path.join(rd, "DONE")):
                continue
            checked.add(rid)
            try:
                if overrun_all(rd, periods[rid]):
                    stop_why = f"{rid}: 오버런 폭주"
            except Exception as e:
                log(f"{rid} 오버런 검사 실패: {e}")
            try:
                info, bad = anchor_check(rid, rd, spec)
                _status["anchor"][rid] = {"info": info, "bad": bad}
                log(f"앵커 {rid}: {info} -> {'OK' if not bad else bad}")
                if bad:
                    stop_why = f"P-S2-0' 이탈 {rid}: {bad}"
            except Exception as e:
                log(f"{rid} 앵커 검사 실패: {e}")
        if stop_why:
            log(f"무인 중단: {stop_why}")
            _status["stops"].append({"why": stop_why, "ts": time.time()})
            save()
            proc.terminate()
            try:
                proc.wait(120)
            except subprocess.TimeoutExpired:
                proc.kill()
            open(os.path.join(OUT, "BATCH_SUSPECT"), "w").write(stop_why + "\n")
            break
    stop_evt.set()
    rc = proc.poll()
    _status["batch"] = {"runner_rc": rc, "stopped": bool(stop_why)}
    save()

    try:
        subprocess.run(["ssh", "user@192.168.0.43",
                        'PF=/var/tmp/s2_cpusample.pid; [ -f $PF ] && '
                        'kill "$(cat $PF)" 2>/dev/null; rm -f $PF; true'], timeout=60)
        subprocess.run(["scp", "-q", "user@192.168.0.43:/var/tmp/s2_cpu.log",
                        os.path.join(OUT, "s2_cpu.log")], timeout=60)
        log("CPU 샘플 수거")
    except Exception as e:
        log(f"CPU 샘플 수거 실패: {e}")
    try:
        r = subprocess.run(["ssh", "user@192.168.0.43",
                            "sudo -n /usr/local/sbin/tb-obshim.sh teardown"],
                           capture_output=True, text=True, timeout=60)
        log(f"shim teardown: {(r.stdout or r.stderr).strip()}")
    except Exception as e:
        log(f"shim teardown 실패: {e}")
    try:
        run_all.cleanup_all(run_all.cohort_ips())
        log("원복: cleanup_all 완료")
    except Exception as e:
        log(f"원복 실패: {e}")
    _status["restore"] = {"precheck": precheck_full("post") or "통과"}
    save()

    if not stop_why:
        try:
            subprocess.run([sys.executable,
                            os.path.join(EXP, "analysis/stage4/s4_analyze.py"), OUT],
                           check=True, timeout=1800,
                           stdout=open(os.path.join(OUT, "AUTO_RESULTS.md"), "w"),
                           stderr=subprocess.STDOUT)
            log("자동 분석 완료 -> AUTO_RESULTS.md")
        except Exception as e:
            log(f"자동 분석 실패: {e}")
    open(os.path.join(OUT, "DRIVER_DONE"), "w").write(f"rc={rc}\n")
    log(f"드라이버 종료 rc={rc} stop={stop_why}")
    return 0 if (rc == 0 and not stop_why) else 1


if __name__ == "__main__":
    sys.exit(main())

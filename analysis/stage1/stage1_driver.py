#!/usr/bin/env python3
"""1단계 무인 드라이버 — 터미널과 무관하게 배치→분석→자동 결과까지 진행.

nohup 으로 기동한다. 순서:
  0. 사전: flush_probe 배포, 시작 상태 확인
  1. 배치1 nf-repro (hc_off) + 오케스트레이터(n=1) + flush 실측(pre 창 110s)
  2. hc_on 전환·검증
  3. 배치2 nf-hc (오케스트레이터 n=6) — HC 이벤트 슬라이스 회수
  4. 배치3 server — 종료 후 outlier/HC 스탯 스냅샷 (재기동 전!)
  5. 배치4 edge (브리지 SORTS+HC 포함) — 종료 후 스탯 스냅샷
  6. hc_off 원복·검증 + iptables 잔재 0 확인 + precheck
  7. s1_analyze.py → AUTO_RESULTS.md

정지 조건 반영: 오케스트레이터 exit 2(해제 실증 실패) → 이후 nf 배치 중단.
전환 검증 실패 → 즉시 원복 시도 후 중단. 어떤 경로든 마지막에 원복을 시도.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

EXP = "/home/user/exp"
ST = os.path.join(EXP, "analysis", "stage1")
OUTROOT = os.path.join(EXP, "runs", "stage1-20260811")
ENVOY = "192.168.0.43"
PY = sys.executable
# 검증 통과 시점(--mode validate OK)의 변형 md5. 재생성본이 다르면 코호트
# 맵 등 입력이 변한 것 — 진행하지 않는다 (조용한 드리프트 금지).
VARIANT_MD5 = {"off": "518cd1a5bd6b6676dd0cea087b2754f8",
               "on": "a6e402f36a0428a1b22d094067387366"}
EXPECT_EP = 27
EXPECT_KEYS = 84


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def sh(cmd, timeout=300):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def out(cmd, timeout=300):
    return sh(cmd, timeout)[1]


def gen_variant(hc):
    """변형 렌더 → md5 대조 → .43 업로드. 반환 md5 (불일치면 None)."""
    v = "on" if hc else "off"
    local = f"/var/tmp/stage1_envoy_hc_{v}.yaml"
    rc, _, err = sh(f"cd {EXP} && {PY} gen_envoy_v10.py {'--hc' if hc else ''} "
                    f"-o {local}", 120)
    if rc != 0:
        log(f"★렌더 실패 hc_{v}: {err}")
        return None
    md5 = out(f"md5sum {local} | cut -d' ' -f1")
    if VARIANT_MD5[v] and md5 != VARIANT_MD5[v]:
        log(f"★변형 md5 불일치 hc_{v}: {md5} != {VARIANT_MD5[v]} — 입력 변화 의심")
        return None
    rc, _, _ = sh(f"scp -q {local} {ENVOY}:/var/tmp/envoy_hc_{v}.yaml", 60)
    if rc != 0:
        log("★변형 업로드 실패")
        return None
    # 같은 렌더 실행이 만든 envoy_keys.json 을 .43 에도 배포 (check_deploy 정합)
    rc, _, _ = sh(f"scp -q {EXP}/envoy_keys.json {ENVOY}:~/envoy_keys.json", 60)
    if rc != 0:
        log("★envoy_keys 배포 실패")
        return None
    return md5


def swap_envoy(hc):
    """config 교체 + 재기동 + §6.1.5 검증. True/False."""
    v = "on" if hc else "off"
    if gen_variant(hc) is None:
        return False
    # /etc/envoy/envoy.yaml 은 user 소유로 전환돼 있다 (사전 조치, sudo 불필요)
    rc, o, e = sh(f"ssh {ENVOY} 'cp /var/tmp/envoy_hc_{v}.yaml /etc/envoy/envoy.yaml "
                  f"&& md5sum /etc/envoy/envoy.yaml /var/tmp/envoy_hc_{v}.yaml'", 60)
    if rc != 0:
        log(f"★config 교체 실패: {e}")
        return False
    m = [ln.split()[0] for ln in o.splitlines()]
    if len(m) != 2 or m[0] != m[1]:
        log(f"★교체 md5 불일치: {o}")
        return False
    sh(f"ssh {ENVOY} 'docker restart front-envoy'", 120)
    for _ in range(30):
        if out(f"ssh {ENVOY} 'curl -s -m 2 http://127.0.0.1:9901/ready'") == "LIVE":
            break
        time.sleep(1)
    else:
        log("★/ready != LIVE")
        return False
    js = out(f"ssh {ENVOY} \"curl -s 'http://127.0.0.1:9901/clusters?format=json'\"", 60)
    try:
        cl = json.loads(js)["cluster_statuses"]
        n_ep = sum(1 for c in cl for h in c.get("host_statuses", [])
                   if h["health_status"]["eds_health_status"] == "HEALTHY")
    except (ValueError, KeyError):
        n_ep = -1
    if n_ep != EXPECT_EP:
        log(f"★healthy EP {n_ep}/{EXPECT_EP}")
        return False
    nk = out(f"ssh {ENVOY} \"curl -s http://127.0.0.1:9901/runtime\" | "
             f"{PY} -c \"import json,sys;"
             f"print(sum(k.startswith('routing.') for k in json.load(sys.stdin)['entries']))\"")
    if nk != str(EXPECT_KEYS):
        log(f"★runtime 키 {nk}/{EXPECT_KEYS}")
        return False
    sys.path.insert(0, EXP)
    import run_all
    run_all.set_policy("site_s3")
    for ip in ("192.168.0.3", "192.168.0.2", "192.168.0.40"):
        n = out(f"ssh -o ConnectTimeout=8 {ip} 'docker ps -q | wc -l'", 60)
        if n != "24":
            log(f"★{ip} 컨테이너 {n}/24")
            return False
    log(f"hc_{v} 전환·검증 통과 (EP 27/27, 키 84, 컨테이너 72 무재시작)")
    return True


def run_batch(manifest, outdir, orch_runs=0, flush_probe=False):
    """배치 실행. 반환 (runner_rc, orch_rc)."""
    os.makedirs(outdir, exist_ok=True)
    blog = os.path.join(outdir, "batch.log")
    bf = open(blog, "a", buffering=1)
    orch = None
    if orch_runs:
        orch = subprocess.Popen(
            [PY, os.path.join(ST, "nodefail_orch.py"), blog, str(orch_runs),
             os.path.join(outdir, "nf_events.json")],
            stdout=open(os.path.join(outdir, "nf_orch.log"), "a", buffering=1),
            stderr=subprocess.STDOUT)
    fl = None
    if flush_probe:
        fl = threading.Thread(target=flush_watch, args=(blog, outdir), daemon=True)
        fl.start()
    runner = subprocess.Popen(
        [PY, os.path.join(EXP, "run_all.py"), "--manifest", manifest,
         "--outdir", outdir, "--resume"],
        cwd=EXP, stdout=bf, stderr=subprocess.STDOUT)
    rrc = runner.wait()
    orc = 0
    if orch:
        try:
            orc = orch.wait(timeout=420)
        except subprocess.TimeoutExpired:
            orch.kill()
            orc = -9
    if fl:
        fl.join(timeout=180)
    log(f"배치 종료 {os.path.basename(outdir)}: runner rc={rrc} orch rc={orc}")
    return rrc, orc


def flush_watch(blog, outdir):
    """배치 로그에서 본측정 시각을 읽어 pre 창(t+5~t+115)에서 flush 실측."""
    t_meas = None
    for _ in range(600):
        try:
            m = re.search(r"부하 기동 t_start=[0-9.]+ 본측정=([0-9.]+)",
                          open(blog, errors="replace").read())
            if m:
                t_meas = float(m.group(1))
                break
        except OSError:
            pass
        time.sleep(1)
    if t_meas is None:
        log("flush: 본측정 시각 미검출 — 실측 생략")
        return
    while time.time() < t_meas + 5:
        time.sleep(0.5)
    log("flush 실측 시작 (110s @ pre 창)")
    rc, o, e = sh(f"ssh {ENVOY} '{PY.split('/')[-1]} ~/flush_probe.py 110 "
                  f"/var/tmp/flush_meas.csv'", 200)
    open(os.path.join(outdir, "flush_result.txt"), "w").write(o + "\n" + e)
    sh(f"scp -q {ENVOY}:/var/tmp/flush_meas.csv {outdir}/flush_meas.csv", 60)
    log(f"flush 실측 완료: {o.splitlines()[-2:] if o else e}")


def stats_snapshot(name):
    o = out(f"ssh {ENVOY} \"curl -s http://127.0.0.1:9901/stats\" | "
            f"grep -E 'outlier_detection|health_check' ", 60)
    p = os.path.join(OUTROOT, f"envoy_stats_{name}.txt")
    open(p, "w").write(o)
    log(f"스탯 스냅샷 -> {p} ({len(o.splitlines())}줄)")


def hc_slice(size0, outdir):
    sz = out(f"ssh {ENVOY} 'stat -c %s /var/log/envoy/hc_events.log 2>/dev/null || echo 0'")
    sh(f"ssh {ENVOY} 'tail -c +{int(size0) + 1} /var/log/envoy/hc_events.log "
       f"2>/dev/null | head -c {max(int(sz) - int(size0), 0)}' > "
       f"{outdir}/hc_events_slice.log", 120)


def main():
    os.makedirs(OUTROOT, exist_ok=True)
    status = {"start_ts": time.time(), "batches": {}}
    sp = os.path.join(OUTROOT, "driver_status.json")

    def save(k, v):
        status["batches"][k] = v
        status["heartbeat"] = time.time()
        json.dump(status, open(sp, "w"), ensure_ascii=False, indent=1)

    sh(f"scp -q {ST}/flush_probe.py {ENVOY}:~/flush_probe.py", 60)
    abort_nf = False
    ok = True

    # -- 배치1: nf-repro (hc_off 이미 배포 상태 — 재확인만)
    cur = out(f"ssh {ENVOY} 'md5sum /etc/envoy/envoy.yaml' | cut -d' ' -f1")
    if cur != VARIANT_MD5["off"]:
        log(f"★시작 상태가 hc_off 가 아님({cur}) — 전환 시도")
        ok = swap_envoy(False)
    if ok:
        rrc, orc = run_batch(os.path.join(EXP, "manifest_stage1_nf_repro.json"),
                             os.path.join(OUTROOT, "nf-repro"),
                             orch_runs=1, flush_probe=True)
        save("nf-repro", {"runner_rc": rrc, "orch_rc": orc})
        if orc == 2:
            abort_nf = True
            log("★오케스트레이터 해제 실증 실패 — nf 후속 배치 중단")

    # -- 배치2: nf-hc (hc_on)
    if ok and not abort_nf:
        ok = swap_envoy(True)
        if ok:
            size0 = out(f"ssh {ENVOY} 'stat -c %s /var/log/envoy/hc_events.log "
                        f"2>/dev/null || echo 0'")
            rrc, orc = run_batch(os.path.join(EXP, "manifest_stage1_nf_hc.json"),
                                 os.path.join(OUTROOT, "nf-hc"), orch_runs=6)
            hc_slice(size0, os.path.join(OUTROOT, "nf-hc"))
            stats_snapshot("after_nf_hc")
            save("nf-hc", {"runner_rc": rrc, "orch_rc": orc})
            if orc == 2:
                abort_nf = True
                log("★해제 실증 실패 — 이후 차단 없음(서버/엣지는 차단 미사용, 계속)")

    # -- 배치3: server (hc_on)
    if ok:
        if out(f"ssh {ENVOY} 'md5sum /etc/envoy/envoy.yaml' | cut -d' ' -f1") \
                != VARIANT_MD5["on"]:
            ok = swap_envoy(True)
        if ok:
            rrc, _ = run_batch(os.path.join(EXP, "manifest_stage1_server.json"),
                               os.path.join(OUTROOT, "server"))
            stats_snapshot("after_server")      # 재기동 전 — outlier ejection 계수
            save("server", {"runner_rc": rrc})

    # -- 배치4: edge (hc_on)
    if ok:
        rrc, _ = run_batch(os.path.join(EXP, "manifest_stage1_edge.json"),
                           os.path.join(OUTROOT, "edge"))
        stats_snapshot("after_edge")
        save("edge", {"runner_rc": rrc})

    # -- 원복 (어떤 경로로 왔든 시도)
    log("hc_off 원복 시작")
    ok_restore = swap_envoy(False)
    sh(f"bash {EXP}/analysis/night2/node_unblock.sh", 60)
    resid = out("sudo -n iptables -S 2>/dev/null | grep -- '--dport 5000' | wc -l")
    sys.path.insert(0, EXP)
    import run_all
    try:
        pb = run_all.precheck(run_all.cohort_ips())
    except Exception as e:
        pb = [f"precheck 예외: {e}"]
    save("restore", {"hc_off": ok_restore, "iptables_5000_rules": resid,
                     "precheck": pb or "통과"})
    log(f"원복: hc_off={ok_restore} iptables잔재={resid} precheck={pb or '통과'}")

    # -- 자동 분석 + 결과 초안
    rc, o, e = sh(f"{PY} {ST}/s1_analyze.py {OUTROOT}", 600)
    md = [
        "# 1단계 자동 결과 (드라이버 무인 산출 — 최종 보고는 STAGE1_REPORT.md)",
        f"\n생성: {time.strftime('%F %T')}  드라이버 상태: driver_status.json\n",
        "## 배치 상태\n```json",
        json.dumps(status["batches"], ensure_ascii=False, indent=1),
        "```\n## 분석 요약 (s1_analyze)\n```",
        o if rc == 0 else f"분석 실패 rc={rc}\n{o}\n{e}",
        "```\n## flush 실측 (§1-6)\n```",
        (open(os.path.join(OUTROOT, "nf-repro", "flush_result.txt")).read()
         if os.path.exists(os.path.join(OUTROOT, "nf-repro", "flush_result.txt"))
         else "없음"),
        "```",
    ]
    open(os.path.join(OUTROOT, "AUTO_RESULTS.md"), "w").write("\n".join(md))
    log(f"완료 — AUTO_RESULTS.md 작성. abort_nf={abort_nf} ok={ok}")


if __name__ == "__main__":
    main()

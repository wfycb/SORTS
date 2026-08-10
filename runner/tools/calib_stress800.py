#!/usr/bin/env python3
"""개정 A §3.4 — 800 rps 에서 stress-ng 강도 재캘리브레이션.

기존 확정 강도 --cpu 3 --cpu-load 80 은 4코어 중 약 2.4개를 뺏어 S3 용량을
약 480 rps 로 떨군다. 800 rps 에서는 붕괴한다(1400 rps 축소판에서 이미
corrected 가 배수되지 않는 영구 backlog 를 만들었다).

--cpu 1 --cpu-load 80 부터 시작해 아래를 만족하는 **최소** 강도를 찾는다.
--cpu 1 이 약하면 --cpu-load 를 올려 미세조정하고, 그래도 안 되면 --cpu 2.

    S3 search p95   교란 전 대비 2배 이상
    달성률          90% 이상
    5xx             1% 미만
    해제 후 복귀    교란 전 값의 1.1배 이내

측정은 800 rps · site_s3 고정.

**지표는 f_c**(Envoy 필드18 US_TX_BEG:US_RX_END − d_net) 다. service_ms 는
d_net 25ms 가 상수로 깔려 "2배 상승" 기준이 왜곡된다 (f_c 5→15ms 여도
service 는 30→40 = 1.33배밖에 안 된다). §3.5 의 "무교란 S3 search p50
10ms 미만"과 같은 지표여야 하고, v5 의 tb-stress.sh 캘리브레이션도 f_c 로 쟀다.
"""
import csv
import json
import subprocess
import sys
import time

sys.path.insert(0, "/home/user/exp")
import run_all

LOADGEN, ENVOY, PIN = "192.168.0.12", "192.168.0.43", "taskset -c 6-15"
OUT = "/home/user/exp/calib"
D = "inDate=2015-04-09&outDate=2015-04-10"
MIX = (f"reserve=1:/reservation?{D}&hotelId=1&customerName=cst8"
       f"&username=Cornell_30&password=0000000000&number=1,"
       f"search=1.5:/hotels?{D}&lat=37.7867&lon=-122.4112,"
       f"recommend=2:/recommendations?require=dis&lat=37.7867&lon=-122.4112")
CONNS, RPS_PER, WARMUP, DUR = 16, 25, 20, 100
TARGET = 800.0
ON_AT, OFF_AT = 38, 75
WIN = {"pre": (5, 35), "during": (42, 72), "post": (79, 98)}
# 최소 강도부터. --cpu 1 안에서 load 를 올려보고 그 다음에 --cpu 2 로 간다.
CANDIDATES = [(1, 80), (1, 90), (1, 100), (2, 80), (2, 90)]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def sh(c, t=900):
    return subprocess.run(c, shell=True, capture_output=True, text=True,
                          timeout=t).stdout.strip()


def stress(cpu, load, on):
    if on:
        return sh(f"TB_STRESS_CPU={cpu} TB_STRESS_LOAD={load} "
                  f"/usr/local/sbin/tb-stress.sh start", 90)
    return sh("/usr/local/sbin/tb-stress.sh stop", 90)


def pctl(xs, q):
    xs = sorted(xs)
    return xs[int(round(q * (len(xs) - 1)))] if xs else float("nan")


def one(cpu, load):
    sh("bash /home/user/setpol.sh site_s3")
    sh("bash /home/user/exp/reserve_reset.sh", 300)
    stress(cpu, load, False)
    off0 = int(run_all.out(f"ssh {ENVOY} 'stat -c %s {run_all.ENVOY_LOG}'", 60) or 0)
    procs = []
    for c in (1, 2):
        cmd = (f'ssh {LOADGEN} "{PIN} python3 ~/tb-load.py --host {ENVOY} --port 8080 '
               f"--cohort {c} --mix '{MIX}' --connections {CONNS} "
               f"--rps-per-connection {RPS_PER} --warmup {WARMUP} --duration {DUR} "
               f'--csv /var/tmp/cst8_c{c}.csv --label cst8-c{c}"')
        procs.append(subprocess.Popen(cmd, shell=True,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.STDOUT))
    t_meas = time.time() + 1.0 + WARMUP
    while time.time() < t_meas + ON_AT:
        time.sleep(0.2)
    stress(cpu, load, True)
    while time.time() < t_meas + OFF_AT:
        time.sleep(0.2)
    stress(cpu, load, False)
    for p in procs:
        p.wait(timeout=900)
    off1 = int(run_all.out(f"ssh {ENVOY} 'stat -c %s {run_all.ENVOY_LOG}'", 60) or 0)
    sl = f"{OUT}/cst8_envoy.log.gz"
    subprocess.run(f"ssh {ENVOY} \"tail -c +{off0 + 1} {run_all.ENVOY_LOG} | "
                   f"head -c {max(off1 - off0, 0)}\" | gzip -1 > {sl}",
                   shell=True, timeout=900)
    hm = run_all.load_hostmap(sl)
    rows = []
    for c in (1, 2):
        sh(f"scp -q {LOADGEN}:/var/tmp/cst8_c{c}.csv {OUT}/")
        rows += [r for r in csv.DictReader(open(f"{OUT}/cst8_c{c}.csv"))
                 if r["warmup"] == "0"]
    res = {"cpu": cpu, "load": load}
    for name, (a, b) in WIN.items():
        sub = [r for r in rows if a <= float(r["end_ts"]) - t_meas < b]
        ok = [r for r in sub if r["status"] == "200"]
        # f_c = 업스트림 왕복 − d_net(S3). 정책이 site_s3 라 전부 S3 다.
        s = []
        for r in ok:
            if r["ep"] != "search":
                continue
            h = hm.get(r["request_id"])
            if h and h[2] is not None and run_all.SITES.get(h[0]) == "S3":
                s.append(h[2] / 1000.0 - run_all.D_NET_MS["S3"])
        n5 = sum(1 for r in sub if r["status"].startswith("5"))
        res[name] = {"fc_search_p95": round(pctl(s, .95), 2),
                     "fc_search_p50": round(pctl(s, .50), 2), "n_fc": len(s),
                     "achieved_rps": round(len(ok) / (b - a), 1),
                     "achieved_pct": round(100 * len(ok) / (b - a) / TARGET, 2),
                     "pct_5xx": round(100 * n5 / len(sub), 4) if sub else None}
    p = res["pre"]["fc_search_p95"]
    res["rise_x"] = round(res["during"]["fc_search_p95"] / p, 3)
    res["recover_x"] = round(res["post"]["fc_search_p95"] / p, 3)
    res["pass"] = {
        "p95 2배 이상": res["rise_x"] >= 2.0,
        "달성률 90% 이상": res["during"]["achieved_pct"] >= 90.0,
        "5xx 1% 미만": (res["during"]["pct_5xx"] or 0) < 1.0,
        "복귀 1.1배 이내": res["recover_x"] <= 1.1}
    res["all_pass"] = all(res["pass"].values())
    return res


if __name__ == "__main__":
    out = []
    chosen = None
    for cpu, load in CANDIDATES:
        log(f"--- 시도: --cpu {cpu} --cpu-load {load} ---")
        r = one(cpu, load)
        out.append(r)
        log(f"    f_c p95 {r['pre']['fc_search_p95']} -> {r['during']['fc_search_p95']} "
            f"-> {r['post']['fc_search_p95']}  상승 {r['rise_x']}x 복귀 {r['recover_x']}x "
            f"달성 {r['during']['achieved_pct']}% 5xx {r['during']['pct_5xx']}%")
        log(f"    {r['pass']}")
        if r["all_pass"]:
            chosen = r
            log(f"*** 확정: --cpu {cpu} --cpu-load {load} (최소 통과 강도)")
            break
    json.dump({"candidates": out, "chosen": chosen},
              open(f"{OUT}/calib_stress800.json", "w"), ensure_ascii=False, indent=1)
    sys.exit(0 if chosen else 1)

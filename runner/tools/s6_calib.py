#!/usr/bin/env python3
"""STEP S6 서버 교란 강도 캘리브레이션 (지시서 v9 §7.3).

한 번 호출 = 한 강도 1런. 구조는 run_all.py 의 축소판이다.
  warmup -> pre(60s) -> 교란 주입 -> during(60s) -> 해제 -> post(60s)

f_c 는 run_all.py 와 **같은 정의**를 쓴다: front Envoy access log 필드18
(US_TX_BEG:US_RX_END, 업스트림 왕복 us) − d_net(사이트). 그래야 기존
캘리브레이션·본실험과 같은 축에서 비교된다.

사용:
  python3 s6_calib.py --target S3 --policy site_s3 --rps 1072 --cpu 2 --load 80
  python3 s6_calib.py --target S1 --policy bl_lr   --rps 800  --cpu 1 --load 30
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import subprocess
import sys
import time

LOADGEN = "192.168.0.12"
ENVOY = "192.168.0.43"
SITES = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
ENVOY_LOG = "/var/log/envoy/front_access.log"
PIN = "taskset -c 6-15"
D_NET_MS = {"S1": 2.0, "S2": 15.0, "S3": 25.006}   # v10: S2 10 -> 15
EXPECT_BYTES = {"reserve": 36, "search": 4474, "recommend": 200}
STRESS = "/usr/local/sbin/tb-stress.sh"
D = "inDate=2015-04-09&outDate=2015-04-10"
MIX = (
    f"reserve=1:/reservation?{D}&hotelId=1&customerName=s6"
    f"&username=Cornell_30&password=0000000000&number=1,"
    f"search=1.5:/hotels?{D}&lat=37.7867&lon=-122.4112,"
    f"recommend=2:/recommendations?require=dis&lat=37.7867&lon=-122.4112"
)
OUT = os.path.expanduser("~/s6")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def sh(cmd, timeout=900):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def out(cmd, timeout=900):
    return sh(cmd, timeout)[1]


def set_policy(p):
    # v10 T2: route 7개 prefix 분리. 구 키(site_weights)는 무시되므로 쓰지 않는다.
    keys = ["site_s1", "site_s2", "site_s3", "bl_rr", "bl_lr", "bl_loc"]
    prefixes = ["c1_search", "c1_reserve", "c1_recommend",
                "c2_search", "c2_reserve", "c2_recommend", "fallback"]
    q = "&".join(f"routing.{pre}.{k}={100 if k == p else 0}"
                 for pre in prefixes for k in keys)
    out(f"ssh {ENVOY} \"curl -s -X POST 'http://127.0.0.1:9901/runtime_modify?{q}'\"", 60)


def pctl(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[int(round(q * (len(xs) - 1)))]


def load_hostmap(path):
    hm = {}
    with gzip.open(path, "rt", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 18:
                continue
            try:
                us = int(p[17]) if p[17].strip().isdigit() else None
                hm[p[1]] = (p[10].split(":")[0], float(p[0]), us)
            except ValueError:
                continue
    return hm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, choices=["S1", "S2", "S3"])
    ap.add_argument("--policy", required=True)
    ap.add_argument("--rps", type=int, required=True)
    ap.add_argument("--cpu", type=int, required=True)
    ap.add_argument("--load", type=int, required=True)
    ap.add_argument("--warmup", type=int, default=45)
    ap.add_argument("--seg", type=int, default=60, help="pre/during/post 각 구간 길이")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    tag = f"{a.target}_cpu{a.cpu}_load{a.load}_rps{a.rps}_{a.policy}"
    rundir = os.path.join(OUT, tag)
    os.makedirs(rundir, exist_ok=True)

    dur = a.seg * 3
    conns_per_cohort = max(1, a.rps // 2 // 25)
    log(f"=== {tag}: 코호트당 conn={conns_per_cohort} x 25rps x 2 = {conns_per_cohort*50} rps ===")

    out(f"{STRESS} {a.target} stop", 90)
    out("bash /home/user/exp/reserve_reset.sh", 300)
    set_policy(a.policy)

    off0 = int(out(f"ssh {ENVOY} 'stat -c %s {ENVOY_LOG}'", 60) or 0)
    procs = []
    for c in (1, 2):
        cmd = (f'ssh {LOADGEN} "{PIN} python3 ~/tb-load.py --host {ENVOY} --port 8080 '
               f"--cohort {c} --mix '{MIX}' --connections {conns_per_cohort} "
               f'--rps-per-connection 25 --warmup {a.warmup} --duration {dur} '
               f'--csv /var/tmp/s6_{tag}_c{c}.csv --label {tag}-c{c}"')
        procs.append(subprocess.Popen(cmd, shell=True,
                                      stdout=open(os.path.join(rundir, f"lg_c{c}.log"), "w"),
                                      stderr=subprocess.STDOUT))

    t_start = None
    for _ in range(300):
        try:
            for line in open(os.path.join(rundir, "lg_c1.log")):
                if line.startswith("start="):
                    t_start = float(line.split()[0].split("=")[1])
                    break
        except OSError:
            pass
        if t_start:
            break
        time.sleep(0.1)
    if t_start is None:
        log("경고: start= 를 못 읽음"); t_start = time.time() + 1.0
    t_meas = t_start + a.warmup

    def wait_until(rel):
        while time.time() < t_meas + rel:
            time.sleep(0.2)

    marks = {}
    wait_until(a.seg)
    t0 = time.time(); log(out(f"{STRESS} {a.target} start", 90)); marks["on"] = (t0, time.time())
    wait_until(2 * a.seg)
    t0 = time.time(); log(out(f"{STRESS} {a.target} stop", 90)); marks["off"] = (t0, time.time())

    for p in procs:
        p.wait(timeout=dur + a.warmup + 900)
    out(f"{STRESS} {a.target} stop", 90)
    log("부하 종료")

    off1 = int(out(f"ssh {ENVOY} 'stat -c %s {ENVOY_LOG}'", 60) or 0)
    sl = os.path.join(rundir, "envoy.log.gz")
    sh(f"ssh {ENVOY} \"tail -c +{off0+1} {ENVOY_LOG} | head -c {max(off1-off0,0)}\" | gzip -1 > {sl}", 900)
    rows = []
    for c in (1, 2):
        sh(f"scp -q {LOADGEN}:/var/tmp/s6_{tag}_c{c}.csv {rundir}/load_c{c}.csv", 900)
        sh(f"ssh {LOADGEN} 'rm -f /var/tmp/s6_{tag}_c{c}.csv'", 60)
        p = os.path.join(rundir, f"load_c{c}.csv")
        if os.path.exists(p):
            rows += [r for r in csv.DictReader(open(p)) if r["warmup"] == "0"]

    hm = load_hostmap(sl)
    GUARD = 2.0
    secs = {"pre": (t_meas, marks["on"][0] - GUARD),
            "during": (marks["on"][1] + GUARD, marks["off"][0] - GUARD),
            "post": (marks["off"][1] + GUARD, t_meas + dur)}

    res = {"tag": tag, "target": a.target, "policy": a.policy, "rps": a.rps,
           "cpu": a.cpu, "load": a.load, "sections": {}}
    for name, (lo, hi) in secs.items():
        sub = [r for r in rows if lo <= float(r["end_ts"]) < hi]
        ok = [r for r in sub if r["status"] == "200"
              and abs(int(r["bytes_recv"]) - EXPECT_BYTES[r["ep"]])
              <= (EXPECT_BYTES[r["ep"]] * 0.10 if EXPECT_BYTES[r["ep"]] > 1000 else 0)]
        n5xx = sum(1 for r in sub if r["status"].startswith("5"))
        fc, svc = {}, {}
        for r in ok:
            h = hm.get(r["request_id"])
            if not h or h[2] is None:
                continue
            st = SITES.get(h[0])
            if st:
                fc.setdefault(st, []).append(h[2] / 1000.0 - D_NET_MS[st])
        for r in ok:
            svc.setdefault("all", []).append(float(r["service_ms"]))
        dist = {}
        for r in sub:
            h = hm.get(r["request_id"])
            dist[SITES.get(h[0], "?") if h else "?"] = dist.get(SITES.get(h[0], "?") if h else "?", 0) + 1
        res["sections"][name] = {
            "n": len(sub), "n_ok": len(ok),
            "achieved_rps": round(len(ok) / max(hi - lo, 1e-9), 1),
            "pct_5xx": round(100.0 * n5xx / len(sub), 3) if sub else None,
            "site_dist": dist,
            "svc_p50": round(pctl(svc.get("all", []), .5), 3),
            "fc_p95": {s: round(pctl(v, .95), 3) for s, v in sorted(fc.items())},
            "fc_p50": {s: round(pctl(v, .5), 3) for s, v in sorted(fc.items())},
        }
    json.dump(res, open(os.path.join(rundir, "result.json"), "w"), ensure_ascii=False, indent=1)

    t = a.target
    pre, dur_, post = (res["sections"][k] for k in ("pre", "during", "post"))
    fp, fd, fq = pre["fc_p95"].get(t), dur_["fc_p95"].get(t), post["fc_p95"].get(t)
    rise = fd / fp if fp and fd else float("nan")
    back = post["svc_p50"] / pre["svc_p50"] if pre["svc_p50"] else float("nan")
    ach = dur_["achieved_rps"] / a.rps * 100
    log(f"결과 {tag}: f_c p95 {t} {fp} -> {fd} -> {fq}  상승={rise:.2f}x  "
        f"복귀(svc p50)={back:.2f}x  달성={ach:.1f}%  5xx={dur_['pct_5xx']}%")
    verdict = ("통과" if (2.0 <= rise <= 4.0 and ach >= 90 and (dur_["pct_5xx"] or 0) < 1.0
                          and back <= 1.2) else "미달/초과")
    log(f"판정: {verdict}")
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

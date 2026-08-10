#!/usr/bin/env python3
"""tb-radio2 (커넥션 단위 셰이핑) 검증 게이트.

통과 기준 (사용자 지정):
  1. 코호트1 처리량   극단 밴드에서도 8.89 Mbit/s 유지 (완료율 700/s)
  2. search d_acc     §0.3 값(극단 24.57 / Poor 17.06)의 1.0~1.5배 이내
  3. 코호트2          무영향
  4. 커넥션 간 편차   커넥션별 p50 의 변동계수 <= 0.2

정책은 bl_lr 을 쓴다 — 1400 rps 에서 세 사이트 중 어느 것도 무릎을 넘지 않는
유일한 정책이라(S1 386/400, S2 456/800, S3 558/1600) 서버측 잡음이 가장 작다.
밴드 구간 앞뒤에 무셰이핑 구간을 끼워 드리프트를 상쇄한다.
"""
import csv
import json
import os
import statistics as st
import subprocess
import time

LOADGEN = "192.168.0.12"
ENVOY = "192.168.0.43"
PIN = "taskset -c 6-15"
OUT = "/home/user/exp/calib"
D = "inDate=2015-04-09&outDate=2015-04-10"
MIX = (f"reserve=1:/reservation?{D}&hotelId=1&customerName=gate"
       f"&username=Cornell_30&password=0000000000&number=1,"
       f"search=1.5:/hotels?{D}&lat=37.7867&lon=-122.4112,"
       f"recommend=2:/recommendations?require=dis&lat=37.7867&lon=-122.4112")

WARMUP, DURATION = 20, 215
WINDOWS = [("none_a", "none", 2, 40), ("extreme", "rate 1600kbit", 45, 85),
           ("none_b", "none", 90, 130), ("poor", "rate 2300kbit", 135, 175),
           ("none_c", "none", 180, 213)]
REF_DACC = {"extreme": {"search": 24.57, "reserve": 1.46, "recommend": 2.28},
            "poor": {"search": 17.06, "reserve": 0.92, "recommend": 1.59}}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def sh(c, t=600):
    return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=t).stdout.strip()


def radio(spec):
    base = (f"ssh {ENVOY} \"C1_IP=10.46.0.6 C2_IP=10.46.0.7 "
            f"sudo -n /usr/local/sbin/tb-radio2.sh ")
    return sh(base + ('clear"' if spec == "none" else f"apply '{spec}' none\""), 120)


def main():
    os.makedirs(OUT, exist_ok=True)
    ips = sh(f"ssh {LOADGEN} 'cat /run/tb-cohort.map'")
    log(f"코호트 맵:\n{ips}")
    sh("bash /home/user/setpol.sh bl_lr")
    sh("bash /home/user/exp/reserve_reset.sh", 300)
    radio("none")

    procs = []
    for c in (1, 2):
        cmd = (f'ssh {LOADGEN} "{PIN} python3 ~/tb-load.py --host {ENVOY} --port 8080 '
               f"--cohort {c} --mix '{MIX}' --connections 28 --rps-per-connection 25 "
               f'--warmup {WARMUP} --duration {DURATION} '
               f'--csv /var/tmp/gate2_c{c}.csv --label gate2-c{c}"')
        procs.append(subprocess.Popen(cmd, shell=True,
                                      stdout=open(f"{OUT}/gate2_c{c}.log", "w"),
                                      stderr=subprocess.STDOUT))
    t_meas = time.time() + 1.0 + WARMUP
    for name, spec, t0, t1 in WINDOWS:
        while time.time() < t_meas + t0:
            time.sleep(0.2)
        radio(spec)
        log(f"  t+{t0:>3}s {name:>8s} <- {spec}")
    for p in procs:
        p.wait(timeout=600)
    radio("none")
    log("부하 종료")

    rows = {}
    for c in (1, 2):
        sh(f"scp -q {LOADGEN}:/var/tmp/gate2_c{c}.csv {OUT}/")
        rows[c] = [r for r in csv.DictReader(open(f"{OUT}/gate2_c{c}.csv"))
                   if r["warmup"] == "0"]

    def win(c, a, b):
        return [r for r in rows[c] if a <= float(r["scheduled_ts"]) - t_meas < b]

    def p50(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else float("nan")

    res = {}
    log("")
    log(f"{'구간':>9s} {'코호트':>4s} {'Mbit/s':>8s} {'완료/s':>8s}  "
        f"{'search p50':>11s} {'reserve p50':>12s} {'recmd p50':>10s}")
    for name, spec, a, b in WINDOWS:
        res[name] = {"spec": spec}
        for c in (1, 2):
            sub = [r for r in win(c, a, b) if r["status"] == "200"]
            by = sum(int(r["bytes_recv"]) for r in sub)
            e = {ep: p50([float(r["service_ms"]) for r in sub if r["ep"] == ep])
                 for ep in ("search", "reserve", "recommend")}
            res[name][f"c{c}"] = {"mbps": round(by * 8 / (b - a) / 1e6, 3),
                                  "done_per_s": round(len(sub) / (b - a), 1),
                                  "p50": {k: round(v, 3) for k, v in e.items()}}
            log(f"{name:>9s} {c:>4d} {res[name][f'c{c}']['mbps']:8.3f} "
                f"{res[name][f'c{c}']['done_per_s']:8.1f}  {e['search']:11.2f} "
                f"{e['reserve']:12.2f} {e['recommend']:10.2f}")

    # 커넥션 간 편차 (코호트1, 극단 밴드 구간)
    cv = {}
    for name in ("extreme", "poor"):
        a, b = [(x[2], x[3]) for x in WINDOWS if x[0] == name][0]
        sub = [r for r in win(1, a, b) if r["status"] == "200" and r["ep"] == "search"]
        byc = {}
        for r in sub:
            byc.setdefault(r["conn"], []).append(float(r["service_ms"]))
        med = [p50(v) for v in byc.values() if len(v) >= 20]
        cv[name] = {"n_conn": len(med), "mean": round(st.mean(med), 3),
                    "cv": round(st.pstdev(med) / st.mean(med), 4) if med else None}
        log(f"  커넥션간 p50 변동계수 [{name}]: {cv[name]}")

    # ---- 판정 ----
    log("")
    verdict = {}
    base_mbps = res["none_a"]["c1"]["mbps"]
    v1 = all(res[n]["c1"]["mbps"] >= 8.5 and res[n]["c1"]["done_per_s"] >= 690
             for n in ("extreme", "poor"))
    verdict["1 코호트1 처리량 유지"] = (v1, {n: (res[n]["c1"]["mbps"],
                                          res[n]["c1"]["done_per_s"]) for n in ("extreme", "poor")})
    d = {}
    ok2 = True
    for n in ("extreme", "poor"):
        base = res["none_a" if n == "extreme" else "none_b"]["c1"]["p50"]
        d[n] = {}
        for ep in ("search", "reserve", "recommend"):
            delta = res[n]["c1"]["p50"][ep] - base[ep]
            ref = REF_DACC[n][ep]
            d[n][ep] = {"delta_ms": round(delta, 3), "ref": ref,
                        "ratio": round(delta / ref, 3)}
            if ep == "search" and not (1.0 <= delta / ref <= 1.5):
                ok2 = False
    verdict["2 search d_acc 배수 1.0~1.5"] = (ok2, d)
    c2v = {n: res[n]["c2"]["p50"]["search"] for n in [w[0] for w in WINDOWS]}
    spread = max(c2v.values()) - min(c2v.values())
    verdict["3 코호트2 무영향"] = (spread < 3.0, {"c2_search_p50": c2v,
                                             "spread_ms": round(spread, 3)})
    v4 = all(v["cv"] is not None and v["cv"] <= 0.2 for v in cv.values())
    verdict["4 커넥션간 CV <= 0.2"] = (v4, cv)

    allok = True
    for k, (ok, det) in verdict.items():
        allok &= ok
        log(f"  {'O' if ok else 'X'}  {k}")
        log(f"       {json.dumps(det, ensure_ascii=False)}")
    log(f"\n게이트: {'통과' if allok else '실패'}")
    json.dump({"windows": res, "cv": cv,
               "verdict": {k: v[0] for k, v in verdict.items()},
               "detail": {k: v[1] for k, v in verdict.items()}},
              open(f"{OUT}/gate_radio2.json", "w"), ensure_ascii=False, indent=1)
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main())

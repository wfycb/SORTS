#!/usr/bin/env python3
"""개정 A §3.3 — 800 rps 에서 셰이핑 4항목 1회 재확인 (게이트 재실행 아님).

커넥션당 하향 요구량은 도착률에 무관하다:
    (8.894 Mbit/s x r/700) / (r/25) = 0.318 Mbit/s
총 요구량과 커넥션 수가 모두 r 에 비례해 상쇄되므로 밴드별 rho 가 불변이고
§0.1 게이트 결과가 그대로 이전된다. 그래도 실제로 그런지 한 번은 본다.

정책은 site_s3 로 고정한다 (v6 §0.2 의 교훈 — 밴드 비교는 다른 변수를 고정).
"""
import csv
import json
import statistics as st
import subprocess
import time

LOADGEN, ENVOY, PIN = "192.168.0.12", "192.168.0.43", "taskset -c 6-15"
OUT = "/home/user/exp/calib"
D = "inDate=2015-04-09&outDate=2015-04-10"
MIX = (f"reserve=1:/reservation?{D}&hotelId=1&customerName=rechk"
       f"&username=Cornell_30&password=0000000000&number=1,"
       f"search=1.5:/hotels?{D}&lat=37.7867&lon=-122.4112,"
       f"recommend=2:/recommendations?require=dis&lat=37.7867&lon=-122.4112")
CONNS, RPS_PER, WARMUP, DUR = 16, 25, 20, 150
# 극단 밴드를 t=48 에 걸고 t=108 에 푼다. 창은 경계 ±2s 를 피해서 잡는다.
WINDOWS = [("none_a", "none", 5, 45), ("extreme", "rate 1600kbit", 52, 105),
           ("none_b", "none", 112, 148)]
APPLY_AT = {"extreme": 48, "none_b": 108}
REF_DACC = 24.57          # §0.3 극단 밴드 search
EXPECT_MBPS = 8.894 * 400 / 700     # 도착률 비례 = 5.082
EXPECT_DONE = 400.0


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def sh(c, t=900):
    return subprocess.run(c, shell=True, capture_output=True, text=True,
                          timeout=t).stdout.strip()


def radio(spec):
    b = (f"ssh {ENVOY} \"C1_IP=10.46.0.6 C2_IP=10.46.0.7 "
         f"sudo -n /usr/local/sbin/tb-radio2.sh ")
    return sh(b + ('clear"' if spec == "none" else f"apply '{spec}' none\""), 120)


def p50(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else float("nan")


def main():
    sh("bash /home/user/setpol.sh site_s3")
    sh("bash /home/user/exp/reserve_reset.sh", 300)
    radio("none")
    procs = []
    for c in (1, 2):
        cmd = (f'ssh {LOADGEN} "{PIN} python3 ~/tb-load.py --host {ENVOY} --port 8080 '
               f"--cohort {c} --mix '{MIX}' --connections {CONNS} "
               f"--rps-per-connection {RPS_PER} --warmup {WARMUP} --duration {DUR} "
               f'--csv /var/tmp/rechk_c{c}.csv --label rechk-c{c}"')
        procs.append(subprocess.Popen(cmd, shell=True,
                                      stdout=open(f"{OUT}/rechk_c{c}.log", "w"),
                                      stderr=subprocess.STDOUT))
    t_meas = time.time() + 1.0 + WARMUP
    for name, at in sorted(APPLY_AT.items(), key=lambda x: x[1]):
        while time.time() < t_meas + at:
            time.sleep(0.2)
        spec = dict(WINDOWS_SPEC)[name]
        radio(spec)
        log(f"  t+{at}s {name} <- {spec}")
    for p in procs:
        p.wait(timeout=900)
    radio("none")
    log("부하 종료")

    rows = {}
    for c in (1, 2):
        sh(f"scp -q {LOADGEN}:/var/tmp/rechk_c{c}.csv {OUT}/")
        rows[c] = [r for r in csv.DictReader(open(f"{OUT}/rechk_c{c}.csv"))
                   if r["warmup"] == "0" and r["status"] == "200"]

    def win(c, a, b):
        return [r for r in rows[c] if a <= float(r["end_ts"]) - t_meas < b]

    res = {}
    log("")
    log(f"{'구간':>9s} {'코호트':>4s} {'Mbit/s':>8s} {'완료/s':>8s} {'search p50':>11s}")
    for name, spec, a, b in WINDOWS:
        res[name] = {"spec": spec}
        for c in (1, 2):
            sub = win(c, a, b)
            by = sum(int(r["bytes_recv"]) for r in sub)
            res[name][f"c{c}"] = {
                "mbps": round(by * 8 / (b - a) / 1e6, 3),
                "done_per_s": round(len(sub) / (b - a), 1),
                "search_p50": round(p50([float(r["service_ms"]) for r in sub
                                         if r["ep"] == "search"]), 3)}
            v = res[name][f"c{c}"]
            log(f"{name:>9s} {c:>4d} {v['mbps']:8.3f} {v['done_per_s']:8.1f} "
                f"{v['search_p50']:11.2f}")

    base = (res["none_a"]["c1"]["search_p50"] + res["none_b"]["c1"]["search_p50"]) / 2
    d_acc = res["extreme"]["c1"]["search_p50"] - base
    sub = [r for r in win(1, 52, 105) if r["ep"] == "search"]
    byc = {}
    for r in sub:
        byc.setdefault(r["conn"], []).append(float(r["service_ms"]))
    med = [p50(v) for v in byc.values() if len(v) >= 20]
    cv = st.pstdev(med) / st.mean(med) if med else None
    c2 = [res[n]["c2"]["search_p50"] for n in ("none_a", "extreme", "none_b")]
    spread = max(c2) - min(c2)

    v = {}
    e = res["extreme"]["c1"]
    v["1 코호트1 처리량"] = (e["mbps"] >= EXPECT_MBPS * 0.97
                        and e["done_per_s"] >= EXPECT_DONE * 0.97,
                        f"{e['mbps']} Mbit/s (기대 {EXPECT_MBPS:.3f}) / "
                        f"{e['done_per_s']}/s (기대 {EXPECT_DONE})")
    v["2 search d_acc 0.9~1.5x"] = (0.9 <= d_acc / REF_DACC <= 1.5,
                                    f"Δ={d_acc:.2f} ref={REF_DACC} 배수={d_acc / REF_DACC:.3f}")
    v["3 코호트2 변화 <=1ms"] = (spread <= 1.0, f"c2 search p50 {c2} spread={spread:.3f} ms")
    v["4 커넥션간 CV <=0.2"] = (cv is not None and cv <= 0.2,
                             f"n_conn={len(med)} CV={cv:.4f}" if cv else "NA")
    log("")
    allok = True
    for k, (ok, det) in v.items():
        allok &= ok
        log(f"  {'O' if ok else 'X'}  {k}: {det}")
    log(f"\n재확인: {'통과' if allok else '실패'}")
    json.dump({"windows": res, "d_acc": d_acc, "ratio": d_acc / REF_DACC,
               "cv": cv, "c2_spread": spread,
               "verdict": {k: x[0] for k, x in v.items()},
               "detail": {k: x[1] for k, x in v.items()}},
              open(f"{OUT}/recheck_radio800.json", "w"), ensure_ascii=False, indent=1)
    return 0 if allok else 1


WINDOWS_SPEC = [(n, s) for n, s, _, _ in WINDOWS]

if __name__ == "__main__":
    raise SystemExit(main())

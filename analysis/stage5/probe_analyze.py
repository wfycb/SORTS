#!/usr/bin/env python3
"""STAGE5 S5-0 §2.2.3 프로브 분석 — 부하 생성 성립 여부.

런별: 요청 rps vs 달성 rps(창별), 커넥션당 요청/달성, 응답 코드, corrected−service
갭(스케줄 밀림 = 커넥션당 1 outstanding 모형의 포화 신호), 사이트 분배.
호스트: .12 CPU 사용률(생성기 무능 판정), S1 NIC 처리량(100 Mb/s 포화 판정).
"""
import csv
import json
import os
import sys

RUNS = "/home/user/exp/runs/stage5-probe-20260812"
SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}


def pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[int(round(q * (len(xs) - 1)))], 1)


def load_window(rd, ncoh, lo, hi):
    """load_c*.csv 에서 [lo,hi] 완료 구간 통계."""
    n = viol = err = 0
    svc, cor = [], []
    per_conn = {}
    for c in range(1, ncoh + 1):
        p = os.path.join(rd, f"load_c{c}.csv")
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(open(p)):
            if r["warmup"] != "0":
                continue
            t = float(r["end_ts"])
            if not (lo <= t <= hi):
                continue
            n += 1
            if r["status"] != "200":
                err += 1
                continue
            s = float(r["service_ms"])
            k = float(r["corrected_ms"])
            svc.append(s)
            cor.append(k)
            viol += k > SLO[r["ep"]]
            per_conn[(c, r["conn"])] = per_conn.get((c, r["conn"]), 0) + 1
    dur = hi - lo
    return {"n": n, "err": err, "rps": round(n / dur, 1) if dur > 0 else None,
            "viol_pct": round(100 * viol / n, 3) if n else None,
            "svc_p50": pctl(svc, .5), "svc_p95": pctl(svc, .95),
            "cor_p50": pctl(cor, .5), "cor_p95": pctl(cor, .95),
            "lag_p50": (round(pctl(cor, .5) - pctl(svc, .5), 1) if svc else None),
            "conn_rps_p50": (pctl([v / dur for v in per_conn.values()], .5)
                             if per_conn else None),
            "n_conn": len(per_conn)}


def host_samples(path, want_net=False):
    """샘플러 파일 -> (cpu 사용률 최대/중앙, NIC Mb/s 최대) — 구간 전체."""
    rows = []
    for line in open(path, errors="replace"):
        p = line.split()
        if len(p) < 9 or p[1] != "cpu":
            continue
        ts = int(p[0])
        vals = [int(x) for x in p[2:11] if x.lstrip("-").isdigit()]
        net = {}
        for tok in p:
            if "=" in tok:
                k, v = tok.split("=", 1)
                if v.isdigit():
                    net[k] = int(v)
        rows.append((ts, vals, net))
    use, mbps = [], []
    for (t0, v0, n0), (t1, v1, n1) in zip(rows, rows[1:]):
        d = [b - a for a, b in zip(v0, v1)]
        tot = sum(d)
        idle = d[3] + (d[4] if len(d) > 4 else 0)
        if tot > 0:
            use.append((t1, 100.0 * (tot - idle) / tot))
        if want_net and n0 and n1 and t1 > t0:
            dt = t1 - t0
            mbps.append((t1, 8e-6 * ((n1.get("tx_bytes", 0) - n0.get("tx_bytes", 0))
                                     + (n1.get("rx_bytes", 0) - n0.get("rx_bytes", 0))) / dt))
    return use, mbps


def main():
    prog = json.load(open(os.path.join(RUNS, "progress.json")))
    cpu12, _ = host_samples(os.path.join(RUNS, "sample_loadgen.txt"))
    cpu1, net1 = host_samples(os.path.join(RUNS, "sample_s1.txt"), want_net=True)
    out = []
    print(f"{'run':13s}{'req':>6s}{'conn':>5s}{'req/c':>7s}{'창':>7s}"
          f"{'달성':>8s}{'달성률':>7s}{'c/rps':>7s}{'svc50':>7s}{'cor50':>7s}"
          f"{'밀림':>7s}{'err':>5s}{'.12CPU':>7s}{'S1 Mb/s':>8s}")
    for rid in prog["runs"]:
        rd = os.path.join(RUNS, rid)
        if not os.path.exists(os.path.join(rd, "meta.json")):
            continue
        meta = json.load(open(os.path.join(rd, "meta.json")))
        ncoh = 2
        req = meta["total_rps"]
        conn = meta["connections"]
        secs = meta["sections_abs_12"]
        for wname in ("pre", "during"):
            lo, hi = secs[wname]
            st = load_window(rd, ncoh, lo, hi)
            c12 = [u for t, u in cpu12 if lo - 0.1 <= t <= hi + 0.1]
            s1n = [m for t, m in net1 if lo - 0.1 <= t <= hi + 0.1]
            rec = dict(run=rid, req=req, conn=conn, win=wname, **st,
                       cpu12_max=round(max(c12), 1) if c12 else None,
                       s1_mbps_max=round(max(s1n), 1) if s1n else None)
            out.append(rec)
            print(f"{rid:13s}{req:>6d}{conn:>5d}{req/(2*conn):>7.2f}{wname:>7s}"
                  f"{str(st['rps']):>8s}{100*st['rps']/req:>6.1f}%"
                  f"{str(st['conn_rps_p50']):>7s}{str(st['svc_p50']):>7s}"
                  f"{str(st['cor_p50']):>7s}{str(st['lag_p50']):>7s}"
                  f"{st['err']:>5d}{str(rec['cpu12_max']):>7s}"
                  f"{str(rec['s1_mbps_max']):>8s}")
    json.dump(out, open(os.path.join(RUNS, "probe_results.json"), "w"),
              ensure_ascii=False, indent=1)
    print(f"\n-> {RUNS}/probe_results.json")


if __name__ == "__main__":
    sys.exit(main())

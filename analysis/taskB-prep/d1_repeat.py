#!/usr/bin/env python3
"""과제 B: D1 pre 반복 분석 — 위반율 + 기전 3성분 분리.

런 형식: disturb=none, 본측정 전체가 관심 구간 (warmup 행 제외 전부).
성분 정의 (PROGRESS.md 사전 등록):
  진동   — decisions.csv 유닛(cohort×class)별 chosen_site 전환 수, 사이트 체류
           비율, feasible_set 전환 수. (D1 pre 실측 630/651 틱과 대조)
  HOL    — 클래스별 service_ms 위반율 vs corrected_ms 위반율 (갭 = HOL 몫)
  S2과부하 — 초당 S2 유입 > 1000rps(무릎) 인 초의 비율, 그 구간 search f_c p95

사용: python3 d1_repeat.py <rundir> ... --json out.json
"""
import argparse
import csv
import gzip
import json
import os
from collections import defaultdict

SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
GB = 5.0
D_NET = {"S1": 2.0, "S2": 15.0, "S3": 25.006}
EXPECT_BYTES = {"reserve": 36, "search": 4474, "recommend": 200}
SITE_OF_IP = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
S2_KNEE = 1000.0


def pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[int(round(q * (len(xs) - 1)))], 3)


def is_valid(r):
    if r["status"] != "200":
        return False
    e = EXPECT_BYTES.get(r["ep"])
    b = int(r["bytes_recv"])
    return e is not None and abs(b - e) <= (e * 0.10 if e > 1000 else 0)


def one_run(rd):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    arm = (meta["arm"]["effective"]["subset_policy"]
           if meta["policy"] == "sorts_reactive" else meta["policy"])
    hm = {}
    with gzip.open(os.path.join(rd, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) > 17 and p[17].strip().isdigit():
                hm[p[1]] = (p[10].split(":")[0], float(p[0]), int(p[17]))

    tot = viol = 0
    by = {}                       # class -> [n, viol_cor, viol_svc]
    per_conn = {}
    for c in (1, 2):
        for r in csv.DictReader(open(os.path.join(rd, f"load_c{c}.csv"))):
            if r["warmup"] != "0":
                continue
            ok = is_valid(r)
            bad_cor = (not ok) or float(r["corrected_ms"]) > SLO[r["ep"]]
            bad_svc = (not ok) or float(r["service_ms"]) > SLO[r["ep"]]
            tot += 1
            viol += bad_cor
            d = by.setdefault(r["ep"], [0, 0, 0])
            d[0] += 1; d[1] += bad_cor; d[2] += bad_svc
            pc = per_conn.setdefault((c, int(r["conn"])), [0, 0])
            pc[0] += 1; pc[1] += bad_cor

    # --- S2 과부하 성분 (envoy 시계 1초 버킷) ---
    sec = defaultdict(lambda: defaultdict(int))       # sec -> site -> n
    fc_s2_search = defaultdict(list)                  # sec -> [fc...]
    for rid, (ip, ts, us) in hm.items():
        s = SITE_OF_IP.get(ip)
        if s is None:
            continue
        t = int(ts)
        sec[t][s] += 1
        if s == "S2":
            fc_s2_search[t].append(us / 1000.0 - D_NET["S2"])
    secs = sorted(sec)
    if len(secs) > 20:                                # 가장자리 부분초 제거
        secs = secs[2:-2]
    over = [t for t in secs if sec[t]["S2"] > S2_KNEE]
    fc_over = [v for t in over for v in fc_s2_search.get(t, [])]
    fc_under = [v for t in secs if t not in set(over)
                for v in fc_s2_search.get(t, [])]
    s2_comp = {"n_secs": len(secs), "n_secs_over_knee": len(over),
               "share_over": round(len(over) / len(secs), 3) if secs else None,
               "s2_inflow_mean": round(sum(sec[t]["S2"] for t in secs) / len(secs), 1)
               if secs else None,
               "s2_inflow_max": max((sec[t]["S2"] for t in secs), default=0),
               "fc_p95_over_knee": pctl(fc_over, .95),
               "fc_p95_under_knee": pctl(fc_under, .95)}

    # --- 진동 성분 (decisions.csv) ---
    osc = {}
    dp = os.path.join(rd, "decisions.csv")
    if os.path.exists(dp):
        units = defaultdict(list)
        for r in csv.DictReader(open(dp)):
            units[(r["cohort"], r["class"])].append(
                (float(r["ts"]), r["chosen_site"], r["feasible_set"]))
        for u, rows in sorted(units.items()):
            rows.sort()
            trans = sum(1 for i in range(1, len(rows))
                        if rows[i][1] != rows[i - 1][1])
            ftrans = sum(1 for i in range(1, len(rows))
                         if rows[i][2] != rows[i - 1][2])
            dwell = defaultdict(int)
            for _, site, _ in rows:
                dwell[site] += 1
            n = len(rows)
            osc["_".join(u)] = {
                "ticks": n, "site_transitions": trans,
                "feasible_transitions": ftrans,
                "dwell": {s: round(k / n, 3) for s, k in sorted(dwell.items())}}

    conn_rates = sorted(100 * v / n for (c, _), (n, v) in per_conn.items() if n)
    dur = (max(secs) - min(secs) + 1) if secs else None
    return {
        "run_id": meta["run_id"], "arm": arm,
        "suspect": os.path.exists(os.path.join(rd, "SUSPECT")),
        "n": tot, "viol_pct": round(100 * viol / tot, 3) if tot else None,
        "by_class": {ep: {"n": d[0],
                          "viol_corrected_pct": round(100 * d[1] / d[0], 3),
                          "viol_service_pct": round(100 * d[2] / d[0], 3),
                          "hol_gap_pct": round(100 * (d[1] - d[2]) / d[0], 3)}
                     for ep, d in sorted(by.items())},
        "per_conn_viol_pct": {"n_conns": len(conn_rates),
                              "min": round(conn_rates[0], 2) if conn_rates else None,
                              "p50": pctl(conn_rates, .5),
                              "max": round(conn_rates[-1], 2) if conn_rates else None},
        "s2_overload": s2_comp,
        "oscillation": osc,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rundirs", nargs="+")
    ap.add_argument("--json", required=True)
    a = ap.parse_args()
    runs = [one_run(rd) for rd in a.rundirs
            if os.path.exists(os.path.join(rd, "DONE"))]
    arms = {}
    for r in runs:
        if not r["suspect"]:
            arms.setdefault(r["arm"], []).append(r["viol_pct"])
    agg = {arm: {"n": len(v), "runs": v, "mean": round(sum(v) / len(v), 3),
                 "half_range": round((max(v) - min(v)) / 2, 3)}
           for arm, v in arms.items()}
    crit1 = None
    if {"far_tier", "strict_far"} <= set(agg):
        diff = abs(agg["far_tier"]["mean"] - agg["strict_far"]["mean"])
        hr = agg["far_tier"]["half_range"] + agg["strict_far"]["half_range"]
        crit1 = {"mean_diff": round(diff, 3), "half_range_sum": round(hr, 3),
                 "pass": diff > hr}
    json.dump({"runs": runs, "arm_agg": agg, "crit1": crit1},
              open(a.json, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"arm_agg": agg, "crit1": crit1}, ensure_ascii=False, indent=1))
    for r in runs:
        so = r["s2_overload"]
        tr = sum(u["site_transitions"] for u in r["oscillation"].values())
        print(f"{r['run_id']:>11s} {r['arm']:>10s} viol={r['viol_pct']:7.3f}% "
              f"전환합={tr:4d} S2>무릎 {so['share_over']} "
              f"conn p50/max={r['per_conn_viol_pct']['p50']}/{r['per_conn_viol_pct']['max']}")


if __name__ == "__main__":
    main()

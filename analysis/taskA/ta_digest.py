#!/usr/bin/env python3
"""작업 A 종합 다이제스트: 11런 산출물 -> 표 재료.

런별로 뽑는 것:
  [summary.json]  during 위반율(전체/c1/c2/search), 달성 rps, 분배(pre/during)
  [decisions.csv] during 창(.43, marks t43 기준) 집합 전환·포함률·EXPECTANT
  [obs_state.csv] I-6: during 창 S2/S3 search 셀 src=obs 비율, p95 진폭
  [access log]    순도: sub_* 행의 필드11 이 허용 집합 밖인 건수 (0 이어야)
  [load_c1.csv]   radio 런 커넥션별 위반율 (I-8 복권 확인)
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import sys
from collections import Counter

EXP = "/home/user/exp"
RUNS = os.path.join(EXP, "runs/taskA-20260809")
EK = json.load(open(os.path.join(EXP, "envoy_keys.json")))
SCS = {c: tuple(EK["cluster_sites"][c]) for c in EK["sorts_clusters"]}
IP_SITE = {ip: s for s, ip in EK["site_ip"].items()}
GUARD = 2.0


def dur_window_43(m):
    """교란 창 (.43 시계). seq_extreme 은 (c1단독, 양코호트) 두 창."""
    d43 = m["clock"]["d43_s"]
    mk = {x["what"]: x for x in m["marks"]}
    if m["disturb"] == "seq_extreme":
        return {"c1only": (mk["c1_extreme"]["t43_done"] + GUARD,
                           mk["c2_extreme"]["t_issue"] + d43 - GUARD),
                "both": (mk["c2_extreme"]["t43_done"] + GUARD,
                         mk["clear_all"]["t_issue"] + d43 - GUARD)}
    st = next(x for x in m["marks"] if x["phase"] == "start")
    en = next(x for x in reversed(m["marks"]) if x["phase"] == "end")
    return {"during": (st["t43_done"] + GUARD, en["t_issue"] + d43 - GUARD)}


def set_metrics(dec_rows, lo, hi):
    u = {}
    for r in dec_rows:
        ts = float(r["ts"])
        if lo <= ts <= hi:
            u.setdefault(f"{r['cohort']}_{r['class']}", []).append(r)
    out = {}
    for unit, seq in sorted(u.items()):
        n = len(seq)
        fs = [tuple(x for x in r["feasible_set"].split("|") if x) for r in seq]
        out[unit] = {
            "n": n,
            "trans": sum(1 for i in range(1, n) if fs[i] != fs[i - 1]),
            "s3_pct": round(100 * sum(1 for m in fs if "S3" in m) / n, 1),
            "s2_pct": round(100 * sum(1 for m in fs if "S2" in m) / n, 1),
            "s1_pct": round(100 * sum(1 for m in fs if "S1" in m) / n, 1),
            "exp_pct": round(100 * sum(1 for r in seq
                                       if r["expectant"] == "1") / n, 1),
            "dist": dict(Counter(r["feasible_set"] for r in seq)),
        }
    return out


def fc_cells(obs_rows, lo, hi):
    """during 창 S2/S3 search 셀: src 분포 + value 진폭 (I-6/진동)."""
    out = {}
    for site in ("S2", "S3"):
        rows = [r for r in obs_rows if r["site"] == site and r["class"] == "search"
                and lo <= float(r["ts"]) <= hi]
        if not rows:
            continue
        vals = [float(r["value_ms"]) for r in rows]
        out[site] = {"n": len(rows),
                     "src": dict(Counter(r["src"] for r in rows)),
                     "value_min": round(min(vals), 2),
                     "value_max": round(max(vals), 2)}
    return out


def purity(rundir):
    n_sub, n_leak = 0, 0
    p = os.path.join(rundir, "envoy_access.log.gz")
    with gzip.open(p, "rt", errors="replace") as f:
        for line in f:
            fl = line.rstrip("\n").split(",")
            if len(fl) < 18:
                continue
            sites = SCS.get(fl[9])
            if sites is None or len(sites) == 1:
                continue
            n_sub += 1
            if IP_SITE.get(fl[10].split(":")[0]) not in sites:
                n_leak += 1
    return n_sub, n_leak


def conn_viol(rundir, slo={"reserve": 35.0, "search": 45.0, "recommend": 35.0}):
    """커넥션별 위반율 (본측정 창, warmup 제외) — I-8 복권 확인."""
    c = {}
    with open(os.path.join(rundir, "load_c1.csv")) as f:
        for r in csv.DictReader(f):
            if r["warmup"] == "1":
                continue
            k = int(r["conn"])
            v = (r["status"] != "200"
                 or float(r["corrected_ms"]) > slo[r["ep"]])
            a = c.setdefault(k, [0, 0])
            a[0] += 1
            a[1] += v
    return sorted(round(100 * b / a, 1) for a, b in c.values())


def main():
    digest = {}
    for rid in sorted(os.listdir(RUNS)):
        rd = os.path.join(RUNS, rid)
        if not os.path.isdir(rd):
            continue
        s = json.load(open(os.path.join(rd, "summary.json")))
        d = {}
        for sec, v in s["sections"].items():
            tot_n = v["n"]
            viol = sum(e["slo_violation_rate"] * e["n"]
                       for e in v["by_endpoint"].values()) / tot_n
            d[sec] = {
                "viol_all": round(viol, 5),
                "viol_c1": v["by_cohort"]["1"]["slo_violation_rate"],
                "viol_c2": v["by_cohort"]["2"]["slo_violation_rate"],
                "viol_search": v["by_endpoint"]["search"]["slo_violation_rate"],
                "p99_search": v["by_endpoint"]["search"].get("corrected_p99"),
                "rps": v["achieved_rps"],
                "share": {k: round(x, 3) for k, x in v["site_share"].items()},
            }
        entry = {"sections": d, "join": s.get("join_rate")}
        dec_p = os.path.join(rd, "decisions.csv")
        if os.path.exists(dec_p):
            m = json.load(open(os.path.join(rd, "marks.json")))
            dec = list(csv.DictReader(open(dec_p)))
            obs = list(csv.DictReader(open(os.path.join(rd, "obs_state.csv"))))
            wins = dur_window_43(m)
            entry["windows"] = {}
            for wname, (lo, hi) in wins.items():
                entry["windows"][wname] = {
                    "sets": set_metrics(dec, lo, hi),
                    "fc": fc_cells(obs, lo, hi),
                }
            entry["purity"] = purity(rd)
        if "radio" in rid or "edge" in rid:
            entry["conn_viol_c1"] = conn_viol(rd)
        digest[rid] = entry
    out = os.path.join(EXP, "analysis/taskA/ta_digest.json")
    json.dump(digest, open(out, "w"), ensure_ascii=False, indent=1)
    print("->", out)
    for rid, e in digest.items():
        dd = e["sections"].get("during", {})
        print(f"{rid:26s} during viol {dd.get('viol_all')} "
              f"(c1 {dd.get('viol_c1')}) share {dd.get('share')} "
              f"purity {e.get('purity', '-')}")


if __name__ == "__main__":
    main()

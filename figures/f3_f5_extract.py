#!/usr/bin/env python3
"""F3(누적 분해)·F5(관측 격차) 입력 CSV 추출 — **대장의 산식을 재사용**한다.

F3: docs/NUMBERS.md §4 의 네 단계를 `t2_policy_repeat.one_run`(both 창 총
    위반율) 로 **재계산**해서 뽑는다. 값을 손으로 옮겨 적지 않는다.
F5: analysis/envoy_blindness/ 의 두 재현 스크립트 산출(result.json,
    result_1000to1.json)을 그대로 읽는다.
"""
import csv
import json
import os
import sys

EXP = "/home/user/exp"
sys.path.insert(0, os.path.join(EXP, "analysis/night-20260810"))
import t2_policy_repeat as t2  # noqa: E402

OUT = os.path.join(EXP, "figures/data")
# (라벨, 켜진 층, 런 목록) — docs/NUMBERS.md §4
STAGES = [
    ("strict_far", "strict_far", ["runs/taskB-20260810/v1/v1_strictoff"]),
    ("+ far_tier\n+ capacity", "far_tier + capacity",
     [f"runs/taskB2-20260810/v1s/v1s_off_{i}" for i in (1, 2, 3)]),
    ("+ soft\nassignment", "far_tier + capacity + soft",
     [f"runs/taskB2-20260810/v1s/v1s_on_{i}" for i in (1, 2, 3)]),
    ("+ C_eff\n(band-aware)", "far_tier + capacity + soft + C_eff",
     [f"runs/taskB3-20260810/v1/v1c_on_{i}" for i in (1, 2, 3)]),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for label, layers, runs in STAGES:
        vs = []
        for r in runs:
            res = t2.one_run(os.path.join(EXP, r))
            vs.append(res["windows"]["both"]["viol_pct"])
        mean = sum(vs) / len(vs)
        half = (max(vs) - min(vs)) / 2 if len(vs) > 1 else 0.0
        rows.append({"label": label.replace("\n", " "), "label_wrapped": label,
                     "layers": layers, "n": len(vs),
                     "viol_pct": round(mean, 3), "half_range": round(half, 3),
                     "runs": ";".join(os.path.basename(r) for r in runs),
                     "values": ";".join(f"{v:.3f}" for v in vs)})
        print(f"  {label.replace(chr(10),' '):26s} n={len(vs)} "
              f"{mean:7.3f} ± {half:.3f}   {rows[-1]['values']}")
    with open(os.path.join(OUT, "f3_cumulative.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"-> {OUT}/f3_cumulative.csv")

    eb = os.path.join(EXP, "analysis/envoy_blindness")
    r46 = json.load(open(os.path.join(eb, "result.json")))["R1_rr_radio"]["c1_search"]
    r1k = json.load(open(os.path.join(eb, "result_1000to1.json")))["delta"]
    f5 = [
        {"case": "A. under load (phase4 R1_rr_radio)",
         "condition": "c1 search, 2300k on one cohort, RR fixed, 800 rps, mean delta pre->during",
         "access_side_ms": round(r46["d_corrected_ms"], 3),
         "envoy_observed_ms": round(r46["d_envoy_upstream_ms"], 4),
         "ratio": round(r46["ratio_cor_over_us"], 1)},
        {"case": "B. static sweep (N2 calibration)",
         "condition": "search only, 20 Mbps -> 1.6 Mbps, p50 (not a load test)",
         "access_side_ms": round(r1k["e2e"], 3),
         "envoy_observed_ms": round(r1k["envoy"], 4),
         "ratio": round(r1k["ratio"], 1)},
    ]
    with open(os.path.join(OUT, "f5_blindness.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(f5[0]))
        w.writeheader()
        w.writerows(f5)
    for r in f5:
        print(f"  {r['case']:36s} {r['access_side_ms']:8.3f} ms vs "
              f"{r['envoy_observed_ms']:8.4f} ms  = {r['ratio']}:1")
    print(f"-> {OUT}/f5_blindness.csv")


if __name__ == "__main__":
    main()

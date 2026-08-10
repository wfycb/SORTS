#!/usr/bin/env python3
"""Phase 4 §3: 경계 민감도 분석.

(1) RR c1 search 의 during e2e 분포 vs SLO 45 — demo 와 오늘 겹쳐 표로.
(2) 민감도 곡선: e2e 를 −2~+2 ms 이동시킬 때 RR/LR/SORTS 의 c1 search
    during 위반율 변화. "누구의 숫자가 견고한가"가 목적.

분포는 load_c1.csv 의 corrected_ms(러너 위반 판정과 동일 관례), during
절단은 meta.json sections_abs_12. demo 수치는 공유기 교체 전 값(I-8).
"""
from __future__ import annotations

import csv
import json
import os
import sys

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "runs")
SLO_SEARCH = 45.0
SHIFTS = [x / 4.0 for x in range(-8, 9)]          # -2.0 ~ +2.0, 0.25 간격


def c1_search_during(rundir):
    meta = json.load(open(os.path.join(rundir, "meta.json")))
    lo, hi = meta["sections_abs_12"]["during"]
    xs = []
    with open(os.path.join(rundir, "load_c1.csv")) as f:
        for r in csv.DictReader(f):
            if (r["warmup"] == "0" and r["ep"] == "search"
                    and lo <= float(r["end_ts"]) < hi):
                # 위반 회계 관례: 비-200 은 무조건 위반. 여기서는 지연 분포가
                # 목적이므로 200 만 쓰되 비-200 개수를 따로 센다.
                if r["status"] == "200":
                    xs.append(float(r["corrected_ms"]))
    return sorted(xs)


def q(xs, p):
    return xs[max(0, min(len(xs) - 1, round(p * (len(xs) - 1))))]


def viol_pct(xs, shift=0.0):
    return 100.0 * sum(1 for x in xs if x + shift > SLO_SEARCH) / len(xs)


def main():
    sets = {
        "RR(오늘)": "phase4-20260807/R1_rr_radio",
        "RR(demo·교체전)": "demo-20260805/D4_rr_radio",
        "LR(오늘)": "phase4-20260807/R2_lr_radio",
        "LR(demo·교체전)": "demo-20260805/D5_lr_radio",
        "SORTS-const(오늘)": "phase4-20260807/A1_const_radio",
        "SORTS-const(demo·교체전)": "demo-20260805/D2_sorts_radio",
        "SORTS-both(오늘)": "phase4-20260807/A2_both_radio",
    }
    data = {}
    for name, rel in sets.items():
        d = os.path.join(BASE, rel)
        if os.path.exists(os.path.join(d, "meta.json")):
            data[name] = c1_search_during(d)
        else:
            print(f"(스킵: {rel} 없음)")

    print("\n## (1) c1 search during e2e 분포 vs SLO 45")
    print("| 계열 | n | p50 | p90 | p95 | p99 | max | P(>45)=위반% | P(>44) | P(>46) |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for name, xs in data.items():
        print(f"| {name} | {len(xs)} | {q(xs,.5):.2f} | {q(xs,.9):.2f} | "
              f"{q(xs,.95):.2f} | {q(xs,.99):.2f} | {xs[-1]:.1f} | "
              f"{viol_pct(xs):.2f} | {viol_pct(xs, 1.0):.2f} | {viol_pct(xs, -1.0):.2f} |")

    print("\n## (2) 민감도 곡선 — e2e 이동량 Δ[ms] 별 위반율[%]")
    hdr = "| Δms | " + " | ".join(data.keys()) + " |"
    print(hdr)
    print("|" + "---|" * (len(data) + 1))
    curves = {n: {} for n in data}
    for s in SHIFTS:
        row = [f"| {s:+.2f} "]
        for n, xs in data.items():
            v = viol_pct(xs, s)
            curves[n][s] = round(v, 3)
            row.append(f"| {v:6.2f} ")
        print("".join(row) + "|")

    # 견고성 요약: Δ±0.5ms 에서의 위반율 요동폭
    print("\n## 견고성 요약 (|Δ|≤0.5 ms 요동폭, %p)")
    for n in data:
        sw = max(curves[n][s] for s in (-0.5, 0, 0.5)) - \
             min(curves[n][s] for s in (-0.5, 0, 0.5))
        print(f"- {n}: {sw:.2f} %p")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "p4_sensitivity.json")
    json.dump({"slo": SLO_SEARCH, "curves": curves,
               "quantiles": {n: {"n": len(xs), "p50": q(xs, .5), "p95": q(xs, .95),
                                 "p99": q(xs, .99)} for n, xs in data.items()}},
              open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n원자료 -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

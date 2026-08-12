#!/usr/bin/env python3
"""STAGE6 §1 — site × class × band SLO 예산 표 (런 0, 계산 + 실측 대조).

예산(f_c 에 남는 여유) = SLO[class] − GB − d_net[site] − d_acc(class, band)
  d_acc = resp_bytes × 8 / band_kbit × overhead      (sorts_ctl.decide 와 동일 식)
예산 ≤ 0 이면 **서버가 아무리 빨라도** SLO 를 못 맞춘다 = 구조적 불가 칸.

산출: budget_table.csv (전 밴드 × 3사이트 × 3클래스) + 표준출력 마크다운 표.
"""
import csv
import os
import sys

SLO = {"search": 45.0, "reserve": 35.0, "recommend": 35.0}
RESP_B = {"search": 4474, "reserve": 36, "recommend": 200}
D_NET = {"S1": 2.0, "S2": 15.0, "S3": 25.0}
GB = 5.0
OVERHEAD = 1.10
BANDS = [("무제한", None), ("20000k(정상)", 20000), ("4500k(완화)", 4500),
         ("2300k(poor)", 2300), ("1600k(extreme)", 1600)]
SITES = ["S1", "S2", "S3"]
CLASSES = ["search", "reserve", "recommend"]
OUT = os.path.dirname(os.path.abspath(__file__))

# 실측 대조 — 밴드 both 창에서 관측된 위반율 범위 (부하에 따른 폭)
OBS = {
    (1600, "S3", "search"): "100 % (전 부하·전 정책, f_c 3.8~5.7 ms 로 서버는 한가)",
    (1600, "S2", "search"): "10.1 %(L200·21 rps) → 45.9 %(L1400·170) → 99.4 %(버스트)",
    (1600, "S1", "search"): "0.4~2.4 %(K비 ≤ 0.92) / 83.6 %(K비 1.65, 용량 초과)",
    (1600, "S3", "recommend"): "0 %(L200) → 34~60 %(L1400·166~388 rps)",
    (1600, "S3", "reserve"): "0 %(L200) → 0.9~4.4 %(L1400)",
    (1600, "S2", "recommend"): "0.1 %(L200) → 6.5~21 %(L1400)",
    (1600, "S2", "reserve"): "0 %(L200) → 0.3~13 %(L1400)",
    (4500, "S3", "search"): "0.06 %(야간 D2 4500k×800) — 예산 양수라 변별 자체가 없다",
    (2300, "S3", "search"): "phase4 poor 계열에서 S3 search 배제(계단 성립 구간)",
}


def d_acc(klass, band):
    if band is None:
        return 0.0
    return RESP_B[klass] * 8.0 / band * OVERHEAD


def main():
    rows = []
    for bname, b in BANDS:
        for klass in CLASSES:
            da = d_acc(klass, b)
            for s in SITES:
                budget = SLO[klass] - GB - D_NET[s] - da
                rows.append({"band": bname, "band_kbit": b or "",
                             "class": klass, "site": s,
                             "slo_ms": SLO[klass], "gb_ms": GB,
                             "d_net_ms": D_NET[s], "d_acc_ms": round(da, 2),
                             "fc_budget_ms": round(budget, 2),
                             "feasible": int(budget > 0),
                             "observed": OBS.get((b, s, klass), "")})
    with open(os.path.join(OUT, "budget_table.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print("## site × class × band  f_c 예산 [ms]  "
          "(= SLO − GB − d_net − d_acc; ≤ 0 이면 구조적 불가)\n")
    for klass in CLASSES:
        print(f"### {klass} (응답 {RESP_B[klass]} B, SLO {SLO[klass]:.0f} ms)\n")
        print("| 밴드 | d_acc | S1 (d_net 2) | S2 (15) | S3 (25) |")
        print("|---|---|---|---|---|")
        for bname, b in BANDS:
            da = d_acc(klass, b)
            cells = []
            for s in SITES:
                v = SLO[klass] - GB - D_NET[s] - da
                cells.append(f"**{v:+.1f}**" if v <= 0 else f"{v:+.1f}")
            print(f"| {bname} | {da:.1f} | " + " | ".join(cells) + " |")
        print()
    print("### 실측 대조 (예산이 예측한 것이 실제로 일어났는가)\n")
    print("| 밴드 | 칸 | 예산 | 실측 |")
    print("|---|---|---|---|")
    for (b, s, k), obs in sorted(OBS.items(), key=lambda x: (-x[0][0], x[0][1])):
        v = SLO[k] - GB - D_NET[s] - d_acc(k, b)
        print(f"| {b}k | {s}_{k} | {v:+.1f} ms | {obs} |")
    print(f"\n-> {OUT}/budget_table.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""STAGE4 사전 등록 판정 — s4_results.json 을 PREREG_S4 기준으로 기계 판정."""
import json
import os
import sys
from collections import defaultdict

D_EFF = {"delay025": 0.31, "delay100": 1.06, "delay200": 2.06,
         "rnis": 0.21, "nwdaf": 2.06, "ideal": 0.06,
         "average": 0.06, "discret": 0.06}    # 실효 = 명목 + ~0.06 s


def arm_of(rid):
    return rid.split("_")[1]


def ms(v):
    v = [x for x in v if x is not None]
    if not v:
        return None, None
    m = sum(v) / len(v)
    sd = (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5 if len(v) > 1 else 0.0
    return m, sd


def main():
    root = sys.argv[1]
    res = [r for r in json.load(open(os.path.join(root, "s4_results.json")))
           if "error" not in r]
    arms = defaultdict(list)
    for r in res:
        arms[arm_of(r["run_id"])].append(r)

    def agg(a, key):
        return ms([key(r) for r in arms.get(a, [])])

    c1 = {a: agg(a, lambda r: r["c1_during_viol_pct_observed"]) for a in arms}
    nd = {a: agg(a, lambda r: r["nondeg_during_viol_pct"]) for a in arms}
    react = {a: agg(a, lambda r: r["detect"]["react_from_effect_s"]) for a in arms}
    det = {a: sum(1 for r in arms[a] if r["detect"]["detected"]) for a in arms}
    stv = {a: agg(a, lambda r: float(sum((r["stale_starved"] or {}).values())
                                     if r["stale_starved"] else 0)) for a in arms}
    het = {a: agg(a, lambda r: r["divergence"]["het_pair_pct"]) for a in arms}
    cells = {a: agg(a, lambda r: float(r["coverage"]["active_cells"])) for a in arms}
    cov = {a: agg(a, lambda r: r["coverage"]["obs_tick_pct"]) for a in arms}
    flips = {a: agg(a, lambda r: float(r["c1s_switches_during"])) for a in arms}

    print("## arm 요약\n")
    print("| arm | 실효지연 | c1 위반% | 비열화 위반% | 감지 n/N | 반응(발효)s | 플립 | starved | 분화도% | 활성칸 | obs% |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for a in ("ideal", "delay025", "delay100", "delay200", "average",
              "discret", "rnis", "nwdaf"):
        if a not in arms:
            continue
        f = lambda t: "—" if t[0] is None else f"{t[0]:.3f}±{t[1]:.3f}"
        print(f"| {a} | {D_EFF[a]:.2f} | {f(c1[a])} | {f(nd[a])} | "
              f"{det[a]}/{len(arms[a])} | {f(react[a])} | {f(flips[a])} | "
              f"{f(stv[a])} | {f(het[a])} | {f(cells[a])} | {f(cov[a])} |")

    print("\n## P-S4 판정\n")
    lines = []
    # P-S4-1 단조성 + 무릎
    seq = [("ideal", c1["ideal"][0])] + \
          [(a, c1[a][0]) for a in ("delay025", "delay100", "delay200") if a in c1]
    mono = all(seq[i][1] <= seq[i + 1][1] + 0.02 for i in range(len(seq) - 1))
    lines.append(("P-S4-1 c1 위반 D 단조 증가",
                  f"{[(a, None if v is None else round(v, 3)) for a, v in seq]} -> "
                  f"{'단조' if mono else '비단조 — 보고'}"))
    # P-S4-2 average
    lines.append(("P-S4-2 average 감지 실패/희석",
                  f"감지 {det.get('average')}/{len(arms.get('average', []))}, "
                  f"c1 {c1['average'][0]:.3f}% (ideal {c1['ideal'][0]:.3f}%), "
                  f"비열화 {nd['average'][0]:.4f}% (ideal {nd['ideal'][0]:.4f}%)"))
    # P-S4-3 starved 반전 + 갈래
    sA = stv.get("average", (None,))[0]
    sI = stv.get("ideal", (None,))[0]
    hA = het.get("average", (None,))[0]
    hI = het.get("ideal", (None,))[0]
    branch = None
    if sA is not None and sI is not None and sA < sI:
        branch = ("갈래 A(분화 소멸형 — '해상도 소실이 관측 문제를 함께 지운다')"
                  if (hA is not None and hI is not None and hA < hI * 0.3)
                  else "갈래 B(커버리지형 — '커버리지와 해상도의 상충')")
    lines.append(("P-S4-3 starved 반전 + 인과 갈래",
                  f"starved ideal {sI} -> average {sA}; 분화도 {hI} -> {hA} "
                  f"=> {branch or '감소 없음 — 예측 불발, 보고'}"))
    # P-S4-4 discret
    lines.append(("P-S4-4 discret 플립/위반 분리",
                  f"플립 ideal {flips['ideal'][0]:.1f} -> discret "
                  f"{flips['discret'][0]:.1f}; c1 위반 {c1['ideal'][0]:.3f} -> "
                  f"{c1['discret'][0]:.3f}%"))
    # P-S4-5 합성
    d_c1 = abs(c1["rnis"][0] - c1["ideal"][0])
    d_nd = abs(nd["rnis"][0] - nd["ideal"][0])
    lines.append(("P-S4-5 rnis ≈ ideal (차 ≤ 0.1 %p)",
                  f"c1 Δ{d_c1:.3f} / 비열화 Δ{d_nd:.4f} -> "
                  f"{'통과' if d_c1 <= 0.1 and d_nd <= 0.1 else '이탈 — 보고'}; "
                  f"nwdaf c1 {c1['nwdaf'][0]:.3f}% "
                  f"({'명확 열화' if c1['nwdaf'][0] > c1['ideal'][0] + 0.1 else '열화 불명 — 보고'})"))
    # P-S4-6 가산성
    base = c1["ideal"][0]
    single = sum(max(0.0, c1[a][0] - base) for a in ("delay200", "average", "discret"))
    combo = c1["nwdaf"][0] - base
    lines.append(("P-S4-6 가산성 (nwdaf Δ vs Σ 단일 Δ)",
                  f"Σ단일 {single:.3f} %p vs nwdaf {combo:.3f} %p -> "
                  f"{'초가산(상호작용) — 보고' if combo > single * 1.3 else ('저가산 — 보고' if combo < single * 0.7 else '가산 범위')}"))
    print("| 항목 | 결과 |")
    print("|---|---|")
    for k, v in lines:
        print(f"| {k} | {v} |")


if __name__ == "__main__":
    main()

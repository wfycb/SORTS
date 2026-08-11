#!/usr/bin/env python3
"""STAGE3 사전 등록 판정 — s3_results.json 을 PREREG_S3 기준으로 기계 판정."""
import json
import os
import sys
from collections import defaultdict


def ms(v):
    v = [x for x in v if x is not None]
    if not v:
        return None, None
    m = sum(v) / len(v)
    sd = (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5 if len(v) > 1 else 0.0
    return m, sd


def main():
    root = sys.argv[1]
    res = [r for r in json.load(open(os.path.join(root, "s3_results.json")))
           if "error" not in r]
    arms = defaultdict(list)
    for r in res:
        arms[r["n_cohorts"]].append(r)
    Ns = sorted(arms)
    lines = []

    # P-S3-1: 비열화 코호트 during 위반율 < 0.05% (전 arm 전 런)
    bad1 = [f"{r['run_id']}={r['nondeg_during_viol_pct']}%" for r in res
            if r["nondeg_during_viol_pct"] is None
            or r["nondeg_during_viol_pct"] >= 0.05]
    for n in Ns:
        m, sd = ms([r["nondeg_during_viol_pct"] for r in arms[n]])
        lines.append((f"주 지표 c{n} 비열화 during 위반%", f"{m:.4f}±{sd:.4f}"))
    lines.append(("P-S3-1 비열화 위반율 < 0.05 % (전 런)",
                  "**통과**" if not bad1 else f"**실패**: {bad1}"))

    # P-S3-2: pre 창 arm 간 최대차 <= 0.1 %p
    pre = {n: ms([r["pre_all_viol_pct"] for r in arms[n]])[0] for n in Ns}
    spread = max(pre.values()) - min(pre.values())
    lines.append((f"P-S3-2 pre 창 위반율 arm 간 최대차 (실측 {pre})",
                  f"{spread:.4f} %p -> {'통과' if spread <= 0.1 else '실패'}"))

    # P-S3-3: starved stale == 0 (전 런)
    bad3 = [f"{r['run_id']}:{r['stale_starved']}" for r in res
            if r["stale_starved"] not in (0, {})]
    lines.append(("P-S3-3 starved stale 0 (site×class, 트래픽-0 제외)",
                  "**통과**" if not bad3 else f"**발생 — 관측 조건 미달 분류**: {bad3}"))

    # P-S3-4: c1:search 플립이 코호트 수에 따라 증가하지 않음 + 비열화 신규 플립
    sw = {n: ms([float(r["c1s_switches_during"]) for r in arms[n]]) for n in Ns}
    for n in Ns:
        lines.append((f"P-S3-4 c{n} c1:search 플립", f"{sw[n][0]:.1f}±{sw[n][1]:.1f}"))
    base_m, base_sd = sw[Ns[0]]
    inc = [n for n in Ns[1:] if sw[n][0] > base_m + max(2 * (base_sd or 0), 5)]
    ndch = {n: [r["nondeg_changed"] for r in arms[n]] for n in Ns}
    lines.append(("P-S3-4 판정 (기준 arm 대비 증가?)",
                  ("증가 없음 — 통과" if not inc else f"증가 arm {inc} — I-17 입력")
                  + f" | 비열화 유닛 changed: {ndch}"))

    # P-S3-5: 결합 크기 Δf_c
    for n in Ns:
        agg = defaultdict(list)
        for r in arms[n]:
            for k, v in (r["delta_fc_obs_p95_med"] or {}).items():
                agg[k].append(v)
        top = sorted(((k, ms(v)[0]) for k, v in agg.items()),
                     key=lambda x: -abs(x[1]))[:4]
        lines.append((f"P-S3-5 c{n} Δf_c(during−pre, obs p95 중앙값) 상위",
                      ", ".join(f"{k} {v:+.2f}ms" for k, v in top)))

    # P-S3-6: 비열화 slack 잠식
    for n in Ns:
        worst = {}
        for r in arms[n]:
            for u, v in (r["slack_nondeg"] or {}).items():
                d = v["dur_min"]
                if u not in worst or d < worst[u][0]:
                    worst[u] = (d, v["pre_med"], v["dur_med"])
        w4 = sorted(worst.items(), key=lambda x: x[1][0])[:4]
        nz = sorted({u for r in arms[n] for u in r["slack_near_zero_units"]})
        lines.append((f"P-S3-6 c{n} 비열화 slack (min-of-runs 하위 4)",
                      ", ".join(f"{u} min {v[0]:.1f} (med {v[1]:.1f}→{v[2]:.1f})"
                                for u, v in w4)
                      + (f" | **0 근접 유닛**: {nz}" if nz else " | 0 근접 없음")))

    # P-S3-7 + 예측
    lines.append(("P-S3-7 발현", "P-S3-1 결과와 §P-S3-5/6 잠식 관측을 함께 서술"
                                 " (위반 0 이어도 잠식 관측 시 보고)"))
    print("| 항목 | 결과 |")
    print("|---|---|")
    for k, v in lines:
        print(f"| {k} | {v} |")


if __name__ == "__main__":
    main()

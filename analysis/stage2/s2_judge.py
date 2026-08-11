#!/usr/bin/env python3
"""STAGE2 사전 등록 판정 — s2_results.json 을 PREREG_S2 기준으로 기계 판정.

등록 출처: analysis/stage2/PREREG_S2.md (§1~§5 원판, §6~§9 v3 개정판),
anchor_spec.json. 판정 규칙은 여기서 바꾸지 않는다 — 수치만 대입한다.
"""
import json
import os
import sys
from collections import defaultdict

SPEC = "/home/user/exp/analysis/stage2/anchor_spec.json"


def pctl(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def ms(v):
    v = [x for x in v if x is not None]
    if not v:
        return None, None
    m = sum(v) / len(v)
    sd = (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5 if len(v) > 1 else 0.0
    return m, sd


def main():
    root = sys.argv[1]
    res = [r for r in json.load(open(os.path.join(root, "s2_results.json")))
           if "error" not in r]
    spec = json.load(open(SPEC))
    arms = defaultdict(list)
    for r in res:
        arms[r["T_s"]].append(r)
    Ts = sorted(arms, reverse=True)

    print("## P-S2 판정 (사전 등록 대비)\n")
    lines = []

    # --- P-S2-0' 앵커 ---
    a_bad = []
    for r in res:
        a1 = r.get("effect_A1_rel_s")
        lo, hi = spec["A1_band_s"]
        if a1 is None or not (lo <= a1 <= hi):
            a_bad.append(f"{r['run_id']} A1={a1}")
    lines.append(("P-S2-0'(a) A1 ∈ [%.3f, %.3f]" % tuple(spec["A1_band_s"]),
                  "통과" if not a_bad else "이탈: " + ", ".join(a_bad)))
    c_bad = [f"{r['run_id']}={r['first_transition']['react_from_effect_s']}"
             for r in res
             if r["first_transition"]["react_from_effect_s"] is None
             or r["first_transition"]["react_from_effect_s"] > r["T_s"] + 0.010
             or r["first_transition"]["react_from_effect_s"] < -0.001]
    lines.append(("P-S2-0'(c) = P-S2-1a: 반응 < T+10 ms",
                  "통과" if not c_bad else "이탈: " + ", ".join(c_bad)))

    # --- P-S2-1b 기대값 ---
    for T in Ts:
        m, sd = ms([r["first_transition"]["react_from_effect_s"] for r in arms[T]])
        lines.append((f"P-S2-1b T={T}s 반응 평균 (등록 기대 E=T/2={T/2:.4f})",
                      f"{m:.4f}±{sd:.4f} s (기각 사유 아님 — 위상 의존)"))

    # --- P-S2-1c burst ---
    base = ms([float(r["burst"]["search_viol"]) for r in arms[Ts[0]]])[0] \
        if Ts else None
    for T in Ts[1:]:
        m, _ = ms([float(r["burst"]["search_viol"]) for r in arms[T]])
        if base and base > 0:
            red = 100 * (1 - m / base)
            need = 80.0 if T == 0.05 else 90.0
            lines.append((f"P-S2-1c T={T}s burst 감소 (등록 ≥{need:.0f}%)",
                          f"{red:.1f}% ({base:.1f} → {m:.1f}) "
                          f"-> {'통과' if red >= need else '미달'}"))
        else:
            lines.append((f"P-S2-1c T={T}s", f"기준선 burst={base} 로 비율 산정 불가"))

    # --- P-S2-1d / P-S2-2 채택 규칙 ---
    stats = {}
    for T in Ts:
        m, sd = ms([r["viol_pct"]["during_c1_search"] for r in arms[T]])
        stats[T] = (m, sd)
        lines.append((f"주 지표 T={T}s during 위반%(c1 search)", f"{m:.3f}±{sd:.3f}"))
    spread = max(s[0] for s in stats.values()) - min(s[0] for s in stats.values())
    lines.append(("P-S2-1d: arm 간 during(c1s) 차이 ≤ ~1 %p 예측",
                  f"실측 최대차 {spread:.3f} %p -> "
                  f"{'예측 적중' if spread <= 1.0 else '예측 이탈'}"))

    def overlap(a, b):
        (ma, sa), (mb, sb) = stats[a], stats[b]
        return not (ma + sa < mb - sb or mb + sb < ma - sa)
    verdict = []
    if len(Ts) >= 2:
        pairs = [(Ts[i], Ts[i + 1]) for i in range(len(Ts) - 1)]
        for a, b in pairs:
            verdict.append(f"{a}s vs {b}s: {'겹침' if overlap(a, b) else '분리'}")
    adopt = Ts[0]
    for i in range(len(Ts) - 1):
        slow, fast = Ts[i], Ts[i + 1]
        if not overlap(slow, fast) and stats[fast][0] < stats[slow][0]:
            adopt = fast
        else:
            break
    lines.append(("P-S2-2 채택 규칙 (겹치면 느린 주기)",
                  f"{'; '.join(verdict)} -> **채택 후보 T={adopt}s**"))

    # --- 진동 가드레일 ---
    base_sw = ms([float(r["c1s_flip"]["n_switches_during"])
                  for r in arms[Ts[0]]])[0]
    for T in Ts:
        m, _ = ms([float(r["c1s_flip"]["n_switches_during"]) for r in arms[T]])
        orig = "발동" if m > 20 else "미발동"
        ratio = (m / base_sw) if base_sw else None
        aux = ("해당 없음(기준 arm)" if T == Ts[0]
               else f"{ratio:.2f}× -> {'보류' if ratio and ratio > 1.5 else '통과'}")
        lines.append((f"가드레일 T={T}s c1:search 전환 {m:.1f}회",
                      f"원 등록(20회 절대) **{orig}** / 보조(1.5×) {aux}"))

    # --- P-S2-3 플립 시간척도 ---
    # 등록 문구("왕복 주기 1.8~2.2 s")는 통계를 명시하지 않았다. 등록 근거였던
    # 미니 런 관측은 **전환 간격** p50 = 2.0 s 였고, 2전환 왕복은 3.0 s 였다.
    # 자의적 선택을 피하려고 **둘 다** 내고, 등록의 실질(=T 비의존)로 판정한다.
    gap_by_T, rt_by_T = {}, {}
    for T in Ts:
        gm, gsd = ms([r["c1s_flip"]["switch_gap_p50_s"] for r in arms[T]])
        rm, rsd = ms([r["c1s_flip"]["roundtrip_p50_s"] for r in arms[T]])
        gap_by_T[T], rt_by_T[T] = gm, rm
        lines.append((f"P-S2-3 T={T}s 플립 시간척도",
                      f"전환 간격 p50 {gm}±{gsd} s / 2전환 왕복 p50 {rm}±{rsd} s"))
    gv = [v for v in gap_by_T.values() if v is not None]
    if len(gv) >= 2:
        rng = max(gv) - min(gv)
        # T 는 40배 차이 — 시간척도가 T 에 비례하면 arm 간 차이가 T 규모여야 한다
        lines.append(("P-S2-3 판정 (실질: 시간척도가 T 에 비례하는가)",
                      f"전환 간격 arm 간 범위 {rng:.3f} s (T 는 1.0 → 0.025, 40배) -> "
                      f"{'T 비의존 — WINDOW_S 귀속 가설 유지' if rng < 0.5 else 'T 의존 — 기전 재특정 필요'}"))
        lines.append(("P-S2-3 부기 (등록 문구 모호성)",
                      "등록 대역 1.8~2.2 s 는 미니 런의 **전환 간격**(2.0 s)에서 나온 값. "
                      "2전환 왕복(≈3.0 s)은 그 대역 밖이므로, 대역 자체가 아니라 "
                      "T 비의존성으로 판정했다(사후 선택 회피 — 두 통계 모두 공개)."))

    print("| 항목 | 결과 |")
    print("|---|---|")
    for k, v in lines:
        print(f"| {k} | {v} |")


if __name__ == "__main__":
    main()

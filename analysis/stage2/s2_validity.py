#!/usr/bin/env python3
"""STAGE2 §3(G2 회신) — T=1 s 채택의 **유효 범위** 산출. 런 추가 0.

두 항을 같은 단위로 놓고 교차점을 낸다 (전부 c1 search, 발효 기준 창):
  B = 밴드 변화 **1회당** 주기 비용 = burst 위반 (T=1 s), T≤50 ms 에서 ≈0
  R = 열화 **지속 1초당** 관측 신선도 비용 = 잔여 위반 / 잔여 창 길이
교차: B = R · d  ->  d* = B/R  (밴드 변화 간격 임계),  f* = 60/d* [회/분]

가정 (명시):
 (1) 스텝 교란 1회 = burst 1회 (실측: 주입당 첫 전환 1회)
 (2) 교란 간 독립 — burst 비용이 빈도에 무관
 (3) 열화 지속 중 잔여 위반율 일정 (실측: during 창에서 균일)
 (4) 모집단 c1 search, 밴드 2300 kbit, 부하 800 rps, SLO 45 ms 조건부
 (5) T≤50 ms 의 burst ≈ 0 (실측 0건)

주의(전제 반박): "교란 f 회/분" 만으로는 교차점이 나오지 않는다 — 각 교란의
지속시간이 고정이면 burst 항과 잔여 항이 **둘 다 f 에 비례**해 f 가 소거된다.
의미 있는 임계는 **밴드 변화 간격 d**(= 열화 지속시간당 변화 횟수)다.
"""
import json
import sys


def main():
    root = sys.argv[1]
    res = [r for r in json.load(open(f"{root}/s2_results.json")) if "error" not in r]
    slow = [r for r in res if r["T_s"] >= 1.0]
    fast = [r for r in res if r["T_s"] < 1.0]

    def mean(v):
        v = [x for x in v if x is not None]
        return sum(v) / len(v) if v else None

    B = mean([float(r["burst"]["search_viol"]) for r in slow])
    B_fast = mean([float(r["burst"]["search_viol"]) for r in fast])
    dur_slow = mean([float(r["during_c1_search_viol"]) for r in slow])
    resid_slow = mean([float(r["during_c1_search_viol"] - r["burst"]["search_viol"])
                       for r in slow])
    resid_fast = mean([float(r["during_c1_search_viol"] - r["burst"]["search_viol"])
                       for r in fast])
    react = mean([r["first_transition"]["react_from_effect_total_s"] for r in slow])
    win = 120.0                      # 교란 창 (disturb_start~end)
    resid_win = win - (react or 0.0)
    R = resid_slow / resid_win
    d_star = B / R
    f_star = 60.0 / d_star

    out = {
        "n_slow": len(slow), "n_fast": len(fast),
        "B_burst_per_change_T1s": round(B, 2),
        "B_burst_per_change_Tfast": round(B_fast, 2),
        "during_c1s_viol_T1s": round(dur_slow, 1),
        "burst_share_pct_T1s": round(100 * B / dur_slow, 1),
        "residual_T1s": round(resid_slow, 1),
        "residual_Tfast": round(resid_fast, 1),
        "residual_window_s": round(resid_win, 2),
        "R_resid_per_s": round(R, 4),
        "d_star_s": round(d_star, 1),
        "f_star_per_min": round(f_star, 2),
        "testbed_change_rate_per_min": round(60.0 / win, 2),
    }
    json.dump(out, open(f"{root}/s2_validity.json", "w"), ensure_ascii=False, indent=1)

    print("## T=1 s 채택의 유효 범위 (런 추가 0)\n")
    print("| 항목 | 값 |")
    print("|---|---|")
    print(f"| B = 밴드 변화 1회당 burst 위반 (T=1 s, n={len(slow)}) | **{B:.1f}건** |")
    print(f"| 같은 값 (T≤50 ms, n={len(fast)}) | {B_fast:.1f}건 |")
    print(f"| during(c1 search) 위반 (T=1 s) | {dur_slow:.1f}건 |")
    print(f"| 그중 burst 몫 | **{100*B/dur_slow:.1f} %** |")
    print(f"| 잔여(관측 신선도) 위반 — T=1 s / T≤50 ms | {resid_slow:.1f} / {resid_fast:.1f}건 (arm 무관) |")
    print(f"| R = 열화 1초당 잔여 위반 | {R:.3f}건/s (창 {resid_win:.1f} s) |")
    print(f"| **d\\* = B/R (밴드 변화 간격 임계)** | **{d_star:.1f} s** |")
    print(f"| **f\\* = 60/d\\*** | **{f_star:.2f} 회/분** |")
    print(f"| 본 테스트베드 실제 변화율 | {60.0/win:.2f} 회/분 (120 s 창에 스텝 1회) |")
    print(f"\n해석: 밴드 변화가 **{d_star:.0f} s 보다 자주**(= {f_star:.2f} 회/분 초과) "
          f"일어나면 주기 비용이 관측 신선도 비용을 넘어선다. 본 테스트베드는 "
          f"{60.0/win:.2f} 회/분으로 임계의 약 {f_star/(60.0/win):.0f}분의 1 이라 "
          f"T=1 s 로 충분하다.")


if __name__ == "__main__":
    main()

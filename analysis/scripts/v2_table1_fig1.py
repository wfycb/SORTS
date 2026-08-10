#!/usr/bin/env python3
"""STEP V2 — 표 1 + 그림 1 (정책별 SLO 초과율).

지표: corrected_ms (사용자 체감). 위반 = (바이트 이탈 or non-200) or corrected_ms > SLO.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import common as C  # noqa: E402

recs = []
for rid in C.RUN_IDS:
    df = C.load_df(rid)
    pol, dis = C.policy_of(rid), C.disturb_of(rid)
    sec = C.sections_of(rid)
    df["viol"] = ((df["valid"] == 0)
                  | (df["corrected_ms"] > df["ep"].map(C.SLO_MS))).astype(int)
    for sname, (a, b) in sec.items():
        s = df[(df["t_rel"] >= a) & (df["t_rel"] < b)]
        for coh in (1, 2):
            sc = s[s["cohort"] == coh]
            for ep in C.EPS:
                e = sc[sc["ep"] == ep]
                if not len(e):
                    continue
                recs.append({
                    "run_id": rid, "policy": pol, "disturb": dis,
                    "section": sname, "cohort": coh, "endpoint": ep,
                    "n": len(e), "n_viol": int(e["viol"].sum()),
                    "viol_rate": round(e["viol"].mean(), 6),
                    "corrected_p50": round(e["corrected_ms"].quantile(.50), 3),
                    "corrected_p95": round(e["corrected_ms"].quantile(.95), 3),
                    "corrected_p99": round(e["corrected_ms"].quantile(.99), 3),
                })
    print(f"  {rid} 처리", flush=True)

t1 = pd.DataFrame(recs)
out = os.path.join(C.ANA, "tables", "table1_slo_violation.csv")
t1.to_csv(out, index=False)
print(f"-> {out}  ({len(t1)}행)")

# ---------------------------------------------------------------- 그림 1
a = t1[(t1["disturb"] != "ramp") & (t1["section"] == "during") & (t1["cohort"] == 1)]
piv = a.pivot_table(index=["disturb", "policy"], columns="endpoint",
                    values="viol_rate")
fig_csv = os.path.join(C.ANA, "figures", "fig1_slo_by_policy_data.csv")
a[["run_id", "policy", "disturb", "endpoint", "n", "n_viol", "viol_rate"]] \
    .to_csv(fig_csv, index=False)
print(f"-> {fig_csv}")

try:
    import numpy as np
    plt = C.setup_mpl()
except ImportError:
    print("matplotlib 없음 — CSV 만 생성")
    sys.exit(0)

# 값이 0.014%~100% 에 걸쳐 있어 선형축에서는 작은 값이 사라진다 -> 로그축.
FLOOR = 0.005          # % — 0 인 막대를 그리기 위한 바닥
fig, axes = plt.subplots(1, 3, figsize=(15, 5.0), sharey=True)
colors = C.EP_COLOR
x = np.arange(len(C.POLICIES))
w = 0.26
for ax, dis in zip(axes, C.DISTURBS):
    for i, ep in enumerate(C.EPS):
        vals = [piv.loc[(dis, p), ep] * 100 if (dis, p) in piv.index else 0
                for p in C.POLICIES]
        drawn = [max(v, FLOOR) for v in vals]
        bars = ax.bar(x + (i - 1) * w, drawn, w, label=ep, color=colors[ep],
                      bottom=FLOOR, linewidth=0.6, edgecolor="#fcfcfb")
        for bx, v in zip(bars, vals):
            ax.text(bx.get_x() + bx.get_width() / 2, max(v, FLOOR) * 1.15,
                    f"{v:.3g}", ha="center", va="bottom", fontsize=7.5,
                    rotation=90, color="#52514e")
    ax.set_yscale("log")
    ax.set_ylim(FLOOR, 900)
    ax.set_xticks(x)
    ax.set_xticklabels(C.POLICIES, fontsize=9)
    # bl_loc 과부하 표식 — 다른 정책과 같은 선상에서 읽으면 안 된다
    ax.get_xticklabels()[3].set_color("#e34948")
    ax.get_xticklabels()[3].set_fontweight("bold")
    ax.axvspan(2.55, 3.45, color=C.ALERT, alpha=0.08, zorder=0)
    ax.text(3, 330, "과부하 상태\ns1_knee_ratio 1.14 / 달성률 79%\n(같은 선상 비교 금지)",
            ha="center", va="center", fontsize=7.2, color=C.ALERT,
            fontweight="bold", linespacing=1.4,
            bbox=dict(fc="#fcfcfb", ec=C.ALERT, lw=0.7, alpha=0.95, pad=2.5))
    ax.axhline(5, color=C.INK2, ls=":", lw=1)
    ax.set_title(f"disturb = {dis}", fontsize=11)
    ax.grid(axis="y", alpha=0.35, which="major")
    ax.set_axisbelow(True)
axes[0].set_ylabel("SLO 초과율 [%] — 로그축\n(코호트1, during, corrected_ms)")
axes[0].legend(fontsize=9, title="endpoint", loc="upper left")
axes[0].text(-0.48, 5.8, "5%", fontsize=7.5, color=C.INK2)
fig.suptitle("Fig 1. SLO violation rate by policy — cohort 1, during window, "
             "metric = corrected_ms  (log y; bl_loc is overloaded even at "
             "disturb=none)", fontsize=11.5)
fig.tight_layout()
p = os.path.join(C.ANA, "figures", "fig1_slo_by_policy.png")
fig.savefig(p, dpi=160)
print(f"-> {p}")

# 콘솔 요약
print("\n[during / cohort1 / 초과율 %]")
print(piv.mul(100).round(3).to_string())
print("\n[none 기준선 — site_s3 전 구간 (cohort1)]")
b = t1[(t1["run_id"] == "A_none_site_s3") & (t1["cohort"] == 1)]
print(b.pivot_table(index="section", columns="endpoint",
                    values="viol_rate").mul(100).round(4).to_string())

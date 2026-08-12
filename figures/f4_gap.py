#!/usr/bin/env python3
"""F4 — 부하 격차 곡선. 입력: runs/stage5-20260812/s5_gap_curve.csv (STAGE5 방출물).

위: 정책 3선(위반율). 아래: SORTS − 최선 비교군 격차(%p), 0선 — L=1400 부호 반전.
"""
import argparse
import csv
import os

import style as st
import matplotlib.pyplot as plt

EXP = "/home/user/exp"
SRC = os.path.join(EXP, "runs/stage5-20260812/s5_gap_curve.csv")
COL = {"SORTS": "viol_sorts_pct", "bl_lr": "viol_bl_lr_pct",
       "bl_loc_pri": "viol_bl_loc_pri_pct"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="talk", choices=["talk", "paper"])
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--xaxis", default="load", choices=["load", "kratio"])
    a = ap.parse_args()
    prof = st.apply(a.profile, a.scale)
    rows = sorted(csv.DictReader(open(SRC)), key=lambda r: float(r["L_total_rps"]))
    L = [float(r["L_total_rps"]) for r in rows]
    K = [float(r["k_ratio_bl_lr"]) for r in rows]
    x = L if a.xaxis == "load" else K
    gap = [float(r["gap_pp"]) for r in rows]

    w, h = prof["figsize_4panel"]
    fig, axes = plt.subplots(2, 1, figsize=(w, h * 0.82), sharex=True,
                             gridspec_kw={"height_ratios": [1.6, 1.0]})
    for pol, col in COL.items():
        s = st.POLICY[pol]
        axes[0].plot(x, [float(r[col]) for r in rows], color=s["color"], ls=s["ls"],
                     marker=s["marker"], ms=8, label=st.POLICY_LABEL[pol])
    axes[0].set_ylabel("violation rate [%]\n(log)")
    axes[0].set_yscale("log")
    axes[0].set_ylim(0.1, 260)
    axes[0].set_yticks([0.1, 1, 10, 100])
    axes[0].set_yticklabels(["0.1", "1", "10", "100"])
    axes[0].legend(loc="upper left", frameon=False)
    # 4단계 누적표 지점 주석
    i800 = [i for i, v in enumerate(L) if v == 800][0]
    axes[0].annotate("6.50 % — the point where the\ncumulative table (F3) was measured",
                     xy=(x[i800], float(rows[i800]["viol_sorts_pct"])),
                     xytext=(-14, 52), textcoords="offset points", ha="right",
                     color=st.POLICY["SORTS"]["color"],
                     arrowprops=dict(arrowstyle="->", lw=1.6,
                                     color=st.POLICY["SORTS"]["color"]))
    axes[1].axhline(0, color="#000000", lw=1.2)
    axes[1].plot(x, gap, color="#444444", marker="D", ms=7)
    for xi, g in zip(x, gap):
        axes[1].annotate(f"{g:+.2f}", xy=(xi, g), xytext=(0, 11),
                         textcoords="offset points", ha="center", fontweight="bold",
                         color="#1a7f37" if g >= 0 else "#b3261e")
    axes[1].fill_between(x, gap, 0, where=[g >= 0 for g in gap],
                         color="#1a7f37", alpha=0.12, interpolate=True)
    axes[1].fill_between(x, gap, 0, where=[g < 0 for g in gap],
                         color="#b3261e", alpha=0.12, interpolate=True)
    axes[1].set_ylabel("gap [%p]\n(best baseline − SORTS)")
    # 점마다 최선 비교군이 다르다 — 표기 없으면 한 정책과의 격차로 오해된다
    short = {"bl_lr": "vs lr", "bl_loc_pri": "vs loc"}
    for xi, r in zip(x, rows):
        axes[1].annotate(short.get(r["best_comparator"], r["best_comparator"]),
                         xy=(xi, 0), xytext=(0, -6), textcoords="offset points",
                         ha="center", va="top", color=st.ANNOT,
                         fontsize=plt.rcParams["legend.fontsize"])
    axes[1].annotate("SORTS better ↑", xy=(0.995, 0.78), xycoords="axes fraction",
                     ha="right", color="#1a7f37",
                     fontsize=plt.rcParams["legend.fontsize"])
    axes[1].annotate("SORTS worse ↓", xy=(0.30, 0.06), xycoords="axes fraction",
                     ha="right", color="#b3261e",
                     fontsize=plt.rcParams["legend.fontsize"])
    if a.xaxis == "load":
        axes[1].set_xlabel("total offered load [rps]\n"
                           "K-ratio (bl_lr): " +
                           "   ".join(f"{l:.0f} → {k:.2f}" for l, k in zip(L, K)))
        axes[1].set_xticks(L)
        axes[1].set_xticklabels([f"{v:.0f}" for v in L])
        axes[1].minorticks_off()
        axes[1].set_xlim(min(L) - 90, max(L) + 90)
    else:
        axes[1].set_xlabel("K-ratio = S1 demand / C_eff(S1|1600k)   [bl_lr axis]")
        axes[1].axvline(1.0, color=st.ANNOT, ls=":", lw=1.2)
    fig.suptitle("F4  Where the advantage exists — and where it flips\n"
                 "gap = min(baselines) − SORTS; the best baseline changes with "
                 "load (vs loc at 200/450, vs lr at 800/1400)", x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    sfx = "" if a.scale == 1 else f"_x{a.scale:g}"
    name = "F4_gap_curve" + ("" if a.xaxis == "load" else "_kratio") + sfx
    return st.save(fig, os.path.join(EXP, "figures", a.profile), name)


if __name__ == "__main__":
    pdf, _ = main()
    print(f"-> {pdf}")

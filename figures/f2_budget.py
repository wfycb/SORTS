#!/usr/bin/env python3
"""F2 — SLO 예산 히트맵 (site × band × class). 입력: analysis/stage6/budget_table.csv.

목표: "밴드는 클래스를 가려서 때린다"가 그림만 봐도 읽히게.
예산 = SLO − GB − d_net − d_acc  (음수 = 서버가 아무리 빨라도 SLO 불가).
"""
import argparse
import csv
import os

import style as st
import matplotlib.pyplot as plt
import numpy as np

EXP = "/home/user/exp"
SRC = os.path.join(EXP, "analysis/stage6/budget_table.csv")
BANDS = ["무제한", "20000k(정상)", "4500k(완화)", "2300k(poor)", "1600k(extreme)"]
BAND_EN = {"무제한": "no shaping", "20000k(정상)": "20 Mbit/s", "4500k(완화)": "4.5 Mbit/s",
           "2300k(poor)": "2.3 Mbit/s", "1600k(extreme)": "1.6 Mbit/s"}
CLASSES = [("search", "search  (4474 B, SLO 45 ms)"),
           ("reserve", "reserve  (36 B, SLO 35 ms)"),
           ("recommend", "recommend  (200 B, SLO 35 ms)")]
SITES = ["S1", "S2", "S3"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="talk", choices=["talk", "paper"])
    ap.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args()
    prof = st.apply(a.profile, a.scale)
    rows = list(csv.DictReader(open(SRC)))
    grid = {(r["class"], r["band"], r["site"]): float(r["fc_budget_ms"]) for r in rows}

    w, h = prof["figsize_1panel"]
    fig, axes = plt.subplots(1, 3, figsize=(w, h * 0.95),
                             gridspec_kw={"width_ratios": [1, 1, 1]})
    vmax = 40.0
    for ax, (klass, title) in zip(axes, CLASSES):
        M = np.array([[grid[(klass, b, s)] for s in SITES] for b in BANDS])
        im = ax.imshow(M, cmap="RdBu", vmin=-vmax, vmax=vmax, aspect="auto")
        ax.set_xticks(range(3))
        ax.set_xticklabels([f"{s}\n({int(d)} ms)" for s, d in
                            zip(SITES, (2, 15, 25))])
        ax.set_yticks(range(len(BANDS)))
        ax.set_yticklabels([BAND_EN[b] for b in BANDS] if ax is axes[0] else [])
        ax.set_title(title, fontsize=plt.rcParams["axes.titlesize"])
        ax.grid(False)
        for i in range(M.shape[0]):
            for j in range(M.shape[1]):
                v = M[i, j]
                ax.text(j, i, f"{v:+.1f}", ha="center", va="center",
                        color="white" if abs(v) > 22 else "#111111",
                        fontweight="bold" if v <= 0 else "normal",
                        fontsize=plt.rcParams["legend.fontsize"])
                if v <= 0:
                    ax.add_patch(plt.Rectangle((j - .5, i - .5), 1, 1, fill=False,
                                               ec="#000000", lw=2.0))
    axes[0].set_ylabel("radio band")
    cb = fig.colorbar(im, ax=axes, fraction=0.025, pad=0.02)
    cb.set_label("f_c budget [ms]   (negative = SLO unreachable)")
    fig.suptitle("F2  The band hits classes selectively — "
                 "budget = SLO − GB − d_net − d_acc  (boxed = negative)",
                 x=0.01, ha="left")
    return st.save(fig, os.path.join(EXP, "figures", a.profile),
                   f"F2_budget_heatmap{'' if a.scale == 1 else f'_x{a.scale:g}'}")


if __name__ == "__main__":
    pdf, png = main()
    print(f"-> {pdf}")

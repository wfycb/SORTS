#!/usr/bin/env python3
"""F3 — 4단계 누적 분해 막대 (95.0 → 74.6 → 28.1 → 6.5 %). 입력: data/f3_cumulative.csv."""
import argparse
import csv
import os

import style as st
import matplotlib.pyplot as plt

EXP = "/home/user/exp"
SRC = os.path.join(EXP, "figures/data/f3_cumulative.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="talk", choices=["talk", "paper"])
    ap.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args()
    prof = st.apply(a.profile, a.scale)
    rows = list(csv.DictReader(open(SRC)))
    labels = [r["label_wrapped"].replace("\\n", "\n") for r in rows]
    vals = [float(r["viol_pct"]) for r in rows]
    err = [float(r["half_range"]) for r in rows]
    ns = [int(r["n"]) for r in rows]

    w, h = prof["figsize_1panel"]
    fig, ax = plt.subplots(figsize=(w * 0.72, h))
    x = range(len(rows))
    base = st.POLICY["SORTS"]["color"]
    shades = ["#9ecae1", "#6baed6", "#3182bd", base]
    bars = ax.bar(x, vals, yerr=err, capsize=6, color=shades,
                  edgecolor="#222222", lw=1.0)
    for i, (b, v, n) in enumerate(zip(bars, vals, ns)):
        ax.annotate(f"{v:.2f} %" + ("" if n > 1 else "  (n=1)"),
                    xy=(b.get_x() + b.get_width() / 2, v + err[i]),
                    xytext=(0, 6), textcoords="offset points", ha="center",
                    fontweight="bold")
    # 감소 화살표
    for i in range(len(vals) - 1):
        drop = 100 * (vals[i] - vals[i + 1]) / vals[i]
        y = max(vals[i], vals[i + 1]) + 14
        ax.annotate("", xy=(i + 1, y), xytext=(i, y),
                    arrowprops=dict(arrowstyle="->", lw=1.8, color=st.ANNOT))
        ax.annotate(f"−{drop:.0f} %", xy=(i + 0.5, y), xytext=(0, 4),
                    textcoords="offset points", ha="center", color=st.ANNOT)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("SLO violation rate [%]\n(both-cohort degraded window)")
    ax.set_ylim(0, 118)
    ax.set_xlabel("layers switched on (cumulative, left → right)")
    fig.suptitle("F3  What each layer buys — cumulative decomposition\n"
                 "(1600 kbit on both cohorts, 800 rps, 2 cohorts, 16 conn, hc_off)",
                 x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    return st.save(fig, os.path.join(EXP, "figures", a.profile),
                   f"F3_cumulative{'' if a.scale == 1 else f'_x{a.scale:g}'}")


if __name__ == "__main__":
    pdf, _ = main()
    print(f"-> {pdf}")

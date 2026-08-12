#!/usr/bin/env python3
"""F5 — 무선 열화를 Envoy 는 못 본다. 입력: data/f5_blindness.csv.

두 조건을 **분리해서** 놓는다(같은 문장에 병렬 금지, docs/NUMBERS.md §1):
  A. 부하 중(phase4 R1_rr_radio) — 헤드라인 **46 : 1**
  B. 정적 스윕(N2 캘리브, search 단독, p50) — 부기 1024.6 : 1
"""
import argparse
import csv
import os

import style as st
import matplotlib.pyplot as plt

EXP = "/home/user/exp"
SRC = os.path.join(EXP, "figures/data/f5_blindness.csv")
ACCESS = "#0072B2"
ENVOY = "#999999"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="talk", choices=["talk", "paper"])
    ap.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args()
    prof = st.apply(a.profile, a.scale)
    rows = list(csv.DictReader(open(SRC)))

    w, h = prof["figsize_1panel"]
    fig, axes = plt.subplots(1, 2, figsize=(w * 0.8, h))
    titles = ["A.  under load  —  headline",
              "B.  static sweep  —  side note (different measurement)"]
    for ax, r, title in zip(axes, rows, titles):
        acc = abs(float(r["access_side_ms"]))
        env = abs(float(r["envoy_observed_ms"]))
        bars = ax.bar([0, 1], [acc, env], color=[ACCESS, ENVOY],
                      edgecolor="#222222", lw=1.0, width=0.6)
        ax.set_yscale("log")
        ax.set_ylim(0.008, 200)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["access side\n(what the user feels)",
                            "front Envoy\n(what the LB sees)"])
        for b, v in zip(bars, (acc, env)):
            ax.annotate(f"{v:.3f} ms" if v < 1 else f"{v:.1f} ms",
                        xy=(b.get_x() + b.get_width() / 2, v), xytext=(0, 6),
                        textcoords="offset points", ha="center", fontweight="bold")
        ax.set_title(title, loc="left", fontsize=plt.rcParams["axes.titlesize"])
        ax.annotate(f"{float(r['ratio']):,.1f} : 1", xy=(0.5, 0.90),
                    xycoords="axes fraction", ha="center", fontweight="bold",
                    fontsize=plt.rcParams["font.size"] * 1.35,
                    color=ACCESS if r["case"].startswith("A") else "#555555")
        cond = r["condition"]
        if len(cond) > 52:
            cut = cond.rfind(",", 0, 52)
            cond = cond[:cut + 1] + "\n" + cond[cut + 1:].strip()
        ax.set_xlabel(cond, fontsize=plt.rcParams["legend.fontsize"] * 0.95,
                      color=st.ANNOT)
    axes[0].set_ylabel("delay increase from the same\nradio degradation [ms, log]")
    fig.suptitle("F5  The load balancer cannot see radio degradation "
                 "(two independent measurements, not comparable to each other)",
                 x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    return st.save(fig, os.path.join(EXP, "figures", a.profile),
                   f"F5_envoy_blindness{'' if a.scale == 1 else f'_x{a.scale:g}'}")


if __name__ == "__main__":
    pdf, _ = main()
    print(f"-> {pdf}")

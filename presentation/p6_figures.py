#!/usr/bin/env python3
"""P6 발표용 그림 5장 (지시서 v11 §6). 읽기 전용 — tables/ 의 재계산 결과를 그린다.

라벨은 전부 영어. 한글 폰트(Noto Sans CJK)는 사용 가능하고 글리프 누락 0 으로
검증했지만, §6.2 가 그림2 정책 라벨을 영어로 지정했으므로 혼용을 피해 통일했다.

규격: 제목 >=16pt, 축라벨 14pt, 눈금 >=12pt, 선굵기 >=2, 마커 >=6, dpi 200, 흰 배경.
팔레트: 검증된 3슬롯 (validate_palette.js, all-pairs 통과)
        S1 blue #2a78d6 / S2 orange #eb6834 / S3 aqua #1baf7a
"""
import csv
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

TAB = os.path.expanduser("~/exp/presentation/tables")
FIG = os.path.expanduser("~/exp/presentation/figures")
os.makedirs(FIG, exist_ok=True)

S1C, S2C, S3C = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#a8a7a0"
plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
    "font.size": 13, "axes.titlesize": 17, "axes.labelsize": 14,
    "xtick.labelsize": 12.5, "ytick.labelsize": 12.5, "legend.fontsize": 12.5,
    "text.color": INK, "axes.labelcolor": INK2, "axes.edgecolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": "#e5e4e0", "grid.linewidth": 0.7,
    "axes.spines.top": False, "axes.spines.right": False,
    "lines.linewidth": 2.4, "lines.markersize": 7, "figure.dpi": 200,
})


def rd(name):
    return list(csv.DictReader(open(os.path.join(TAB, name))))


# ---------------------------------------------------------------- p1 구성도
def fig1():
    fig, ax = plt.subplots(figsize=(12, 6.4))
    ax.set_xlim(0, 100); ax.set_ylim(0, 64); ax.axis("off")
    ax.grid(False)

    def box(x, y, w, h, title, lines, fc="#f4f4f2", ec=INK2, tc=INK):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6",
                                    facecolor=fc, edgecolor=ec, linewidth=1.6))
        ax.text(x + w / 2, y + h - 2.6, title, ha="center", va="top",
                fontsize=13, fontweight="bold", color=tc)
        # 본문을 남은 높이에 균등 배치해 박스를 넘지 않게 한다
        top = y + h - 6.6
        avail = top - (y + 1.8)
        step = min(3.1, avail / max(len(lines) - 1, 1)) if len(lines) > 1 else 0
        for i, ln in enumerate(lines):
            ax.text(x + w / 2, top - i * step, ln, ha="center", va="top",
                    fontsize=10.4, color=INK2)

    def arrow(x1, y1, x2, y2, color=INK2, style="-|>", lw=2.0, ls="-"):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                                     mutation_scale=17, color=color, linewidth=lw,
                                     linestyle=ls, shrinkA=2, shrinkB=2))

    box(1, 30, 21, 27, "Load generator + RAN",
        ["PC3  192.168.0.12", "i5-14400F, 16 cores", "UERANSIM v3.3.0", "gNB + 2 UE",
         "uesimtun0 10.46.0.6 (c1)", "uesimtun1 10.46.0.7 (c2)"], fc="#eef4fc")
    box(29, 30, 22, 27, "5G Core + LB + Ctrl",
        ["PC5  192.168.0.43", "i7-3770, 8 cores", "Open5GS 2.8.0 (ogstun)",
         "Envoy v1.39.0 (:8080)", "sorts_ctl.py (T_ctrl 1 s)"], fc="#fdf0e9")
    box(60, 44, 37, 15, "S1  Edge  192.168.0.3",
        ["DSB HR 24 containers - cpuset 0 (1 core)", "d_net 2 ms - capacity 400 rps"],
        fc="#eef4fc", ec=S1C)
    box(60, 27, 37, 15, "S2  Regional  192.168.0.2",
        ["DSB HR 24 containers - cpuset 0-1 (2 cores)", "d_net 15 ms - capacity 800 rps"],
        fc="#fdf0e9", ec=S2C)
    box(60, 10, 37, 15, "S3  Central  192.168.0.40",
        ["DSB HR 24 containers - cpuset 0-3 (4 cores)", "d_net 25 ms - capacity 1600 rps"],
        fc="#e9f7f1", ec=S3C)

    arrow(22, 43.5, 29, 43.5)
    ax.text(25.5, 45.4, "N3 GTP", ha="center", fontsize=10.5, color=INK2)
    for yb, c in ((51.5, S1C), (34.5, S2C), (17.5, S3C)):
        arrow(51, 43.5, 60, yb, color=c)
    ax.text(53.0, 52.5, "HTTP", ha="center", fontsize=10.5, color=INK2)

    # 주입 지점
    ax.add_patch(FancyBboxPatch((25, 17.5), 30, 9.5, boxstyle="round,pad=0.5",
                                facecolor="#fff6e5", edgecolor="#c98500", linewidth=1.8))
    ax.text(40, 25.4, "1  Radio impairment", ha="center", fontsize=11.8,
            fontweight="bold", color="#8a5c00")
    ax.text(40, 21.2, "tc netem rate on ogstun, per connection\n"
                      "bands 20 / 4.5 / 2.3 / 1.6 Mbit/s", ha="center", va="center",
            fontsize=10.3, color="#8a5c00")
    arrow(40, 27.0, 40, 30, color="#c98500", lw=2.2)

    ax.add_patch(FancyBboxPatch((25, 4), 30, 9.5, boxstyle="round,pad=0.5",
                                facecolor="#f0eefc", edgecolor="#4a3aa7", linewidth=1.8))
    ax.text(40, 11.9, "2  Path delay", ha="center", fontsize=11.8,
            fontweight="bold", color="#4a3aa7")
    ax.text(40, 7.7, "tc netem delay on eno1 egress\nper destination site (2 / 15 / 25 ms)",
            ha="center", va="center", fontsize=10.3, color="#4a3aa7")
    arrow(55, 11.5, 56.5, 40.5, color="#4a3aa7", lw=2.2, ls=(0, (5, 3)))
    ax.text(57.5, 27, "applies to\nall three\nsite paths", ha="left", fontsize=9.8,
            color="#4a3aa7")

    ax.text(50, 62.5, "SORTS testbed - data path and impairment injection points",
            ha="center", fontsize=17, fontweight="bold", color=INK)
    ax.text(1, 1.0, "3  Server impairment (stress-ng): applied locally on a site cpuset, not on the network path.", fontsize=10.5, color=MUTED)
    fig.tight_layout()
    fig.savefig(f"{FIG}/p1_architecture.png", bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------- p2 위반율
def fig2():
    rows = rd("p4_t1_violation_by_policy.csv")
    order = ["SORTS", "Round Robin", "Least Request", "Static-Far (no reaction)"]
    rows.sort(key=lambda r: order.index(r["policy"]))
    vals = [float(r["violation_pct"]) for r in rows]
    fig, ax = plt.subplots(figsize=(9.5, 5.6))
    cols = [S1C if r["policy"] == "SORTS" else MUTED for r in rows]
    bars = ax.bar(range(4), vals, width=0.56, color=cols, zorder=3)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 2.2, f"{v:.2f}%", ha="center",
                va="bottom", fontsize=14, fontweight="bold", color=INK)
    ax.set_xticks(range(4))
    ax.set_xticklabels([r["policy"].replace(" (no reaction)", "\n(no reaction)")
                        for r in rows], fontsize=12.5)
    ax.set_ylabel("SLO violation rate (%)")
    ax.set_ylim(0, 112)
    ax.set_title("Cohort-1 search SLO violations under radio impairment\n"
                 "(Poor band 2.3 Mbit/s, during window, n≈15,300 per policy)", fontsize=16)
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    fig.savefig(f"{FIG}/p2_violation_by_policy.png")
    plt.close(fig)
    with open(f"{FIG}/p2_violation_by_policy_data.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["policy", "n", "violations", "violation_pct"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in w.fieldnames})


# ---------------------------------------------------------------- p3 6유닛
def fig3():
    rows = rd("p4_t3_six_units.csv")
    order = ["c1_reserve", "c1_search", "c1_recommend",
             "c2_reserve", "c2_search", "c2_recommend"]
    rows.sort(key=lambda r: order.index(r["unit"]))
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.4),
                                  gridspec_kw={"width_ratios": [1.45, 1]})
    y = range(6)
    lbl = [r["unit"].replace("c1_", "Cohort 1 / ").replace("c2_", "Cohort 2 / ")
           for r in rows]
    for i, r in enumerate(rows):
        left = 0
        for key, c, nm in (("S1_pct", S1C, "S1"), ("S2_pct", S2C, "S2"), ("S3_pct", S3C, "S3")):
            v = float(r[key])
            if v > 0:
                ax.barh(i, v, left=left, height=0.6, color=c, zorder=3,
                        label=nm if i == 0 or (nm == "S2" and r["unit"] == "c1_search") else None)
                ax.text(left + v / 2, i, nm, ha="center", va="center", fontsize=12,
                        color="white", fontweight="bold")
                left += v
    ax.set_yticks(list(y)); ax.set_yticklabels(lbl, fontsize=12.5)
    ax.invert_yaxis(); ax.set_xlim(0, 100)
    ax.set_xlabel("Traffic distribution (%)")
    ax.set_title("Placement of the 6 routing units", fontsize=16)
    ax.grid(axis="y", visible=False)
    idx = order.index("c1_search")
    ax.add_patch(plt.Rectangle((-1.5, idx - 0.42), 103, 0.84, fill=False,
                               edgecolor="#e34948", linewidth=2.8, zorder=5))
    ax.text(101, idx, "  moved", va="center", fontsize=12.5, color="#e34948",
            fontweight="bold")

    vals = [float(r["violation_pct"]) for r in rows]
    cols = ["#e34948" if r["unit"] == "c1_search" else MUTED for r in rows]
    ax2.barh(list(y), vals, height=0.6, color=cols, zorder=3)
    for i, v in enumerate(vals):
        ax2.text(v + 0.012, i, f"{v:.3f}%", va="center", fontsize=12, color=INK)
    ax2.set_yticks(list(y)); ax2.set_yticklabels([])
    ax2.invert_yaxis()
    ax2.set_xlim(0, max(vals) * 1.45)
    ax2.set_xlabel("SLO violation rate (%)")
    ax2.set_title("Violation rate per unit", fontsize=16)
    ax2.grid(axis="y", visible=False)
    fig.suptitle("SORTS under radio impairment: only 1 of 6 units is relocated",
                 fontsize=17, fontweight="bold", y=1.005)
    fig.tight_layout()
    fig.savefig(f"{FIG}/p3_six_units.png", bbox_inches="tight")
    plt.close(fig)
    with open(f"{FIG}/p3_six_units_data.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["unit", "n", "S1_pct", "S2_pct", "S3_pct",
                                          "violation_pct", "corrected_p95"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in w.fieldnames})


# ---------------------------------------------------------------- p4 램프 펄스
def fig4():
    RUNS = os.path.expanduser("~/exp/runs/demo-20260805/D6_sorts_ramp")
    meta = json.load(open(f"{RUNS}/meta.json"))
    t0 = meta["t_meas"] - meta["clock"]["d12_s"] + meta["clock"]["d43_s"]
    dec = [r for r in csv.DictReader(open(f"{RUNS}/decisions.csv"))
           if r["cohort"] == "c1" and r["class"] == "search"]
    ts = [float(r["ts"]) - t0 for r in dec]
    rate = [float(r["observed_rate_kbit"]) / 1000.0 if r["observed_rate_kbit"] else float("nan")
            for r in dec]
    tl = list(csv.DictReader(open(os.path.expanduser(
        "~/exp/analysis/demo/tables/t4_3_distribution_timeline_D6_sorts_ramp.csv"))))
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6.6), sharex=True,
                                 gridspec_kw={"height_ratios": [1, 1.35], "hspace": 0.13})
    a1.step(ts, rate, where="post", color=INK2, linewidth=2.6)
    a1.set_ylabel("Achievable rate\n(Mbit/s)")
    a1.set_ylim(0, 22)
    a1.set_title("Ramp: link degradation drives two placement transitions", fontsize=17)
    a1.text(0.015, 0.12, "gaps = no shaping (unlimited)", transform=a1.transAxes,
            fontsize=11, color=MUTED)
    for s, c in (("S1", S1C), ("S2", S2C), ("S3", S3C)):
        a2.plot([int(r["t"]) for r in tl], [float(r[f"{s}%"]) for r in tl],
                color=c, linewidth=2.6, label=f"{s}")
    a2.set_ylabel("Cohort-1 search\ndistribution (%)")
    a2.set_xlabel("Time relative to measurement start (s)")
    a2.set_ylim(-6, 108)
    a2.legend(loc="center left", frameon=False, ncol=3)
    for t_, lab, dx, ty in ((160.3, "→ S2  (3.27 Mbit/s)", -8, 70),
                            (171.3, "→ S1  (1.60 Mbit/s)", 6, 44),
                            (300.3, "→ S3  (released)", 6, 70)):
        a2.axvline(t_, color=MUTED, linewidth=1.3, linestyle="--", zorder=1)
        a2.text(t_ + dx, ty, lab, fontsize=11.5, color=INK2,
                ha="right" if dx < 0 else "left")
    a2.annotate("", xy=(172, 88), xytext=(299, 88),
                arrowprops=dict(arrowstyle="<->", color=S1C, lw=2.2))
    a2.text(236, 79, "edge (S1) occupancy pulse", ha="center", fontsize=12.5,
            color=S1C, fontweight="bold")
    fig.tight_layout()
    fig.savefig(f"{FIG}/p4_s1_pulse.png")
    plt.close(fig)
    with open(f"{FIG}/p4_s1_pulse_data.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_rel_s", "rate_mbit"])
        for t_, v in zip(ts, rate):
            w.writerow([round(t_, 3), v])


# ---------------------------------------------------------------- p5 잔여위반
def fig5():
    rows = rd("p4_t4b_1s_buckets.csv")
    t4 = rd("p4_t4_residual.csv")[0]
    xs = [int(r["t_rel_inject_s"]) for r in rows]
    vs = [float(r["violation_pct"]) for r in rows]
    fig, ax = plt.subplots(figsize=(10.5, 5.6))
    cols = ["#e34948" if v > 1 else MUTED for v in vs]
    bars = ax.bar(xs, vs, width=0.74, color=cols, zorder=3)
    for x, v in zip(xs, vs):
        if v > 1:
            ax.text(x, v + 1.6, f"{v:.1f}%", ha="center", fontsize=12.5,
                    fontweight="bold", color=INK)
    ax.axvline(-0.5, color=INK, linewidth=2.2, zorder=4)
    ax.annotate("impairment injected", xy=(-0.5, 57), xytext=(-3.6, 57),
                fontsize=12.5, color=INK, va="center", ha="left",
                arrowprops=dict(arrowstyle="->", color=INK, lw=1.6))
    ax.set_xlabel("Time relative to impairment injection (s)")
    ax.set_ylabel("SLO violation rate (%)")
    ax.set_ylim(0, 60)
    ax.set_xticks(xs)
    ax.set_title("Residual violations are confined to the reaction window\n"
                 f"detection {float(t4['detect_delay_s'])*1000:.0f} ms + apply "
                 f"{float(t4['apply_latency_ms']):.2f} ms  →  "
                 f"{t4['gap_violations']}/{t4['during_violations']} "
                 f"({float(t4['gap_share_of_during_pct']):.1f}%) of all during-window violations",
                 fontsize=15.5)
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    fig.savefig(f"{FIG}/p5_residual.png")
    plt.close(fig)
    with open(f"{FIG}/p5_residual_data.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)


for fn in (fig1, fig2, fig3, fig4, fig5):
    fn()
    print("OK", fn.__name__)
print("->", FIG)

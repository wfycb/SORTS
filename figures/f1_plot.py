#!/usr/bin/env python3
"""F1 — 시계열 4단 그림 (CSV → PDF/PNG). 원자료는 f1_extract.py 가 이미 뽑았다.

방출물 (--load 450 기준, 프로파일별로 파일명 접미사 없음 — outdir 로 구분):
  F1_timeseries            주 그림: 4단, x=시간[s], 지연=롤링 p50+p95 밴드
  F1_timeseries_reqidx     같은 그림, x=요청 순번
  F1_timeseries_scatter    2단을 요청 단위 산점도로 교체한 변형
  F1_sites_by_policy       3단(사이트 분배)의 **정책 3열 비교** 변형
  F1b_L800_saturated       (--load 800) bl_loc_pri 분리 패널 — corrected 기준
사용: python3 f1_plot.py --load 450 --profile talk
"""
import argparse
import csv
import os
from collections import defaultdict

import style as st
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

EXP = "/home/user/exp"
DATA = os.path.join(EXP, "figures/data")
SLO_SEARCH = 45.0
POLS = ["SORTS", "bl_lr", "bl_loc_pri"]
# both 창 위반율 [%] — docs/NUMBERS.md §5.5 (arm 평균, n=2; L800 loc_pri 는 n=1)
VIOL = {450: {"SORTS": 0.420, "bl_lr": 7.407, "bl_loc_pri": 4.162},
        800: {"SORTS": 6.340, "bl_lr": 10.614, "bl_loc_pri": 100.000}}


def read(name, load):
    p = os.path.join(DATA, f"f1_L{load}_{name}.csv")
    with open(p) as f:
        return list(csv.DictReader(f))


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def marks_of(rows, policy="SORTS"):
    return {r["mark"]: (float(r["t_rel_s"]), r["label"])
            for r in rows if r["policy"] == policy}


def draw_marks(ax, mk, shade=True, label=False):
    c1, both, clear = mk["c1_extreme"][0], mk["c2_extreme"][0], mk["clear_all"][0]
    if shade:
        ax.axvspan(c1, both, color="#999999", alpha=0.10, lw=0)
        ax.axvspan(both, clear, color="#999999", alpha=0.20, lw=0)
    for x in (c1, both, clear):
        ax.axvline(x, color=st.ANNOT, ls="-", lw=1.0, alpha=0.7)
    if label:
        y = ax.get_ylim()[1]
        for x, t in ((c1, "c1 1600k"), (both, "both 1600k"), (clear, "clear")):
            ax.annotate(t, xy=(x, y), xytext=(3, -2), textcoords="offset points",
                        va="top", ha="left", fontsize=plt.rcParams["legend.fontsize"],
                        color=st.ANNOT)


def panel_band(ax, slack, mk):
    """1단 — 컨트롤러가 관측한 밴드(kbit), 코호트별. 미셰이핑은 'no shaping'."""
    for coh, ls, lw_f in (("c1", "-", 1.0), ("c2", (0, (5, 2)), 0.85)):
        rows = sorted((r for r in slack if r["cohort"] == coh),
                      key=lambda r: float(r["t_rel_s"]))
        xs = [float(r["t_rel_s"]) for r in rows]
        ys = [fnum(r["observed_rate_kbit"]) or 20000.0 for r in rows]
        ax.step(xs, ys, where="post", color="#333333", ls=ls,
                lw=plt.rcParams["lines.linewidth"] * lw_f,
                label=f"cohort {coh[-1]}")
    ax.legend(loc="lower left", ncol=2, frameon=False,
              fontsize=plt.rcParams["legend.fontsize"])
    ax.set_yscale("log")
    ax.set_yticks([1600, 20000])
    ax.set_yticklabels(["1600", "no shaping"])
    ax.set_ylabel("radio band\n[kbit/s]")
    ax.set_ylim(1000, 40000)
    draw_marks(ax, mk, label=True)


def panel_latency(ax, lat, mk, col="service", xkey="t_rel_s", ymax=60.0):
    """2단 — 정책 3종 롤링 p50 + p95 밴드 + SLO 선."""
    by = defaultdict(list)
    for r in lat:
        by[r["policy"]].append(r)
    for pol in POLS:
        if not by[pol]:
            continue
        rows = sorted(by[pol], key=lambda r: float(r[xkey]))
        x = [float(r[xkey]) for r in rows]
        p50 = [fnum(r[f"{col}_p50"]) for r in rows]
        p95 = [fnum(r[f"{col}_p95"]) for r in rows]
        s = st.POLICY[pol]
        ax.plot(x, p50, color=s["color"], ls=s["ls"], label=st.POLICY_LABEL[pol])
        ax.fill_between(x, p50, p95, color=s["color"], alpha=0.13, lw=0)
    ax.axhline(SLO_SEARCH, color=st.SLO_COLOR, ls=(0, (6, 3)), lw=1.4)
    ax.annotate(f"SLO {SLO_SEARCH:.0f} ms", xy=(0.995, SLO_SEARCH),
                xycoords=("axes fraction", "data"), ha="right", va="bottom",
                fontsize=plt.rcParams["legend.fontsize"])
    ax.set_ylabel(f"search latency\n{col} [ms]")
    ax.set_ylim(0, ymax)
    # 잘린 꼬리는 반드시 표시한다 (잘라놓고 말하지 않으면 오독된다)
    clipped = [(float(r[xkey]), fnum(r[f"{col}_p95"]), r["policy"])
               for r in lat if (fnum(r[f"{col}_p95"]) or 0) > ymax]
    if clipped:
        t, v, pol = max(clipped, key=lambda z: z[1])
        ax.annotate(f"{st.POLICY_LABEL[pol].split(' ')[0]} p95 spikes to "
                    f"~{v:.0f} ms (clipped)",
                    xy=(t, ymax * 0.985), xytext=(max(t - 105, 5), ymax * 0.60),
                    color=st.POLICY[pol]["color"],
                    fontsize=plt.rcParams["legend.fontsize"],
                    arrowprops=dict(arrowstyle="->", color=st.POLICY[pol]["color"],
                                    lw=1.4))
    ax.set_title("line = p50, shading = p50…p95 (1 s buckets)", loc="right",
                 fontsize=plt.rcParams["legend.fontsize"], color=st.ANNOT)
    if xkey == "t_rel_s":
        draw_marks(ax, mk)
    ax.legend(loc="upper left", ncol=3, frameon=False)


def panel_latency_scatter(ax, pts, mk, col="service_ms"):
    for pol in POLS:
        rows = [r for r in pts if r["policy"] == pol and r["ok"] == "1"]
        s = st.POLICY[pol]
        ax.scatter([float(r["t_rel_s"]) for r in rows],
                   [float(r[col]) for r in rows],
                   s=2.5, alpha=0.30, color=s["color"], lw=0,
                   label=st.POLICY_LABEL[pol])
    ax.axhline(SLO_SEARCH, color=st.SLO_COLOR, ls=(0, (6, 3)), lw=1.4)
    ax.set_ylabel("search latency\nservice [ms]\n(per request)")
    draw_marks(ax, mk)
    leg = ax.legend(loc="upper left", ncol=3, frameon=False, markerscale=4)
    for h in leg.legendHandles:
        h.set_alpha(1.0)


def panel_sites(ax, sites, mk, policy="SORTS", ylabel=None, annotate=True):
    rows = sorted((r for r in sites if r["policy"] == policy),
                  key=lambda r: float(r["t_rel_s"]))
    x = [float(r["t_rel_s"]) for r in rows]
    ys = [[float(r[f"{s}_pct"]) for r in rows] for s in ("S1", "S2", "S3")]
    ax.stackplot(x, *ys,
                 colors=[st.SITE[s]["color"] for s in ("S1", "S2", "S3")],
                 labels=[st.SITE_LABEL[s] for s in ("S1", "S2", "S3")], alpha=0.85)
    ax.set_ylim(0, 100)
    ax.set_ylabel(f"site share\n[%] ({policy})" if ylabel is None else ylabel)
    draw_marks(ax, mk, shade=False)
    # 스택 안에 사이트 라벨을 직접 얹는다 (범례 상자가 S1 영역을 가리지 않게)
    if policy == "SORTS" and annotate:
        pre = [r for r in rows if float(r["t_rel_s"]) < 100]
        acc = 0.0
        for site in ("S1", "S2", "S3"):
            v = sum(float(r[f"{site}_pct"]) for r in pre) / max(len(pre), 1)
            if v > 6:
                ax.text(50, acc + v / 2, site, ha="center", va="center",
                        fontweight="bold", color="#222222")
            acc += v
        dur = [r for r in rows if 185 < float(r["t_rel_s"]) < 238]
        s1 = sum(float(r["S1_pct"]) for r in dur) / max(len(dur), 1)
        ax.annotate(f"edge (S1) enters only while the band is degraded\n"
                    f"S1 share  0 % (pre)  →  {s1:.0f} % (both cohorts 1600 k)",
                    xy=(212, s1), xytext=(150, 62), color="#222222",
                    fontsize=plt.rcParams["legend.fontsize"],
                    arrowprops=dict(arrowstyle="->", color="#222222", lw=1.2))


def panel_slack(ax, slack, mk, cohort="c1"):
    rows = sorted(slack, key=lambda r: float(r["t_rel_s"]))
    x = [float(r["t_rel_s"]) for r in rows]
    for s in ("S1", "S2", "S3"):
        ax.plot(x, [float(r[f"slack_{s}"]) for r in rows],
                color=st.SITE[s]["color"], ls=st.SITE[s]["ls"], label=st.SITE_LABEL[s])
    ax.axhline(0.0, color=st.SLO_COLOR, lw=1.2)
    ax.set_ylabel(f"slack [ms]\n(SORTS only, {cohort})")
    ax.set_title("slack = SLO − GB − d_net − f_c − d_acc   "
                 "(SORTS only — baselines have no such state;  "
                 "colors as above: S1 orange, S2 blue, S3 pink)",
                 loc="left", fontsize=plt.rcParams["legend.fontsize"], color=st.ANNOT)
    draw_marks(ax, mk)
    lo = min(float(r[f"slack_{s}"]) for r in rows for s in ("S1", "S2", "S3"))
    hi = max(float(r[f"slack_{s}"]) for r in rows for s in ("S1", "S2", "S3"))
    ax.set_ylim(lo - 0.15 * (hi - lo), hi + 0.20 * (hi - lo))
    # 선 시작점에 직접 라벨 (범례 상자가 데이터를 가리지 않게)
    first = rows[0]
    for s in ("S1", "S2", "S3"):
        ax.annotate(s, xy=(float(first["t_rel_s"]), float(first[f"slack_{s}"])),
                    xytext=(3, 3), textcoords="offset points", va="bottom",
                    ha="left", color=st.SITE[s]["color"], fontweight="bold",
                    fontsize=plt.rcParams["legend.fontsize"])
    ax.annotate("under the band S2/S3 < 0 → SLO unreachable there",
                xy=(3, lo - 0.10 * (hi - lo)), va="bottom", ha="left",
                color=st.ANNOT, fontsize=plt.rcParams["legend.fontsize"])


def fig_main(load, prof, outdir, xkey="t_rel_s", scatter=False,
             ymax=60.0, cohort="c1", suffix=""):
    lat, pts = read("latency", load), read("points", load)
    sites, slack = read("sites", load), read("slack", load)
    mk = marks_of(read("marks", load))
    fig, axes = plt.subplots(4, 1, figsize=prof["figsize_4panel"], sharex=True,
                             gridspec_kw={"height_ratios": [0.7, 1.5, 1.0, 1.1]})
    panel_band(axes[0], slack, mk)
    if scatter:
        panel_latency_scatter(axes[1], pts, mk)
    else:
        panel_latency(axes[1], lat, mk, xkey=xkey, ymax=ymax)
    panel_sites(axes[2], sites, mk)
    panel_slack(axes[3], [r for r in slack if r["cohort"] == cohort], mk,
                cohort=cohort)
    axes[-1].set_xlabel("time since measurement start [s]"
                        if xkey == "t_rel_s" else "request index")
    if xkey == "t_rel_s":
        axes[-1].set_xlim(0, 300)
    fig.align_ylabels(axes)
    fig.suptitle(f"F1  Radio degradation → routing response  "
                 f"(L = {load} rps, seq_extreme 1600 kbit, 2 cohorts, hc_off)",
                 x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    name = ("F1_timeseries_scatter" if scatter else
            "F1_timeseries" if xkey == "t_rel_s" else "F1_timeseries_reqidx")
    return st.save(fig, outdir, f"{name}_L{load}{suffix}")


def fig_sites_by_policy(load, prof, outdir, suffix=""):
    sites = read("sites", load)
    mk = marks_of(read("marks", load))
    w, h = prof["figsize_1panel"]
    fig, axes = plt.subplots(1, 3, figsize=(w, h * 0.95), sharey=True)
    for ax, pol in zip(axes, POLS):
        panel_sites(ax, sites, marks_of(read("marks", load), pol), policy=pol,
                    ylabel="site share [%]" if pol == POLS[0] else "",
                    annotate=False)
        ax.set_title(st.POLICY_LABEL[pol], fontsize=plt.rcParams["axes.titlesize"])
        ax.set_xlabel("time [s]")
        ax.set_xlim(0, 300)
        ax.set_xticks([0, 120, 180, 240, 300])
        share = sum(float(r["S1_pct"]) for r in sites
                    if r["policy"] == pol and 185 < float(r["t_rel_s"]) < 238)
        n = sum(1 for r in sites if r["policy"] == pol and 185 < float(r["t_rel_s"]) < 238)
        pre = sum(float(r["S1_pct"]) for r in sites
                  if r["policy"] == pol and float(r["t_rel_s"]) < 115)
        npre = sum(1 for r in sites if r["policy"] == pol and float(r["t_rel_s"]) < 115)
        viol = VIOL[load][pol]
        ax.set_xlabel(f"time [s]\nS1 share: {pre/max(npre,1):.0f} % → "
                      f"{share/max(n,1):.0f} % under band\n"
                      f"both-window violation: {viol:.2f} %")
    handles = [Patch(facecolor=st.SITE[s]["color"], label=st.SITE_LABEL[s])
               for s in ("S1", "S2", "S3")]
    axes[0].legend(handles=handles, loc="lower left", frameon=False, ncol=1)
    fig.suptitle(f"F1c  Who actually moves?  site share per policy "
                 f"(L = {load} rps, band 1600 kbit at t=120/180 s)",
                 x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    return st.save(fig, outdir, f"F1c_sites_by_policy_L{load}{suffix}")


def fig_saturated(load, prof, outdir, suffix=""):
    """L=800 보조 — bl_loc_pri 를 별도 패널로 분리(corrected 기준)."""
    lat = read("latency", load)
    mk = marks_of(read("marks", load))
    fig, axes = plt.subplots(2, 1, figsize=prof["figsize_4panel"], sharex=True,
                             gridspec_kw={"height_ratios": [1, 1]})
    # 위: SORTS vs bl_lr (service, 선형축)
    panel_latency(axes[0], [r for r in lat if r["policy"] != "bl_loc_pri"], mk)
    axes[0].set_title("SORTS vs bl_lr — service latency (linear axis)",
                      loc="left", fontsize=plt.rcParams["legend.fontsize"],
                      color=st.ANNOT)
    # 아래: bl_loc_pri (corrected, 로그축)
    rows = sorted((r for r in lat if r["policy"] == "bl_loc_pri"),
                  key=lambda r: float(r["t_rel_s"]))
    x = [float(r["t_rel_s"]) for r in rows]
    s = st.POLICY["bl_loc_pri"]
    axes[1].plot(x, [fnum(r["corrected_p50"]) for r in rows], color=s["color"],
                 ls=s["ls"], label="corrected p50  (wait since due — includes generator backlog)")
    axes[1].plot(x, [fnum(r["service_p50"]) for r in rows], color=s["color"],
                 ls="-", alpha=0.55, label="service p50  (actual server work)")
    axes[1].axhline(SLO_SEARCH, color=st.SLO_COLOR, ls=(0, (6, 3)), lw=1.4)
    axes[1].set_yscale("log")
    axes[1].set_ylabel("bl_loc_pri latency [ms]\n(log scale)")
    axes[1].legend(loc="center left", frameon=False)
    draw_marks(axes[1], mk)
    axes[1].annotate(
        "NOT a 65 s server response.\n"
        "  service  = server work per request → p50 99.7 ms (both window)\n"
        "  corrected = wait since the request was DUE → p50 65 s\n"
        "The gap is the load generator's own queue: offered load exceeds\n"
        "capacity, so scheduled requests pile up (I-19).",
        xy=(0.30, 0.62), xycoords="axes fraction", va="top",
        fontsize=plt.rcParams["legend.fontsize"], color=st.ANNOT,
        bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="#bbbbbb", alpha=0.9))
    axes[1].set_xlabel("time since measurement start [s]")
    axes[1].set_xlim(0, 300)
    fig.suptitle("F1b  At L = 800 rps the locality-first baseline is broken "
                 "before the radio degrades", x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    return st.save(fig, outdir, f"F1b_L800_saturated{suffix}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", type=int, default=450)
    ap.add_argument("--profile", default="talk", choices=["talk", "paper"])
    ap.add_argument("--scale", type=float, default=1.0,
                    help="발표장 확대율 (1.2 / 1.5). 파일명에 _x1.2 접미사")
    a = ap.parse_args()
    prof = st.apply(a.profile, a.scale)
    sfx = "" if a.scale == 1.0 else f"_x{a.scale:g}"
    outdir = os.path.join(EXP, "figures", a.profile)
    made = []
    made.append(fig_main(a.load, prof, outdir, suffix=sfx))                 # 주 그림 (0~60)
    made.append(fig_main(a.load, prof, outdir, ymax=115.0,
                         suffix=f"_full{sfx}"))                             # 백업 (0~115)
    made.append(fig_main(a.load, prof, outdir, cohort="c2",
                         suffix=f"_slackc2{sfx}"))                          # 예비 (c2 slack)
    made.append(fig_main(a.load, prof, outdir, xkey="req_idx_start", suffix=sfx))
    made.append(fig_main(a.load, prof, outdir, scatter=True, suffix=sfx))
    made.append(fig_sites_by_policy(a.load, prof, outdir, suffix=sfx))
    if a.load == 800:
        made.append(fig_saturated(a.load, prof, outdir, suffix=sfx))
    for pdf, png in made:
        print(f"-> {os.path.relpath(pdf, EXP)} / {os.path.basename(png)}")


if __name__ == "__main__":
    main()

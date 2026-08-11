#!/usr/bin/env python3
"""v12 STEP B 분석. 표 B1/B2 + 그림 b1/b2. 원자료에서 계산."""
import csv, glob, gzip, json, os, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

RUNS = os.path.expanduser("~/exp/runs/edge-20260805")
TAB = os.path.expanduser("~/exp/presentation/tables")
FIG = os.path.expanduser("~/exp/presentation/figures")
SITE = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
C1, C2 = "10.46.0.6", "10.46.0.7"
EP = {"/hotels": "search", "/reservation": "reserve", "/recommendations": "recommend"}
SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
EXPECT = {"reserve": 36, "search": 4474, "recommend": 200}
CAP = 400.0
LAB = {"E1_sorts_far": "SORTS (far-first)", "E2_sorts_near": "SORTS-NearFirst",
       "E3_lr": "Least Request"}
COL = {"E1_sorts_far": "#2a78d6", "E2_sorts_near": "#eb6834", "E3_lr": "#1baf7a"}

_f = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
font_manager.fontManager.addfont(_f)
plt.rcParams.update({
    "font.family": font_manager.FontProperties(fname=_f).get_name(),
    "axes.unicode_minus": False, "figure.facecolor": "white", "axes.facecolor": "white",
    "savefig.facecolor": "white", "font.size": 13, "axes.titlesize": 16.5,
    "axes.labelsize": 14, "xtick.labelsize": 12.5, "ytick.labelsize": 12.5,
    "legend.fontsize": 12.5, "text.color": "#0b0b0b", "axes.labelcolor": "#52514e",
    "axes.edgecolor": "#52514e", "xtick.color": "#52514e", "ytick.color": "#52514e",
    "axes.grid": True, "grid.color": "#e5e4e0", "grid.linewidth": 0.7,
    "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 200})


def meta(rid):
    return json.load(open(f"{RUNS}/{rid}/meta.json"))


def elog(rid):
    out = []
    with gzip.open(f"{RUNS}/{rid}/envoy_access.log.gz", "rt", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 15 or p[12] not in (C1, C2):
                continue
            s = SITE.get(p[10].split(":")[0]); e = EP.get(p[3].split("?")[0])
            if s and e:
                out.append((float(p[0]), p[12], e, s))
    return out


def load(rid):
    rows = []
    for c in (1, 2):
        p = f"{RUNS}/{rid}/load_c{c}.csv"
        if os.path.exists(p):
            for r in csv.DictReader(open(p)):
                if r["warmup"] == "0":
                    r["cohort"] = c
                    rows.append(r)
    return rows


def viol(r):
    ok = (r["status"] == "200" and
          abs(int(r["bytes_recv"]) - EXPECT[r["ep"]]) <=
          (EXPECT[r["ep"]] * 0.10 if EXPECT[r["ep"]] > 1000 else 0))
    return (not ok) or float(r["corrected_ms"]) > SLO[r["ep"]]


rids = [d for d in ("E1_sorts_far", "E2_sorts_near", "E3_lr")
        if os.path.exists(f"{RUNS}/{d}/summary.json")]
print("분석 대상:", rids)

# ---- 구간 정의: 마크 기준 (.43 시계) ----
b1, b2, series = [], [], {}
for rid in rids:
    m = meta(rid); mk = json.load(open(f"{RUNS}/{rid}/marks.json"))["marks"]
    d12, d43 = m["clock"]["d12_s"], m["clock"]["d43_s"]
    t0_12 = m["t_meas"]; t0_43 = t0_12 - d12 + d43
    G = 2.0
    def find(w):
        return next((x for x in mk if x["what"] == w), None)
    a, b, c = find("c1_extreme"), find("c2_extreme"), find("clear_all")
    end12 = t0_12 + m["duration"]
    secs = {}
    if a and b and c:
        secs["pre"] = (t0_12, a["t_issue"] + d12 - G)
        secs["c1만"] = (a["t_done"] + d12 + G, b["t_issue"] + d12 - G)
        secs["c1+c2"] = (b["t_done"] + d12 + G, c["t_issue"] + d12 - G)
        secs["post"] = (c["t_done"] + d12 + G, end12)
    else:
        print(f"  경고 {rid}: 마크 부족 {[x['what'] for x in mk]}")
        continue
    el = elog(rid); lr = load(rid)
    for name, (lo, hi) in secs.items():
        lo43, hi43 = lo - d12 + d43, hi - d12 + d43
        sub_e = [x for x in el if lo43 <= x[0] < hi43]
        n = len(sub_e); n1 = sum(1 for x in sub_e if x[3] == "S1")
        dur = max(hi - lo, 1e-9)
        inflow = n1 / dur
        row = {"run": rid, "policy": LAB[rid], "section": name,
               "window_s": round(dur, 1), "n": n,
               "S1_share_pct": round(100 * n1 / n, 2) if n else 0,
               "S1_inflow_rps": round(inflow, 1),
               "edge_headroom_pct": round(100 * (1 - inflow / CAP), 2)}
        for coh, xff in ((1, C1), (2, C2)):
            s = [r for r in lr if r["cohort"] == coh and r["ep"] == "search"
                 and lo <= float(r["end_ts"]) < hi]
            v = sum(1 for r in s if viol(r))
            row[f"c{coh}_search_viol_pct"] = round(100 * v / len(s), 3) if s else None
            row[f"c{coh}_search_n"] = len(s)
            se = [x for x in sub_e if x[1] == xff and x[2] == "search"]
            dd = {}
            for x in se:
                dd[x[3]] = dd.get(x[3], 0) + 1
            tot = sum(dd.values()) or 1
            row[f"c{coh}_search_site"] = "/".join(
                f"{k}:{round(100*vv/tot)}%" for k, vv in sorted(dd.items(), key=lambda z: -z[1]))
        b1.append(row)
    # 1초 시계열
    buck, vb = {}, {}
    for t, xff, e, s in el:
        k = int(t - t0_43)
        d = buck.setdefault(k, {"S1": 0, "S2": 0, "S3": 0}); d[s] += 1
    for r in lr:
        if r["ep"] != "search":
            continue
        k = int(float(r["end_ts"]) - t0_12)
        d = vb.setdefault((k, r["cohort"]), [0, 0]); d[0] += 1; d[1] += viol(r)
    series[rid] = {"inflow": {k: v["S1"] for k, v in buck.items()}, "viol": vb,
                   "marks": {x["what"]: x["t_done"] + d12 - t0_12 for x in mk}}

with open(f"{TAB}/b1_edge_by_section.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(b1[0])); w.writeheader(); w.writerows(b1)

print("\n표 B1. 구간별 엣지 유입과 위반율")
print(f"{'런':18s}{'구간':8s}{'창(s)':>7s}{'S1유입':>8s}{'헤드룸%':>9s}"
      f"{'c1 위반%':>10s}{'c2 위반%':>10s}  c1_search 배치 / c2_search 배치")
for r in b1:
    print(f"{r['policy']:18s}{r['section']:8s}{r['window_s']:7.1f}{r['S1_inflow_rps']:8.1f}"
          f"{r['edge_headroom_pct']:9.2f}{str(r['c1_search_viol_pct']):>10s}"
          f"{str(r['c2_search_viol_pct']):>10s}  {r['c1_search_site']} / {r['c2_search_site']}")

# ---- 표 B2 ----
b2rows = []
for r in b1:
    if r["section"] != "c1+c2":
        continue
    b2rows.append({"run": r["run"], "policy": r["policy"],
                   "c2_search_placement": r["c2_search_site"],
                   "c2_search_viol_pct": r["c2_search_viol_pct"],
                   "c1_search_viol_pct": r["c1_search_viol_pct"],
                   "S1_total_inflow_rps": r["S1_inflow_rps"],
                   "vs_capacity_pct": round(100 * r["S1_inflow_rps"] / CAP, 1),
                   "edge_headroom_pct": r["edge_headroom_pct"]})
with open(f"{TAB}/b2_second_cohort_rescue.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(b2rows[0])); w.writeheader(); w.writerows(b2rows)
print("\n표 B2. 두 번째 코호트 구제 여부 (c1+c2 구간)")
for r in b2rows:
    print(f"  {r['policy']:18s} c2_search 배치={r['c2_search_placement']:22s} "
          f"c2 위반={r['c2_search_viol_pct']}%  c1 위반={r['c1_search_viol_pct']}%  "
          f"S1 유입={r['S1_total_inflow_rps']} rps ({r['vs_capacity_pct']}% of 400)")

# ---- 그림 b1 ----
fig, ax = plt.subplots(figsize=(11.5, 5.8))
for rid in rids:
    s = series[rid]["inflow"]
    xs = sorted(k for k in s if 1 <= k)   # t=0 버킷은 부분구간이라 제외
    ax.plot(xs, [s[k] for k in xs], color=COL[rid], linewidth=2.4, label=LAB[rid])
ax.axhline(CAP, color="#e34948", linewidth=2.4, linestyle="--", zorder=4)
ax.text(2, CAP + 18, "S1 mixed-workload capacity  400 rps", fontsize=12,
        color="#e34948", fontweight="bold")
ax.axhline(133.3, color="#8a5c00", linewidth=2.2, linestyle=(0, (6, 3)), zorder=4)
ax.text(2, 133.3 + 18, "search component of that capacity  133 rps", fontsize=12,
        color="#8a5c00", fontweight="bold")
mk0 = series[rids[0]]["marks"]
for w, lab in (("c1_extreme", "cohort 1\n→ extreme"), ("c2_extreme", "cohort 2\n→ extreme"),
               ("clear_all", "released")):
    if w in mk0:
        ax.axvline(mk0[w], color="#a8a7a0", linewidth=1.4, linestyle="--", zorder=1)
        ax.text(mk0[w] + 2.5, ax.get_ylim()[1] * 0.62, lab, fontsize=11.5, color="#52514e")
ax.set_xlabel("Time relative to measurement start (s)")
ax.set_ylabel("Edge (S1) inflow (rps)")
ax.set_title("Reclaiming the reserved edge when a second cohort degrades", fontsize=16.5)
ax.legend(loc="upper left", frameon=False)
fig.tight_layout(); fig.savefig(f"{FIG}/b1_edge_reclaim.png"); plt.close(fig)
with open(f"{FIG}/b1_edge_reclaim_data.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["run", "t_rel_s", "S1_inflow_rps"])
    for rid in rids:
        for k in sorted(series[rid]["inflow"]):
            w.writerow([rid, k, series[rid]["inflow"][k]])

# ---- 그림 b2 ----
fig, axes = plt.subplots(len(rids), 1, figsize=(11.5, 3.0 * len(rids)), sharex=True)
if len(rids) == 1:
    axes = [axes]
for a, rid in zip(axes, rids):
    vb = series[rid]["viol"]
    for coh, c, nm in ((1, "#2a78d6", "cohort 1"), (2, "#eb6834", "cohort 2")):
        xs = sorted(k for (k, ch) in vb if ch == coh and k >= 0)
        a.plot(xs, [100 * vb[(k, coh)][1] / max(vb[(k, coh)][0], 1) for k in xs],
               color=c, linewidth=2.0, label=nm)
    for w in ("c1_extreme", "c2_extreme", "clear_all"):
        if w in series[rid]["marks"]:
            a.axvline(series[rid]["marks"][w], color="#a8a7a0", linewidth=1.3, linestyle="--")
    a.set_ylabel("search violation\nrate (%)")
    a.set_title(LAB[rid], fontsize=14, loc="left")
    a.set_ylim(-5, 105)
    a.legend(loc="upper left", frameon=False, ncol=2, fontsize=11.5)
axes[-1].set_xlabel("Time relative to measurement start (s)")
fig.suptitle("Per-cohort search SLO violations", fontsize=16.5, y=1.002)
fig.tight_layout(); fig.savefig(f"{FIG}/b2_violation_timeline.png", bbox_inches="tight")
print("\n그림 -> b1_edge_reclaim.png , b2_violation_timeline.png")

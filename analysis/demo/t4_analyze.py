#!/usr/bin/env python3
"""T4 데모 분석 (지시서 v10 §5~6). 관측 기술만 한다 — 결론은 사람이 쓴다.

입력: ~/exp/runs/demo-20260805/<run_id>/{summary,meta,marks}.json,
      load_c*.csv, envoy_access.log.gz, decisions.csv(sorts 런)
출력: tables/*.csv, figures/*.png + *_data.csv
"""
import csv
import glob
import gzip
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RUNS = os.path.expanduser("~/exp/runs/demo-20260805")
OUT = os.path.expanduser("~/exp/analysis/demo")
TAB = os.path.join(OUT, "tables")
FIG = os.path.join(OUT, "figures")
os.makedirs(TAB, exist_ok=True)
os.makedirs(FIG, exist_ok=True)

SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
SITE_OF = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
C1, C2 = "10.46.0.6", "10.46.0.7"
EP_OF = {"/hotels": "search", "/reservation": "reserve", "/recommendations": "recommend"}

# 검증된 기본 팔레트 (dataviz). 사이트 고정 배정: S1=blue S2=orange S3=aqua.
COL = {"S1": "#2a78d6", "S2": "#eb6834", "S3": "#1baf7a"}
SURF, INK, INK2 = "#fcfcfb", "#0b0b0b", "#52514e"
# .ttc 컬렉션이라 family 이름 해석이 캐시에 안 잡힌다 — 파일을 직접 등록한다.
from matplotlib import font_manager
_f = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
import os as _os
if not _os.path.exists(_f):
    _f = "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"
font_manager.fontManager.addfont(_f)
_fam = font_manager.FontProperties(fname=_f).get_name()
plt.rcParams.update({
    "font.family": _fam,                 # 한글 글리프
    "axes.unicode_minus": False,
    "figure.facecolor": SURF, "axes.facecolor": SURF,
    "text.color": INK, "axes.edgecolor": INK2,
    "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": "#e5e4e0", "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 10, "figure.dpi": 150,
})


def jload(rid, name):
    return json.load(open(os.path.join(RUNS, rid, name)))


def envoy_rows(rid):
    """(t43, xff, ep, site) — 코호트 트래픽만."""
    rows = []
    with gzip.open(os.path.join(RUNS, rid, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 15:
                continue
            xff = p[12]
            if xff not in (C1, C2):
                continue
            ep = EP_OF.get(p[3].split("?")[0])
            site = SITE_OF.get(p[10].split(":")[0])
            if ep and site:
                rows.append((float(p[0]), xff, ep, site))
    return rows


def load_rows(rid):
    rows = []
    for c in (1, 2):
        p = os.path.join(RUNS, rid, f"load_c{c}.csv")
        for r in csv.DictReader(open(p)):
            if r["warmup"] != "0":
                continue
            r["cohort"] = c
            rows.append(r)
    return rows


def decisions(rid):
    p = os.path.join(RUNS, rid, "decisions.csv")
    if not os.path.exists(p):
        return []
    return list(csv.DictReader(open(p)))


ALL = ["D1_sorts_none", "D2_sorts_radio", "D3_s3_radio", "D4_rr_radio",
       "D5_lr_radio", "D6_sorts_ramp"]
LABEL = {"D2_sorts_radio": "SORTS (reactive)", "D3_s3_radio": "Static-Far (반응 없음)",
         "D4_rr_radio": "RR", "D5_lr_radio": "Least-Request"}

# ------------------------------------------------ 표 T4-2: 정책별 SLO (radio 4런)
t42 = []
for rid in ALL:
    s = jload(rid, "summary.json")
    row = {"run": rid, "policy": s["policy"], "disturb": s["disturb"]}
    for sec in ("pre", "during", "post"):
        d = s["sections"][sec]
        c1 = d["by_cohort"].get("1", {}).get("by_endpoint", {})
        row[f"{sec}_c1_search_viol%"] = round(
            100 * c1.get("search", {}).get("slo_violation_rate", 0), 3)
        row[f"{sec}_total_viol%"] = round(100 * sum(
            v["slo_violation_rate"] * v["n"] for v in d["by_endpoint"].values())
            / max(sum(v["n"] for v in d["by_endpoint"].values()), 1), 3)
    t42.append(row)
with open(f"{TAB}/t4_2_slo_by_policy.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(t42[0]))
    w.writeheader()
    w.writerows(t42)
print("표 T4-2 (정책별, c1 search corrected 위반율 %):")
for r in t42:
    print(f"  {r['run']:16s} pre={r['pre_c1_search_viol%']:7.3f}  "
          f"during={r['during_c1_search_viol%']:7.3f}  post={r['post_c1_search_viol%']:7.3f}")

# ------------------------------------------------ D2 상세: 6유닛 분배·위반
rid = "D2_sorts_radio"
meta = jload(rid, "meta.json")
d12 = meta["clock"]["d12_s"]
d43 = meta["clock"]["d43_s"]
sec12 = meta["sections_abs_12"]


def t12_to_43(t):
    return t - d12 + d43


erows = envoy_rows(rid)
unit_rows = []
s = jload(rid, "summary.json")
for secname in ("pre", "during", "post"):
    lo, hi = (t12_to_43(x) for x in sec12[secname])
    for coh, xff in (("c1", C1), ("c2", C2)):
        for ep in ("reserve", "search", "recommend"):
            sub = [r for r in erows if lo <= r[0] < hi and r[1] == xff and r[2] == ep]
            dist = {}
            for r in sub:
                dist[r[3]] = dist.get(r[3], 0) + 1
            tot = sum(dist.values()) or 1
            vi = s["sections"][secname]["by_cohort"][coh[1]]["by_endpoint"][ep]
            unit_rows.append({
                "section": secname, "unit": f"{coh}_{ep}", "n": tot,
                "S1%": round(100 * dist.get("S1", 0) / tot, 2),
                "S2%": round(100 * dist.get("S2", 0) / tot, 2),
                "S3%": round(100 * dist.get("S3", 0) / tot, 2),
                "viol%": round(100 * vi["slo_violation_rate"], 3),
                "corrected_p95": vi["corrected_p95"]})
with open(f"{TAB}/t4_2b_d2_units.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(unit_rows[0]))
    w.writeheader()
    w.writerows(unit_rows)
print("\nD2 상세 (during):")
for r in unit_rows:
    if r["section"] == "during":
        print(f"  {r['unit']:13s} S1/S2/S3 = {r['S1%']:5.1f}/{r['S2%']:5.1f}/{r['S3%']:5.1f} %"
              f"  위반={r['viol%']:6.3f}%  p95={r['corrected_p95']}")

# ------------------------------------------------ 표 T4-1: 반응 지연·잔여 위반
marks = jload(rid, "marks.json")["marks"]
on = next(m for m in marks if m["what"] == "radio_on")
inject_43 = on["t43_done"]
dec = decisions(rid)
det = next(r for r in dec if r["cohort"] == "c1" and r["class"] == "search"
           and r["changed"] == "1" and r["chosen_site"] == "S2")
det_ts = float(det["ts"])
apply_done = det_ts + float(det["apply_latency_ms"]) / 1000.0
# 위반 계수는 .12 시계 (loadgen end_ts)
inject_12 = inject_43 - d43 + d12
applied_12 = apply_done - d43 + d12
lrows = load_rows(rid)
gap = [r for r in lrows if r["cohort"] == 1 and r["ep"] == "search"
       and inject_12 <= float(r["end_ts"]) < applied_12]
gap_viol = sum(1 for r in gap if r["status"] != "200"
               or float(r["corrected_ms"]) > SLO["search"])
dur_lo, dur_hi = sec12["during"]
dur = [r for r in lrows if r["cohort"] == 1 and r["ep"] == "search"
       and dur_lo <= float(r["end_ts"]) < dur_hi]
dur_viol = sum(1 for r in dur if r["status"] != "200"
               or float(r["corrected_ms"]) > SLO["search"])
t41 = {
    "inject_t43": round(inject_43, 3),
    "detect_t43": round(det_ts, 3),
    "apply_done_t43": round(apply_done, 3),
    "detect_delay_s": round(det_ts - inject_43, 3),
    "apply_latency_ms": det["apply_latency_ms"],
    "gap_c1_search_n": len(gap),
    "gap_c1_search_viol": gap_viol,
    "during_c1_search_viol": dur_viol,
    "gap_share_of_during_viol%": round(100 * gap_viol / dur_viol, 1) if dur_viol else 0.0,
}
with open(f"{TAB}/t4_1_residual.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(t41))
    w.writeheader()
    w.writerow(t41)
print("\n표 T4-1 (반응 지연·잔여 위반):")
for k, v in t41.items():
    print(f"  {k:28s} {v}")

# ------------------------------------------------ 표 T4-3 + 그림 데이터: 분배 시계열
def unit_timeline(rid, xff, ep):
    """1초 버킷: c 코호트 ep 트래픽의 사이트 분배 (t는 t_meas 기준 상대초, .43)."""
    meta = jload(rid, "meta.json")
    t0_43 = t12_to_43_g(meta, meta["t_meas"])
    buckets = {}
    for t, x, e, site in envoy_rows(rid):
        if x != xff or e != ep:
            continue
        b = int(t - t0_43)
        d = buckets.setdefault(b, {"S1": 0, "S2": 0, "S3": 0})
        d[site] += 1
    out = []
    for b in sorted(buckets):
        d = buckets[b]
        tot = sum(d.values()) or 1
        out.append({"t": b, **{f"{s}%": round(100 * d[s] / tot, 2) for s in ("S1", "S2", "S3")},
                    "n": tot})
    return out


def t12_to_43_g(meta, t):
    return t - meta["clock"]["d12_s"] + meta["clock"]["d43_s"]


for rid2 in ("D2_sorts_radio", "D6_sorts_ramp"):
    tl = unit_timeline(rid2, C1, "search")
    with open(f"{TAB}/t4_3_distribution_timeline_{rid2}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(tl[0]))
        w.writeheader()
        w.writerows(tl)

# ------------------------------------------------ 그림 1: radio 4정책 막대
fig, ax = plt.subplots(figsize=(7, 4.2))
order = ["D2_sorts_radio", "D4_rr_radio", "D5_lr_radio", "D3_s3_radio"]
vals = [next(r for r in t42 if r["run"] == o)["during_c1_search_viol%"] for o in order]
names = [LABEL[o] for o in order]
colors = ["#2a78d6" if o == "D2_sorts_radio" else "#a8a7a0" for o in order]
bars = ax.bar(range(4), vals, width=0.55, color=colors, zorder=3)
for i, (b, v) in enumerate(zip(bars, vals)):
    ax.text(b.get_x() + b.get_width() / 2, v + max(vals) * 0.02, f"{v:.2f}%",
            ha="center", va="bottom", fontsize=10, color=INK)
ax.set_xticks(range(4))
ax.set_xticklabels(names, fontsize=9)
ax.set_ylabel("코호트1 search SLO 초과율, during (%)")
ax.set_title("radio 교란(Poor 2.3 Mbit/s) 중 코호트1 search SLO 초과율", fontsize=11)
ax.grid(axis="x", visible=False)
fig.tight_layout()
fig.savefig(f"{FIG}/fig_d1_radio_comparison.png")
with open(f"{FIG}/fig_d1_radio_comparison_data.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["policy", "during_c1_search_viol_pct"])
    for n, v in zip(names, vals):
        w.writerow([n, v])

# ------------------------------------------------ 그림 2: 램프 S1 점유 펄스
rid6 = "D6_sorts_ramp"
tl6 = list(csv.DictReader(open(f"{TAB}/t4_3_distribution_timeline_{rid6}.csv")))
dec6 = decisions(rid6)
meta6 = jload(rid6, "meta.json")
t0_43 = t12_to_43_g(meta6, meta6["t_meas"])
rate_ts = [(float(r["ts"]) - t0_43, float(r["observed_rate_kbit"]) / 1000.0 if r["observed_rate_kbit"] else None)
           for r in dec6 if r["cohort"] == "c1" and r["class"] == "search"]
fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 5), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1.4], "hspace": 0.12})
xs = [t for t, v in rate_ts]
ys = [v if v is not None else float("nan") for _, v in rate_ts]
a1.step(xs, ys, where="post", color=INK2, linewidth=2)
a1.set_ylabel("설정 전송률 (Mbit/s)")
a1.set_title("램프 런: 전송률 계단과 코호트1 search 의 사이트 이동", fontsize=11)
for s in ("S1", "S2", "S3"):
    a2.plot([int(r["t"]) for r in tl6], [float(r[f"{s}%"]) for r in tl6],
            color=COL[s], linewidth=2, label=s)
a2.set_ylabel("c1 search 분배 (%)")
a2.set_xlabel("본측정 기준 상대시간 (s)")
a2.legend(loc="center left", frameon=False)
a2.set_ylim(-5, 105)
fig.tight_layout()
fig.savefig(f"{FIG}/fig_d2_s1_occupancy_ramp.png")
with open(f"{FIG}/fig_d2_s1_occupancy_ramp_data.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["t_rel_s", "rate_mbit"])
    for t, v in rate_ts:
        w.writerow([round(t, 3), v])

# ------------------------------------------------ 그림 3: D2 결정 타임라인
dec2 = decisions("D2_sorts_radio")
meta2 = jload("D2_sorts_radio", "meta.json")
t0 = t12_to_43_g(meta2, meta2["t_meas"])
c1s = [r for r in dec2 if r["cohort"] == "c1" and r["class"] == "search"]
ts = [float(r["ts"]) - t0 for r in c1s]
rate = [float(r["observed_rate_kbit"]) / 1000.0 if r["observed_rate_kbit"] else float("nan")
        for r in c1s]
fig, (a1, a2, a3, a4) = plt.subplots(4, 1, figsize=(8, 8), sharex=True,
                                     gridspec_kw={"hspace": 0.15})
a1.step(ts, rate, where="post", color=INK2, linewidth=2)
a1.set_ylim(0, 22)
a1.set_ylabel("관측 rate\n(Mbit/s)")
a1.set_title("D2 (SORTS x radio): 관측 -> slack -> 결정 -> 실측 분배", fontsize=11)
a1.text(0.02, 0.82, "빈 구간 = 셰이핑 없음 (achievable 무제한)", transform=a1.transAxes,
        fontsize=8, color=INK2)
for s in ("S1", "S2", "S3"):
    a2.plot(ts, [float(r[f"slack_{s.lower()}"]) for r in c1s], color=COL[s],
            linewidth=2, label=s)
a2.axhline(0, color=INK, linewidth=0.8, linestyle="--")
a2.set_ylabel("slack (ms)")
a2.legend(loc="center right", frameon=False, fontsize=8)
site_y = {"S1": 1, "S2": 2, "S3": 3}
a3.step(ts, [site_y[r["chosen_site"]] for r in c1s], where="post",
        color="#2a78d6", linewidth=2)
a3.set_yticks([1, 2, 3])
a3.set_yticklabels(["S1", "S2", "S3"])
a3.set_ylabel("선택 사이트")
a3.set_ylim(0.5, 3.5)
tl2 = list(csv.DictReader(open(f"{TAB}/t4_3_distribution_timeline_D2_sorts_radio.csv")))
for s in ("S2", "S3"):
    a4.plot([int(r["t"]) for r in tl2], [float(r[f"{s}%"]) for r in tl2],
            color=COL[s], linewidth=2, label=s)
a4.set_ylabel("실측 분배 (%)")
a4.set_xlabel("본측정 기준 상대시간 (s)")
a4.legend(loc="center right", frameon=False, fontsize=8)
a4.set_ylim(-5, 105)
fig.tight_layout()
fig.savefig(f"{FIG}/fig_d3_decision_timeline.png")
with open(f"{FIG}/fig_d3_decision_timeline_data.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["t_rel_s", "rate_mbit", "slack_s1", "slack_s2", "slack_s3", "chosen"])
    for r, t, rt in zip(c1s, ts, rate):
        w.writerow([round(t, 3), rt, r["slack_s1"], r["slack_s2"], r["slack_s3"],
                    r["chosen_site"]])

# ------------------------------------------------ D6 전환 요약
print("\nD6 램프: c1_search 전환 이력 (changed=1):")
for r in dec6:
    if r["changed"] == "1" and r["cohort"] == "c1" and r["class"] == "search":
        print(f"  t={float(r['ts'])-t12_to_43_g(meta6, meta6['t_meas']):+8.1f}s "
              f"rate={r['observed_rate_kbit'] or '없음':>6s}kbit -> {r['chosen_site']}"
              f" (lat {r['apply_latency_ms']}ms)")
print("\n분석 완료 ->", OUT)

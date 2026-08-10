#!/usr/bin/env python3
"""STEP V3 — 표 2 + 그림 2 (무선 교란: 위반은 오르고 분배는 그대로인가).

지표: SLO 위반 = corrected_ms 기준. 분배 = Envoy 조인 결과의 사이트 몫.
radio 교란은 코호트1 에만 걸린다 (tb-radio2.sh C1_IP, 코호트2 = 대조군).
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import common as C  # noqa: E402

RUNS_R = [f"A_radio_{p}" for p in C.POLICIES]
BIN = 1.0


def prep(rid):
    df = C.load_df(rid)
    df["viol"] = ((df["valid"] == 0)
                  | (df["corrected_ms"] > df["ep"].map(C.SLO_MS))).astype(int)
    df["bin"] = np.floor(df["t_rel"] / BIN).astype(int)
    return df


def shares(sub):
    j = sub[sub["site"].isin(["S1", "S2", "S3"])]
    n = len(j)
    if not n:
        return {s: float("nan") for s in ("S1", "S2", "S3")}
    return {s: 100.0 * (j["site"] == s).sum() / n for s in ("S1", "S2", "S3")}


rows, ts_rows = [], []
for rid in RUNS_R:
    pol = C.policy_of(rid)
    sec = C.sections_of(rid)
    df = prep(rid)
    for coh in (1, 2, "all"):
        d = df if coh == "all" else df[df["cohort"] == coh]
        r = {"policy": pol, "cohort": coh}
        for sname in ("pre", "during", "post"):
            a, b = sec[sname]
            sh = shares(d[(d["t_rel"] >= a) & (d["t_rel"] < b)])
            for s in ("S1", "S2", "S3"):
                r[f"{sname}_{s}_pct"] = round(sh[s], 3)
        r["max_delta_pp"] = round(max(abs(r[f"during_{s}_pct"] - r[f"pre_{s}_pct"])
                                      for s in ("S1", "S2", "S3")), 3)
        # 같은 표에 위반도 같이 (분배는 그대로인데 위반은 오르는지)
        for sname in ("pre", "during"):
            a, b = sec[sname]
            w = d[(d["t_rel"] >= a) & (d["t_rel"] < b)]
            ws = w[w["ep"] == "search"]
            r[f"{sname}_search_viol_pct"] = round(100 * ws["viol"].mean(), 3) \
                if len(ws) else float("nan")
            r[f"{sname}_all_viol_pct"] = round(100 * w["viol"].mean(), 3) \
                if len(w) else float("nan")
        r["search_viol_delta_pp"] = round(r["during_search_viol_pct"]
                                          - r["pre_search_viol_pct"], 3)
        rows.append(r)

    # --- 시계열 (그림 2 원자료) : 코호트별
    for coh in (1, 2):
        d = df[df["cohort"] == coh]
        g = d.groupby("bin")
        srch = d[d["ep"] == "search"].groupby("bin")["viol"].agg(["mean", "size"])
        site = d[d["site"].isin(["S1", "S2", "S3"])].groupby(["bin", "site"]).size() \
            .unstack(fill_value=0)
        for s in ("S1", "S2", "S3"):
            if s not in site:
                site[s] = 0
        tot = site.sum(axis=1)
        for b in sorted(set(g.groups) | set(site.index)):
            ts_rows.append({
                "run_id": rid, "policy": pol, "cohort": coh, "t_rel_s": b,
                "n_search": int(srch["size"].get(b, 0)),
                "search_viol_pct": round(100 * srch["mean"].get(b, float("nan")), 4),
                "S1_pct": round(100 * site["S1"].get(b, 0) / tot.get(b, 1), 4)
                if tot.get(b, 0) else float("nan"),
                "S2_pct": round(100 * site["S2"].get(b, 0) / tot.get(b, 1), 4)
                if tot.get(b, 0) else float("nan"),
                "S3_pct": round(100 * site["S3"].get(b, 0) / tot.get(b, 1), 4)
                if tot.get(b, 0) else float("nan"),
            })
    print(f"  {rid} 처리", flush=True)

t2 = pd.DataFrame(rows)
p2 = os.path.join(C.ANA, "tables", "table2_radio_distribution.csv")
t2.to_csv(p2, index=False)
ts = pd.DataFrame(ts_rows)
pts = os.path.join(C.ANA, "figures", "fig2_radio_no_reaction_data.csv")
ts.to_csv(pts, index=False)
print(f"-> {p2}\n-> {pts}")

print("\n[표 2 — 분배 변화 (radio, during vs pre)]")
cols = ["policy", "cohort", "pre_S1_pct", "pre_S2_pct", "pre_S3_pct",
        "during_S1_pct", "during_S2_pct", "during_S3_pct", "max_delta_pp",
        "pre_search_viol_pct", "during_search_viol_pct", "search_viol_delta_pp"]
print(t2[cols].to_string(index=False))

# ---------------------------------------------------------------- 그림 2
try:
    plt = C.setup_mpl()
except ImportError:
    print("matplotlib 없음 — CSV 만 생성")
    sys.exit(0)

fig, axes = plt.subplots(2, 4, figsize=(17, 6.4), sharex=True,
                         gridspec_kw={"height_ratios": [1, 1]})
for col, pol in enumerate(C.POLICIES):
    rid = f"A_radio_{pol}"
    sec = C.sections_of(rid)
    da, db = sec["during"]
    # 마지막 초는 부분 구간이라 비율이 튄다 — 그림에서만 뺀다.
    keep = ts["t_rel_s"] <= 358
    d1 = ts[keep & (ts["run_id"] == rid) & (ts["cohort"] == 1)].sort_values("t_rel_s")
    d2 = ts[keep & (ts["run_id"] == rid) & (ts["cohort"] == 2)].sort_values("t_rel_s")
    top, bot = axes[0][col], axes[1][col]
    for ax in (top, bot):
        ax.axvspan(da, db, color="#eda100", alpha=0.13, zorder=0, lw=0)
        ax.grid(alpha=0.3)
        ax.set_axisbelow(True)
        ax.set_xlim(0, 360)
    top.plot(d1["t_rel_s"], d1["search_viol_pct"], lw=1.6, color=C.SERIES[1],
             label="코호트1 (교란)")
    top.plot(d2["t_rel_s"], d2["search_viol_pct"], lw=1.2, color=C.SERIES[0],
             alpha=0.85, label="코호트2 (대조)")
    top.set_ylim(-4, 108)
    lbl = pol + ("  ※과부하" if pol == "bl_loc" else "")
    top.set_title(lbl, fontsize=11,
                  color=C.ALERT if pol == "bl_loc" else C.INK)
    for s in ("S1", "S2", "S3"):
        bot.plot(d1["t_rel_s"], d1[f"{s}_pct"], lw=1.5, color=C.SITE_COLOR[s],
                 label=s)
    bot.set_ylim(-4, 104)
    bot.set_xlabel("t [s] (측정 시작 기준)")
axes[0][0].set_ylabel("코호트1 search\nSLO 초과율 [%]")
axes[1][0].set_ylabel("사이트 분배 [%]\n(코호트1)")
axes[0][0].legend(fontsize=8, loc="upper left")
axes[1][0].legend(fontsize=8, loc="center left", ncol=3)
fig.suptitle("Fig 2. 무선(Poor 2.3Mbit) 교란 — 위(위반)는 뛰고 아래(분배)는 평평하다. "
             "음영 = during 구간. 지표 = corrected_ms", fontsize=12)
fig.tight_layout()
pf = os.path.join(C.ANA, "figures", "fig2_radio_no_reaction.png")
fig.savefig(pf, dpi=160)
print(f"-> {pf}")

#!/usr/bin/env python3
"""STEP V4 — 표 3 + 그림 3 (서버 교란에서의 반응 지연).

서버 교란은 S3(192.168.0.40) 에 stress-ng --cpu 2 --cpu-load 80 를 건다.
따라서 "분배가 반응했다" = S3 몫이 pre 기준선에서 이탈했다.

분배 변화 시작 판정 규칙 (명시적으로 고정한다):
  기준선 = pre 구간 1초 bin 의 S3 몫 평균 mu, 표준편차 sd
  임계   = mu - max(3*sd, 2.0 %p)
  시작   = 교란 적용완료(t_done) 이후, 임계 미만이 연속 3 bin 이상 유지되는
           첫 bin 의 시각
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import common as C  # noqa: E402

RUNS_S = [f"A_server_{p}" for p in C.POLICIES]
SUSTAIN = 3
MIN_PP = 2.0


def prep(rid):
    df = C.load_df(rid)
    df["viol"] = ((df["valid"] == 0)
                  | (df["corrected_ms"] > df["ep"].map(C.SLO_MS))).astype(int)
    df["bin"] = np.floor(df["t_rel"]).astype(int)
    return df


rows, ts_rows = [], []
for rid in RUNS_S:
    pol = C.policy_of(rid)
    sec = C.sections_of(rid)
    mk = C.marks_rel(rid)
    t_inj_issue = next(m["t_issue"] for m in mk if m["phase"] == "start")
    t_inj = next(m["t_done"] for m in mk if m["phase"] == "start")
    df = prep(rid)
    da, db = sec["during"]
    mid = da + (db - da) / 2.0

    # --- 1초 시계열 (전 코호트: 분배는 공유 LB 의 결정이다)
    site = df[df["site"].isin(["S1", "S2", "S3"])].groupby(["bin", "site"]).size() \
        .unstack(fill_value=0)
    for s in ("S1", "S2", "S3"):
        if s not in site:
            site[s] = 0
    tot = site.sum(axis=1)
    share = site.div(tot, axis=0).mul(100)
    c1s = df[(df["cohort"] == 1) & (df["ep"] == "search")].groupby("bin")["viol"]
    c1s_rate = c1s.mean().mul(100)
    c1s_n = c1s.size()
    allv = df.groupby("bin")["viol"]

    pre_bins = share.index[(share.index >= sec["pre"][0]) & (share.index < sec["pre"][1])]
    mu, sd = share.loc[pre_bins, "S3"].mean(), share.loc[pre_bins, "S3"].std()
    thr = mu - max(3 * sd, MIN_PP)

    # 지속 조건을 만족하는 첫 bin
    cand = [b for b in share.index if b >= t_inj]
    t_react = None
    for i, b in enumerate(cand):
        win = cand[i:i + SUSTAIN]
        if len(win) < SUSTAIN:
            break
        if all(share.loc[w, "S3"] < thr for w in win):
            t_react = b
            break

    dsub = df[(df["t_rel"] >= da) & (df["t_rel"] < db)]
    h1 = dsub[dsub["t_rel"] < mid]
    h2 = dsub[dsub["t_rel"] >= mid]

    def vr(d, coh=None, ep=None):
        if coh:
            d = d[d["cohort"] == coh]
        if ep:
            d = d[d["ep"] == ep]
        return round(100 * d["viol"].mean(), 4) if len(d) else float("nan")

    during_bins = [b for b in share.index if da <= b < db]
    max_dev = round(max(abs(share.loc[b, "S3"] - mu) for b in during_bins), 3)

    if t_react is not None:
        pre_react = df[(df["t_rel"] >= t_inj) & (df["t_rel"] < t_react)]
        lag = round(t_react - t_inj, 2)
        cum_all = int(pre_react["viol"].sum())
        cum_c1s = int(pre_react[(pre_react["cohort"] == 1)
                                & (pre_react["ep"] == "search")]["viol"].sum())
    else:
        lag, cum_all, cum_c1s = float("nan"), float("nan"), float("nan")
        pre_react = df[(df["t_rel"] >= t_inj) & (df["t_rel"] < db)]
        cum_all = int(pre_react["viol"].sum())        # 반응 없음 -> during 전체
        cum_c1s = int(pre_react[(pre_react["cohort"] == 1)
                                & (pre_react["ep"] == "search")]["viol"].sum())

    rows.append({
        "policy": pol,
        "during_h1_viol_pct": vr(h1), "during_h2_viol_pct": vr(h2),
        "diff_pp": round(vr(h2) - vr(h1), 4),
        "h1_c1_search_pct": vr(h1, 1, "search"), "h2_c1_search_pct": vr(h2, 1, "search"),
        "t_inject_issue_s": round(t_inj_issue, 2), "t_inject_done_s": round(t_inj, 2),
        "pre_S3_mu_pct": round(mu, 3), "pre_S3_sd_pp": round(sd, 3),
        "thr_S3_pct": round(thr, 3),
        "t_react_s": round(t_react, 2) if t_react is not None else "",
        "react_lag_s": lag,
        "max_S3_dev_pp": max_dev,
        "cum_viol_before_react_all": cum_all,
        "cum_viol_before_react_c1_search": cum_c1s,
        "reacted": bool(t_react is not None),
    })

    for b in share.index:
        if b > 358:
            continue
        ts_rows.append({
            "run_id": rid, "policy": pol, "t_rel_s": int(b),
            "c1_search_viol_pct": round(c1s_rate.get(b, float("nan")), 4),
            "n_c1_search": int(c1s_n.get(b, 0)),
            "all_viol_pct": round(100 * allv.mean().get(b, float("nan")), 4),
            "S1_pct": round(share.loc[b, "S1"], 4),
            "S2_pct": round(share.loc[b, "S2"], 4),
            "S3_pct": round(share.loc[b, "S3"], 4),
        })
    print(f"  {rid} 처리  반응={t_react}", flush=True)

t3 = pd.DataFrame(rows)
p3 = os.path.join(C.ANA, "tables", "table3_server_reaction.csv")
t3.to_csv(p3, index=False)
ts = pd.DataFrame(ts_rows)
pts = os.path.join(C.ANA, "figures", "fig3_server_late_reaction_data.csv")
ts.to_csv(pts, index=False)
print(f"-> {p3}\n-> {pts}")
print("\n[표 3]")
print(t3.to_string(index=False))

# ---------------------------------------------------------------- 그림 3
try:
    plt = C.setup_mpl()
except ImportError:
    sys.exit(0)

# 교란이 실제로 S3 를 열화시켰다는 증거를 그림에 같이 싣는다 (v4b 산출).
fcp = os.path.join(C.ANA, "tables", "table3b_fc_by_site.csv")
FC = pd.read_csv(fcp) if os.path.exists(fcp) else None

fig, axes = plt.subplots(2, 4, figsize=(17, 6.4), sharex=True)
for col, pol in enumerate(C.POLICIES):
    rid = f"A_server_{pol}"
    r = t3[t3["policy"] == pol].iloc[0]
    sec = C.sections_of(rid)
    da, db = sec["during"]
    d = ts[ts["run_id"] == rid].sort_values("t_rel_s")
    top, bot = axes[0][col], axes[1][col]
    for ax in (top, bot):
        ax.axvspan(da, db, color="#eda100", alpha=0.13, zorder=0, lw=0)
        ax.grid(alpha=0.3)
        ax.set_axisbelow(True)
        ax.set_xlim(0, 360)
    top.plot(d["t_rel_s"], d["c1_search_viol_pct"], lw=1.5, color=C.SERIES[1])
    top.set_ylim(-4, 108)
    lbl = pol + ("  ※과부하" if pol == "bl_loc" else "")
    top.set_title(lbl, fontsize=11, color=C.ALERT if pol == "bl_loc" else C.INK)
    for s in ("S1", "S2", "S3"):
        bot.plot(d["t_rel_s"], d[f"{s}_pct"], lw=1.5, color=C.SITE_COLOR[s], label=s)
    bot.axhline(r["thr_S3_pct"], color=C.INK2, ls=":", lw=1)
    bot.set_ylim(-4, 104)
    bot.set_xlabel("t [s] (측정 시작 기준)")
    if FC is not None:
        q = FC[(FC["policy"] == pol) & (FC["disturb"] == "server")
               & (FC["site"] == "S3")].set_index("section")["fc_p95"]
        if {"pre", "during"} <= set(q.index):
            bot.text(0.03, 0.06,
                     f"교란은 걸렸다: S3 f_c p95 {q['pre']:.2f} → {q['during']:.2f} ms "
                     f"({q['during'] / q['pre']:.2f}×)",
                     transform=bot.transAxes, fontsize=7.5, color=C.INK2,
                     bbox=dict(fc="#fcfcfb", ec="#c9c8c3", lw=0.7, pad=2.2))
    if r["reacted"]:
        for ax in (top, bot):
            ax.axvline(r["t_react_s"], color=C.ALERT, lw=1.6, ls="--")
        top.annotate(f"분배 반응 t={r['t_react_s']:.0f}s\n"
                     f"(주입 +{r['react_lag_s']:.0f}s)\n"
                     f"그때까지 누적 위반 {int(r['cum_viol_before_react_all']):,}건",
                     xy=(r["t_react_s"], 60), xytext=(0.03, 0.62),
                     textcoords="axes fraction", fontsize=7.5, color=C.ALERT,
                     bbox=dict(fc="#fcfcfb", ec=C.ALERT, lw=0.7, pad=2.5))
    else:
        top.text(0.03, 0.72, "분배 반응 없음\n(during 내 임계 미달)\n"
                             f"during 누적 위반 {int(r['cum_viol_before_react_all']):,}건",
                 transform=top.transAxes, fontsize=7.5, color=C.INK2,
                 bbox=dict(fc="#fcfcfb", ec="#c9c8c3", lw=0.7, pad=2.5))
axes[0][0].set_ylabel("코호트1 search\nSLO 초과율 [%]")
axes[1][0].set_ylabel("사이트 분배 [%]\n(전 코호트)")
axes[1][0].legend(fontsize=8, loc="center left", ncol=3)
fig.suptitle("Fig 3. 서버(S3 stress-ng) 교란 — 네 정책 모두 during 안에서 분배 반응이 "
             "판정 임계(아래 점선, S3 몫)를 넘지 않았다.  아래 상자 = 교란이 실제로 "
             "걸렸다는 증거(f_c). 지표 = corrected_ms", fontsize=11.5)
fig.tight_layout()
pf = os.path.join(C.ANA, "figures", "fig3_server_late_reaction.png")
fig.savefig(pf, dpi=160)
print(f"-> {pf}")

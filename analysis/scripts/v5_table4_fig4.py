#!/usr/bin/env python3
"""STEP V5 — 표 4 + 그림 4 (램프에서 h 도출).

T1/T5 = 코호트1 search SLO 초과율이 1% / 5% 를 넘은 최초 시각.
1초 bin 은 코호트1 search 가 초당 ~133건이라 1% 해상도가 약 1.3건이다. 그래서
  primary  : 1초 bin (지시서 문구 그대로)
  robust   : 3초 이동평균 (잡음에 덜 민감)
둘 다 낸다. h 의 근거로 제안하는 값은 램프시작 -> T1 이다.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import common as C  # noqa: E402

RUNS_B = [f"B_ramp_{p}" for p in C.POLICIES]


def first_cross(series, idx, thr, t_from):
    for t, v in zip(idx, series):
        if t >= t_from and not np.isnan(v) and v > thr:
            return float(t)
    return None


rows, ts_rows = [], []
for rid in RUNS_B:
    pol = C.policy_of(rid)
    mk = C.marks_rel(rid)
    steps = [m for m in mk if m["what"].startswith("ramp_")
             and m["what"] not in ("ramp_base", "ramp_clear")]
    steps.sort(key=lambda m: m["t_done"])
    t_ramp = next(m["t_done"] for m in mk if m["phase"] == "start")   # ramp_0 적용완료
    t_clear = next(m["t_issue"] for m in reversed(mk) if m["phase"] == "end")
    rates = [(m["t_done"], int(m["spec"].split()[1].replace("kbit", "")))
             for m in steps]

    df = C.load_df(rid)
    df["viol"] = ((df["valid"] == 0)
                  | (df["corrected_ms"] > df["ep"].map(C.SLO_MS))).astype(int)
    df["bin"] = np.floor(df["t_rel"]).astype(int)
    d1s = df[(df["cohort"] == 1) & (df["ep"] == "search")]
    g = d1s.groupby("bin")["viol"]
    rate = (g.mean() * 100).reindex(range(0, 360)).astype(float)
    n = g.size().reindex(range(0, 360)).fillna(0).astype(int)
    roll = rate.rolling(3, center=True, min_periods=2).mean()

    def rate_at(t):
        r = None
        for tt, kb in rates:
            if tt <= t:
                r = kb
        return r

    def step_idx(t):
        i = None
        for k, (tt, _) in enumerate(rates):
            if tt <= t:
                i = k
        return i

    res = {"policy": pol, "run_id": rid,
           "t_ramp_start_s": round(t_ramp, 2),
           "t_ramp_clear_s": round(t_clear, 2)}
    for tag, ser in (("", rate), ("_roll3", roll)):
        t1 = first_cross(ser.values, ser.index, 1.0, t_ramp)
        t5 = first_cross(ser.values, ser.index, 5.0, t_ramp)
        res[f"T1{tag}_s"] = t1
        res[f"T5{tag}_s"] = t5
        res[f"lead_to_T1{tag}_s"] = round(t1 - t_ramp, 2) if t1 is not None else None
        res[f"lead_to_T5{tag}_s"] = round(t5 - t_ramp, 2) if t5 is not None else None
        res[f"T1{tag}_rate_kbit"] = rate_at(t1) if t1 is not None else None
        res[f"T5{tag}_rate_kbit"] = rate_at(t5) if t5 is not None else None
        res[f"T1{tag}_step"] = step_idx(t1) if t1 is not None else None
        res[f"T5{tag}_step"] = step_idx(t5) if t5 is not None else None
    # 램프 종료(마지막 단계 이후 ~ clear) 초과율
    last = rates[-1][0]
    tailv = d1s[(d1s["t_rel"] >= last) & (d1s["t_rel"] < t_clear)]["viol"]
    res["ramp_end_viol_pct"] = round(100 * tailv.mean(), 3) if len(tailv) else None
    res["ramp_end_n"] = len(tailv)
    # 램프 시작 직전 기준선
    base = d1s[(d1s["t_rel"] >= t_ramp - 55) & (d1s["t_rel"] < t_ramp - 5)]["viol"]
    res["pre_ramp_viol_pct"] = round(100 * base.mean(), 3) if len(base) else None

    # --- 보조: 기준선 대비 이탈 시점 T_dep --------------------------------
    # bl_rr/bl_lr/bl_loc 는 램프 전부터 초과율이 1% 를 넘으므로 절대 임계
    # T1 이 램프 효과에 귀속되지 않는다. 기준선에서 이탈한 시점을 따로 낸다.
    #   임계 = base_mu + max(3*base_sd, 1.0 %p), 연속 3 bin 유지
    bb = [b for b in rate.index if t_ramp - 55 <= b < t_ramp - 5]
    bmu, bsd = rate.loc[bb].mean(), rate.loc[bb].std()
    dthr = bmu + max(3 * bsd, 1.0)
    res["base_mu_pct"] = round(bmu, 3)
    res["base_sd_pp"] = round(bsd, 3)
    res["dep_thr_pct"] = round(dthr, 3)
    cand = [b for b in roll.index if b >= t_ramp and not np.isnan(roll[b])]
    t_dep = None
    for i, b in enumerate(cand):
        win = cand[i:i + 3]
        if len(win) < 3:
            break
        if all(roll[w] > dthr for w in win):
            t_dep = float(b)
            break
    res["T_dep_s"] = t_dep
    res["lead_to_T_dep_s"] = round(t_dep - t_ramp, 2) if t_dep is not None else None
    res["T_dep_rate_kbit"] = rate_at(t_dep) if t_dep is not None else None
    res["T_dep_step"] = step_idx(t_dep) if t_dep is not None else None
    rows.append(res)

    for t in range(0, 359):
        ts_rows.append({"run_id": rid, "policy": pol, "t_rel_s": t,
                        "n_c1_search": int(n.get(t, 0)),
                        "c1_search_viol_pct": round(rate.get(t), 4)
                        if not np.isnan(rate.get(t, np.nan)) else "",
                        "c1_search_viol_pct_roll3": round(roll.get(t), 4)
                        if not np.isnan(roll.get(t, np.nan)) else "",
                        "rate_kbit": rate_at(t) if rate_at(t) else ""})
    print(f"  {rid} 처리  T1={res['T1_s']} T5={res['T5_s']}", flush=True)

t4 = pd.DataFrame(rows)
p4 = os.path.join(C.ANA, "tables", "table4_ramp.csv")
t4.to_csv(p4, index=False)
ts = pd.DataFrame(ts_rows)
pts = os.path.join(C.ANA, "figures", "fig4_ramp_horizon_data.csv")
ts.to_csv(pts, index=False)
print(f"-> {p4}\n-> {pts}")
cols = ["policy", "t_ramp_start_s", "pre_ramp_viol_pct",
        "T1_s", "lead_to_T1_s", "T1_rate_kbit", "T1_step",
        "T5_s", "lead_to_T5_s", "T5_rate_kbit", "T5_step",
        "ramp_end_viol_pct"]
print("\n[표 4 — 지시서 규정대로 절대 임계 1%/5%]")
print(t4[cols].to_string(index=False))
print("\n[표 4 보조 — 기준선 대비 이탈 T_dep (절대 임계가 램프 전부터 깨진 정책용)]")
print(t4[["policy", "base_mu_pct", "base_sd_pp", "dep_thr_pct", "T_dep_s",
          "lead_to_T_dep_s", "T_dep_rate_kbit", "T_dep_step"]].to_string(index=False))
lt = t4["lead_to_T1_s"].dropna()
ld = t4["lead_to_T_dep_s"].dropna()
clean = t4[t4["pre_ramp_viol_pct"] < 1.0]["lead_to_T1_s"].dropna()
print(f"\nT1 리드타임 중앙값(전체) = {lt.median():.2f}s  값={list(lt)}")
print(f"T1 리드타임 (기준선<1% 인 런만) = {list(clean)}")
print(f"T_dep 리드타임 중앙값 = {ld.median():.2f}s  값={list(ld)}")

# ---------------------------------------------------------------- 그림 4
try:
    plt = C.setup_mpl()
except ImportError:
    sys.exit(0)

fig, axes = plt.subplots(1, 4, figsize=(17, 4.8), sharex=True, sharey=True)
for col, pol in enumerate(C.POLICIES):
    rid = f"B_ramp_{pol}"
    r = t4[t4["policy"] == pol].iloc[0]
    d = ts[ts["run_id"] == rid].copy()
    d["c1_search_viol_pct"] = pd.to_numeric(d["c1_search_viol_pct"], errors="coerce")
    d["rate_kbit"] = pd.to_numeric(d["rate_kbit"], errors="coerce")
    ax = axes[col]
    ax.grid(alpha=0.3)
    ax.set_axisbelow(True)
    ax.set_xlim(0, 360)
    ax.set_ylim(-4, 108)
    ax.plot(d["t_rel_s"], d["c1_search_viol_pct"], lw=1.4, color=C.SERIES[1],
            label="코호트1 search 초과율 (좌)")
    ax2 = ax.twinx()
    ax2.step(d["t_rel_s"], d["rate_kbit"] / 1000.0, where="post", lw=1.6,
             color=C.SERIES[0], alpha=0.85, label="전송률 (우)")
    ax2.set_ylim(0, 21.5)
    ax2.tick_params(labelright=(col == 3), colors=C.SERIES[0])
    if col == 3:
        ax2.set_ylabel("전송률 [Mbit/s] (계단, 우측 축)", color=C.SERIES[0])
    ax.axhline(r["base_mu_pct"], color=C.INK2, ls=":", lw=1)
    ax.axvline(r["t_ramp_start_s"], color=C.INK2, lw=1.2)
    ax.text(r["t_ramp_start_s"] + 1.5, -1.5, "램프 시작", fontsize=7, color=C.INK2)
    yy = 0.975
    for tag, val, cl, ls in (("T1", r["T1_s"], "#eda100", "--"),
                             ("T5", r["T5_s"], C.ALERT, "--"),
                             ("T_dep", r["T_dep_s"], "#4a3aa7", "-.")):
        if val is None or pd.isna(val):
            continue
        ax.axvline(val, color=cl, lw=1.5, ls=ls)
        ax.text(0.02, yy,
                f"{tag} = {val:.0f}s  (+{val - r['t_ramp_start_s']:.0f}s, "
                f"{r[tag + '_rate_kbit'] / 1000:.1f} Mb/s, step {int(r[tag + '_step'])})",
                transform=ax.transAxes, fontsize=7.2, color=cl, va="top",
                bbox=dict(fc="#fcfcfb", ec="none", alpha=0.9, pad=1.6))
        yy -= 0.062
    if r["pre_ramp_viol_pct"] >= 1.0:
        ax.text(0.02, yy - 0.02,
                f"※ 램프 전 기준선이 이미 {r['pre_ramp_viol_pct']:.1f}% (>1%)\n"
                "   → 이 런의 T1/T5 는 램프 효과가 아니다",
                transform=ax.transAxes, fontsize=7, color=C.ALERT, va="top",
                linespacing=1.4,
                bbox=dict(fc="#fcfcfb", ec=C.ALERT, lw=0.7, alpha=0.95, pad=2.2))
    lbl = pol + ("  ※과부하" if pol == "bl_loc" else "")
    ax.set_title(lbl, fontsize=11, color=C.ALERT if pol == "bl_loc" else C.INK)
    ax.set_xlabel("t [s] (측정 시작 기준)")
axes[0].set_ylabel("코호트1 search SLO 초과율 [%]\n(좌측 축, corrected_ms)")
axes[0].legend(fontsize=8, loc="center left")
fig.suptitle("Fig 4. 무선 램프(20 → 1.6 Mbit/s, 10초 12단계) — T1(1%)·T5(5%)·"
             "T_dep(기준선 이탈, 연속 3초) 도달 시각.  "
             "네 런 모두 T1 이 step 0(=20 Mbit, 아직 안 떨어진 시점)에 걸린다",
             fontsize=11.5)
fig.tight_layout()
pf = os.path.join(C.ANA, "figures", "fig4_ramp_horizon.png")
fig.savefig(pf, dpi=160)
print(f"-> {pf}")

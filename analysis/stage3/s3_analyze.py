#!/usr/bin/env python3
"""STAGE3 본 배치 분석 — 코호트 확장 radio 축 (PREREG_S3 §1~3).

런별 (c1 열화, N 코호트):
  ① 주 지표: **비열화 코호트(c2..cN) during 위반율** (코호트 가중 합).
     c1 자신의 위반율은 관측 항목(부하 변동 직접 노출 — 해석하지 않음).
  ② P-S3-2 입력: pre 창 위반율 (전 코호트).
  ③ P-S3-3: stale 분류 (site×class; traffic0/warm 제외, starved 만 카운트).
  ④ P-S3-4: c1:search 플립 수 + 비열화 유닛 changed 수 (during).
  ⑤ P-S3-5: c1 유입 목적지 사이트의 Δf_c = during obs p95 중앙값 − pre.
  ⑥ P-S3-6: 비열화 유닛의 사용-슬랙(선택 사이트, 집합이면 최대) 분포
     pre → during (중앙값·최소값). 최소값 0 근접 유닛 명시.
  ⑦ 분배·달성 rps·오버런·A1(폴러).
"""
import csv
import json
import os
import sys
from collections import defaultdict

N_MIN_FC = 100
SITES = ("S1", "S2", "S3")


def pctl(xs, q=0.5):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def effect_time(root, rid):
    p = os.path.join(root, f"tcpoll_{rid}.csv")
    if not os.path.exists(p):
        return None
    for r in csv.DictReader(open(p)):
        try:
            if int(r["n_netem_c1"]) >= 1:
                return float(r["ts"])
        except (ValueError, TypeError):
            continue
    return None


def slack_used(r):
    """tick 의 '사용 슬랙' — 단일 선택이면 그 사이트, 집합이면 집합 내 최대."""
    fs = (r["feasible_set"] or "").split("|")
    vals = []
    for s in fs:
        col = {"S1": "slack_s1", "S2": "slack_s2", "S3": "slack_s3"}.get(s)
        if col and r[col]:
            vals.append(float(r[col]))
    return max(vals) if vals else None


def analyze(root, rd):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    summ = json.load(open(os.path.join(rd, "summary.json")))
    n_coh = int(meta.get("arm", {}).get("effective", {}).get("n_cohorts", 2))
    d43 = meta["clock"]["d43_s"]
    mk_on = [m for m in meta["marks"] if m.get("phase") == "start"][0]
    mk_off = [m for m in meta["marks"] if m.get("phase") == "end"][0]
    t_on = mk_on["t43_done"]
    t_off = mk_off.get("t43_done", mk_off["t_done"] + d43)
    A1 = effect_time(root, meta["run_id"])

    def coh_viol(sec, cohs):
        by = summ["sections"].get(sec, {}).get("by_cohort") or {}
        n = sum(v["n"] for c, v in by.items() if int(c) in cohs)
        vi = sum(v["n"] * v["slo_violation_rate"] for c, v in by.items()
                 if int(c) in cohs)
        return (round(100 * vi / n, 4) if n else None), n
    nond = set(range(2, n_coh + 1))
    nd_dur, nd_n = coh_viol("during", nond)
    c1_dur, _ = coh_viol("during", {1})
    pre_all, _ = coh_viol("pre", set(range(1, n_coh + 1)))
    nd_by = {c: round(100 * v["slo_violation_rate"], 4)
             for c, v in (summ["sections"].get("during", {}).get("by_cohort") or {}).items()
             if int(c) != 1}

    # decisions: 플립·비열화 changed·슬랙 분포
    c1s_switch = 0
    nd_changed = defaultdict(int)
    sl = defaultdict(lambda: {"pre": [], "dur": []})
    ticks = set()
    for r in csv.DictReader(open(os.path.join(rd, "decisions.csv"))):
        ts = float(r["ts"])
        ticks.add(ts)
        u = f"{r['cohort']}:{r['class']}"
        in_dur = t_on <= ts < t_off
        if r["changed"] == "1" and in_dur:
            if u == "c1:search":
                c1s_switch += 1
            elif r["cohort"] != "c1":
                nd_changed[u] += 1
        if r["cohort"] != "c1":
            v = slack_used(r)
            if v is not None:
                if in_dur:
                    sl[u]["dur"].append(v)
                elif ts < t_on:
                    sl[u]["pre"].append(v)
    period = float(meta.get("arm", {}).get("effective", {}).get("ctl_period_s", 1.0))
    tss = sorted(ticks)
    gaps = [(b - a) for a, b in zip(tss, tss[1:])]
    over = sum(1 for g in gaps if g > period * 1.1)

    slack_tab = {}
    for u, d in sorted(sl.items()):
        if not d["pre"] or not d["dur"]:
            continue
        slack_tab[u] = {
            "pre_med": round(pctl(d["pre"]), 2), "pre_min": round(min(d["pre"]), 2),
            "dur_med": round(pctl(d["dur"]), 2), "dur_min": round(min(d["dur"]), 2)}
    near_zero = [u for u, v in slack_tab.items() if v["dur_min"] < 1.0]

    # obs_state: Δf_c (obs p95 의 tick 중앙값, pre vs during) + stale 분류
    fc = defaultdict(lambda: {"pre": [], "dur": []})
    stale = defaultdict(int)
    rows = list(csv.DictReader(open(os.path.join(rd, "obs_state.csv"))))
    t0 = float(rows[0]["ts"]) if rows else 0
    for r in rows:
        ts = float(r["ts"])
        k = (r["site"], r["class"])
        if r["src"] == "obs":
            if r["p95_ms"]:
                if t_on <= ts < t_off:
                    fc[k]["dur"].append(float(r["p95_ms"]))
                elif t0 + 10 <= ts < t_on:
                    fc[k]["pre"].append(float(r["p95_ms"]))
            continue
        n = int(r["n"])
        if n == 0:
            stale[("traffic0",) + k] += 1
        elif ts - t0 < 10.0:
            stale[("warm",) + k] += 1
        elif n < N_MIN_FC:
            stale[("starved",) + k] += 1
        else:
            stale[("other",) + k] += 1
    dfc = {}
    for k, d in sorted(fc.items()):
        if d["pre"] and d["dur"]:
            dfc[f"{k[0]}/{k[1]}"] = round(pctl(d["dur"]) - pctl(d["pre"]), 3)
    starved = {f"{s}/{c}": v for (cat, s, c), v in stale.items() if cat == "starved"}

    return {
        "run_id": meta["run_id"], "n_cohorts": n_coh,
        "nondeg_during_viol_pct": nd_dur, "nondeg_n": nd_n,
        "nondeg_by_cohort": nd_by,
        "c1_during_viol_pct_observed": c1_dur,
        "pre_all_viol_pct": pre_all,
        "c1s_switches_during": c1s_switch,
        "nondeg_changed": dict(nd_changed) or 0,
        "slack_nondeg": slack_tab, "slack_near_zero_units": near_zero,
        "delta_fc_obs_p95_med": dfc,
        "stale_starved": starved or 0,
        "site_share_during": summ["sections"].get("during", {}).get("site_share"),
        "achieved_rps": summ["sections"].get("during", {}).get("achieved_rps"),
        "overrun_ticks": over, "A1_rel_s": None if A1 is None
        else round(A1 - (mk_on["t_issue"] + d43), 3),
    }


def main():
    root = sys.argv[1]
    out = []
    for rid in sorted(os.listdir(root)):
        rd = os.path.join(root, rid)
        if os.path.isdir(rd) and os.path.exists(os.path.join(rd, "DONE")):
            try:
                out.append(analyze(root, rd))
            except Exception as e:
                out.append({"run_id": rid, "error": f"{type(e).__name__}: {e}"})
    json.dump(out, open(os.path.join(root, "s3_results.json"), "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""작업B2 검증① 재실행 분석 — 사전 등록 기준 3종 (PROGRESS.md §C).

  ① soft on 위반율이 soft off 대비 유의하게 낮다 (평균 차 > 두 arm 반범위 합)
  ② soft_applied 가 기존 blocked_by=slack 창에서 실제 발동
  ③ carry_over(배정 의도, decisions carry_s*/overflow)와 실측 사이트별 도착
     등가 부하가 일치 (both 창, search 클래스 기준 비교)

부가: (A) 목적함수(초과량 합)로도 사후 평가 — 주 판정은 (B) 위반율.
드리프트 §G 예측 대조: soft off 평균 vs 오후 74.21 / 야간 70.54.
"""
import csv
import glob
import gzip
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "/home/user/exp/analysis/night-20260810")
import t2_policy_repeat as t2  # noqa: E402

W_EQ = {"search": 1.0, "reserve": 0.278, "recommend": 0.178}
SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
SITE_OF_IP = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
RUNS = "/home/user/exp/runs/taskB2-20260810/v1s"


def arm_of(meta):
    if meta["policy"] != "sorts_reactive":
        return meta["policy"]
    eff = meta["arm"]["effective"]
    a = f"{eff['subset_policy']}+{'on' if eff.get('capacity_check') else 'off'}"
    a += "+softon" if eff.get("soft_assign") else "+softoff"
    return a


def decisions_stats(rd, lo, hi, d12, d43):
    """both 창의 tick 통계: blocked_by, soft 발동, carry 합, 마지막 유닛 planned."""
    dp = os.path.join(rd, "decisions.csv")
    if not os.path.exists(dp):
        return None
    by = Counter()
    n = soft_n = 0
    carry = defaultdict(float)
    over = obj = 0.0
    weights = Counter()
    ticks = set()
    for r in csv.DictReader(open(dp)):
        ts12 = float(r["ts"]) - d43 + 0.0    # ts 는 .43 시계 → 러너 시계
        ts12 = ts12 + d12                    # 러너 → .12 시계
        if not (lo <= ts12 <= hi):
            continue
        n += 1
        ticks.add(r["ts"])
        by[r.get("blocked_by", "") or "-"] += 1
        if r.get("soft_applied") == "1":
            soft_n += 1
            for s in ("s1", "s2", "s3"):
                v = r.get(f"carry_{s}")
                if v:
                    carry[s.upper()] += float(v)
            over += float(r.get("soft_overflow_eq") or 0)
            obj += float(r.get("soft_objective_eq") or 0)
            weights[r.get("soft_weights", "")] += 1
    nt = max(len(ticks), 1)
    return {"n_unit_ticks": n, "blocked_by": dict(by),
            "soft_applied_ticks": soft_n,
            "carry_eq_rps_mean": {k: round(v / nt, 1)
                                  for k, v in sorted(carry.items())},
            "overflow_eq_rps_mean": round(over / nt, 2),
            "objective_eq_rps_mean": round(obj / nt, 2),
            "weights_hist_top": weights.most_common(5)}


def measured_eq(rd, lo, hi):
    """both 창 실측: 사이트별 등가 부하 + search 만 따로 (carry 대조용).
    부가 (A) 목적함수: 클래스별 Σ max(0, corrected − SLO) [ms/s]."""
    hm = {}
    with gzip.open(os.path.join(rd, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) > 10:
                hm[p[1]] = p[10].split(":")[0]
    eq = defaultdict(float)
    seq = defaultdict(float)
    exceed = 0.0
    for c in (1, 2):
        fp = os.path.join(rd, f"load_c{c}.csv")
        if not os.path.exists(fp):
            continue
        for r in csv.DictReader(open(fp)):
            if r["warmup"] != "0":
                continue
            t = float(r["end_ts"])
            if not (lo <= t <= hi):
                continue
            exceed += max(0.0, float(r["corrected_ms"]) - SLO[r["ep"]])
            ip = hm.get(r["request_id"])
            s = SITE_OF_IP.get(ip) if ip else None
            if s is None:
                continue
            eq[s] += W_EQ[r["ep"]]
            if r["ep"] == "search":
                seq[s] += 1.0
    dur = max(hi - lo, 1e-9)
    return {"observed_eq_rps": {s: round(v / dur, 1) for s, v in sorted(eq.items())},
            "observed_search_rps": {s: round(v / dur, 1)
                                    for s, v in sorted(seq.items())},
            "objA_exceed_ms_per_s": round(exceed / dur, 1)}


def main():
    out = []
    for rd in sorted(glob.glob(os.path.join(RUNS, "v1s_*"))):
        if not os.path.exists(os.path.join(rd, "DONE")):
            continue
        meta = json.load(open(os.path.join(rd, "meta.json")))
        r = t2.one_run(rd)
        r["arm"] = arm_of(meta)
        r["done_order"] = os.path.getmtime(os.path.join(rd, "DONE"))
        lo, hi = t2.windows(meta)["both"]
        d43 = meta["clock"]["d43_s"]
        d12 = meta["clock"]["d12_s"]
        r["dec"] = decisions_stats(rd, lo, hi, d12, d43)
        r["meas"] = measured_eq(rd, lo, hi)
        out.append(r)
    out.sort(key=lambda r: r["done_order"])

    arms = defaultdict(list)
    for r in out:
        if not r["suspect"]:
            arms[r["arm"]].append(r["windows"]["both"]["viol_pct"])
    agg = {a: {"n": len(v), "runs": [round(x, 3) for x in v],
               "mean": round(sum(v) / len(v), 3),
               "half_range": round((max(v) - min(v)) / 2, 3)}
           for a, v in arms.items()}

    son = agg.get("far_tier+on+softon")
    soff = agg.get("far_tier+on+softoff")
    crit1 = None
    if son and soff:
        diff = soff["mean"] - son["mean"]
        thr = son["half_range"] + soff["half_range"]
        crit1 = {"diff_pp": round(diff, 3), "threshold_pp": round(thr, 3),
                 "pass": diff > thr}
    soft_ticks = [r["dec"]["soft_applied_ticks"] for r in out
                  if r["arm"] == "far_tier+on+softon" and r["dec"]]
    crit2 = {"soft_applied_ticks": soft_ticks,
             "pass": bool(soft_ticks) and all(t > 0 for t in soft_ticks)}

    res = {"runs": [{k: r[k] for k in ("run_id", "arm", "suspect")}
                    | {"viol_both": r["windows"]["both"]["viol_pct"],
                       "dec": r["dec"], "meas": r["meas"],
                       "by_class_both": r["windows"]["both"].get(
                           "by_cohort_class")}
                    for r in out],
           "agg": agg, "crit1_significant_improvement": crit1,
           "crit2_soft_fired": crit2,
           "drift_G": {"soff_mean_tonight": soff["mean"] if soff else None,
                       "afternoon_ref": 74.21, "night_ref": 70.54}}
    json.dump(res, open("/home/user/exp/analysis/taskB2/v1s_results.json", "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps({k: res[k] for k in ("agg",
                                          "crit1_significant_improvement",
                                          "crit2_soft_fired", "drift_G")},
                     ensure_ascii=False, indent=1))
    for r in out:
        d = r["dec"] or {}
        print(f"{r['run_id']:>11s} {r['arm']:>22s} "
              f"both={r['windows']['both']['viol_pct']:7.3f}% "
              f"soft틱={d.get('soft_applied_ticks')} "
              f"blocked={d.get('blocked_by')} carry={d.get('carry_eq_rps_mean')} "
              f"of={d.get('overflow_eq_rps_mean')} 실측eq={r['meas']['observed_eq_rps']} "
              f"objA={r['meas']['objA_exceed_ms_per_s']}")


if __name__ == "__main__":
    main()

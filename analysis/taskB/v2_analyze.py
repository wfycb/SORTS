#!/usr/bin/env python3
"""작업B 검증② 판정: 클린×1400 트리거 주입, 사전 등록 기준 3종.

  ① strict+on 락인 0/n (락인 = 전환합 > 600 그리고 위반 > 30 % — 캘리브
     실측 락인 1980~2520전환/64~71 % vs 비락인 0~32전환/0.2 % 사이 명확 분리)
  ② blocked_by 에 capacity 성분('capacity' 또는 'both')이 있고 그 tick 에
     S2 가 cap_blocked_sites 에 포함 — 용량 항이 S2 이동을 실제로 막았다
  ③ 진동 지표 소멸: strict+on 전환합이 비락인 수준(<100), 주기-2 구조 부재

arm: v2cal_soff_1/2 + v2_soff_3 = strict+off n=3 (before).
"""
import csv
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, "/home/user/exp/analysis/taskB-prep")
import d1_repeat as d1  # noqa: E402

LOCK_TRANS = 600
LOCK_VIOL = 30.0


def arm_of(meta):
    eff = meta["arm"]["effective"]
    return f"{eff['subset_policy']}+{'on' if eff.get('capacity_check') else 'off'}"


def blocked_full(rd):
    dp = os.path.join(rd, "decisions.csv")
    by = Counter()
    cap_s2_ticks = 0
    for r in csv.DictReader(open(dp)):
        b = r.get("blocked_by", "") or "-"
        by[b] += 1
        if b in ("capacity", "both") and "S2" in (r.get("cap_blocked_sites") or ""):
            cap_s2_ticks += 1
    return dict(by), cap_s2_ticks


def main():
    rds = sorted(glob.glob("/home/user/exp/runs/taskB-20260810/v2/v2*"))
    runs = []
    for rd in rds:
        if not os.path.exists(os.path.join(rd, "DONE")):
            continue
        meta = json.load(open(os.path.join(rd, "meta.json")))
        r = d1.one_run(rd)
        r["arm"] = arm_of(meta)
        trans = sum(u["site_transitions"] for u in r["oscillation"].values())
        r["trans_total"] = trans
        r["locked"] = trans > LOCK_TRANS and r["viol_pct"] > LOCK_VIOL
        r["blocked_by_hist"], r["cap_s2_ticks"] = blocked_full(rd)
        runs.append(r)

    def arm_runs(a):
        return [r for r in runs if r["arm"] == a and not r["suspect"]]

    son = arm_runs("strict_far+on")
    soff = arm_runs("strict_far+off")
    fon = arm_runs("far_tier+on")
    crit1 = {"locked": sum(r["locked"] for r in son), "n": len(son),
             "pass": len(son) > 0 and not any(r["locked"] for r in son)}
    crit2_ticks = sum(r["cap_s2_ticks"] for r in son)
    crit2 = {"cap_blocked_S2_ticks_strict_on": crit2_ticks,
             "pass": crit2_ticks > 0}
    crit3 = {"strict_on_trans": [r["trans_total"] for r in son],
             "pass": len(son) > 0 and all(r["trans_total"] < 100 for r in son)}
    out = {"runs": [{k: r[k] for k in ("run_id", "arm", "viol_pct",
                                       "trans_total", "locked",
                                       "blocked_by_hist", "cap_s2_ticks",
                                       "per_conn_viol_pct", "by_class")}
                    for r in runs],
           "crit1_no_lockin": crit1, "crit2_capacity_blocked_S2": crit2,
           "crit3_oscillation_gone": crit3,
           "overall_pass": crit1["pass"] and crit2["pass"] and crit3["pass"],
           "arms": {a: [round(r["viol_pct"], 3) for r in arm_runs(a)]
                    for a in ("strict_far+off", "strict_far+on", "far_tier+on")}}
    json.dump(out, open("/home/user/exp/analysis/taskB/v2_results.json", "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ("crit1_no_lockin",
                                          "crit2_capacity_blocked_S2",
                                          "crit3_oscillation_gone",
                                          "overall_pass", "arms")},
                     ensure_ascii=False, indent=1))
    for r in runs:
        print(f"{r['run_id']:>13s} {r['arm']:>14s} viol={r['viol_pct']:7.3f}% "
              f"전환={r['trans_total']:4d} {'★락인' if r['locked'] else '안정'} "
              f"blocked={r['blocked_by_hist']} capS2틱={r['cap_s2_ticks']}")


if __name__ == "__main__":
    main()

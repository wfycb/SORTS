#!/usr/bin/env python3
"""작업B 검증① 분석: 위반율(arm=정책+cap) + blocked_by 분포 + share 근사 오차.

- 위반율 창 계산은 t2_policy_repeat 재사용 (동일 런 형식).
- blocked_by: decisions.csv 신규 열 — tick×유닛 히스토그램, cap_blocked 사이트.
- share 근사 오차: far 런의 both 창에서 reserve/recommend 실측 S2:S3 분배 vs
  set_share 예측(0.58/0.42); decisions l_eff vs envoy 실측 등가 부하.
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
SITE_OF_IP = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}


def arm_of(meta):
    if meta["policy"] != "sorts_reactive":
        return meta["policy"]
    eff = meta["arm"]["effective"]
    return f"{eff['subset_policy']}+{'on' if eff.get('capacity_check') else 'off'}"


def blocked_stats(rd, lo, hi, d12_to_43):
    """decisions.csv 의 both 창 tick 통계. (43 시계 — 창은 .12 시계라 보정)"""
    dp = os.path.join(rd, "decisions.csv")
    if not os.path.exists(dp):
        return None
    by = Counter()
    capsites = Counter()
    leff = defaultdict(list)
    n = 0
    for r in csv.DictReader(open(dp)):
        ts12 = float(r["ts"]) - d12_to_43          # ts 는 .43 시계
        if not (lo <= ts12 <= hi):
            continue
        n += 1
        by[r.get("blocked_by", "") or "-"] += 1
        if r.get("cap_blocked_sites"):
            for s in r["cap_blocked_sites"].split("|"):
                capsites[s] += 1
        for s in ("s1", "s2", "s3"):
            v = r.get(f"l_eff_{s}")
            if v:
                leff[s.upper()].append(float(v))
    return {"n_unit_ticks": n, "blocked_by": dict(by),
            "cap_blocked_sites": dict(capsites),
            "l_eff_mean_last_unit": {k: round(sum(v[5::6]) / max(len(v[5::6]), 1), 1)
                                     for k, v in leff.items()}}


def share_error(rd, lo, hi):
    """both 창 reserve/recommend 의 실측 사이트 분배 + 실측 등가 부하."""
    hm = {}
    with gzip.open(os.path.join(rd, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) > 17:
                hm[p[1]] = p[10].split(":")[0]
    rr = Counter()
    eq = defaultdict(float)
    for c in (1, 2):
        for r in csv.DictReader(open(os.path.join(rd, f"load_c{c}.csv"))):
            if r["warmup"] != "0":
                continue
            t = float(r["end_ts"])
            if not (lo <= t <= hi):
                continue
            ip = hm.get(r["request_id"])
            s = SITE_OF_IP.get(ip) if ip else None
            if s is None:
                continue
            if r["ep"] in ("reserve", "recommend"):
                rr[s] += 1
            eq[s] += W_EQ[r["ep"]]
    tot_rr = sum(rr.values())
    dur = hi - lo
    return {"rr_share": {s: round(rr[s] / tot_rr, 3) for s in sorted(rr)}
            if tot_rr else None,
            "observed_eq_rps": {s: round(v / dur, 1) for s, v in sorted(eq.items())}}


def main():
    rundirs = sorted(glob.glob("/home/user/exp/runs/taskB-20260810/v1/v1_*"))
    out = []
    for rd in rundirs:
        if not os.path.exists(os.path.join(rd, "DONE")):
            continue
        meta = json.load(open(os.path.join(rd, "meta.json")))
        r = t2.one_run(rd)
        r["arm"] = arm_of(meta)
        lo, hi = t2.windows(meta)["both"]
        d43 = meta["clock"]["d43_s"]
        d12 = meta["clock"]["d12_s"]
        r["blocked"] = blocked_stats(rd, lo, hi, d12_to_43=(d12 - d43) * -1 + 0)
        r["share"] = share_error(rd, lo, hi)
        out.append(r)
    arms = defaultdict(list)
    for r in out:
        if not r["suspect"]:
            arms[r["arm"]].append(r["windows"]["both"]["viol_pct"])
    agg = {a: {"n": len(v), "runs": v, "mean": round(sum(v) / len(v), 3),
               "half_range": round((max(v) - min(v)) / 2, 3)}
           for a, v in arms.items()}
    json.dump({"runs": out, "agg": agg},
              open("/home/user/exp/analysis/taskB/v1_results.json", "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps(agg, ensure_ascii=False, indent=1))
    for r in out:
        w = r["windows"]["both"]
        print(f"{r['run_id']:>13s} {r['arm']:>13s} both={w['viol_pct']:7.3f}% "
              f"blocked={r['blocked']['blocked_by'] if r['blocked'] else None} "
              f"rr_share={r['share']['rr_share']}")


if __name__ == "__main__":
    main()

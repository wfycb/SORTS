#!/usr/bin/env python3
"""작업B3 검증① 재재실행 분석 — 사전 등록 기준 3종 (PROGRESS.md §D).

  ① C_eff on 위반율 < off (차 > 반범위 합)
  ② 채움 진동 소멸 (search soft 틱): 교대율<0.3 · 인접|ΔS1%|평균<30 ·
     S1 완전회피 비율<1/3 — 세 부지표 전부 (B2: 0.96~1.00 / ~91 / ~0.67)
  ③ 이월 목적지 편차 전 런 ≤6%p (도착 send_ts 기반)
부가: 잔여 위반 구성 분해(사이트×클래스), room/c_eff 열 확인, B2 대비.
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

SITE = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
RUNS = "/home/user/exp/runs/taskB3-20260810/v1"


def arm_of(meta):
    if meta["policy"] != "sorts_reactive":
        return meta["policy"]
    eff = meta["arm"]["effective"]
    a = f"{eff['subset_policy']}+{'on' if eff.get('capacity_check') else 'off'}"
    a += "+soft" if eff.get("soft_assign") else ""
    a += "+ceff" if eff.get("c_eff") else ""
    return a


def osc_metrics(rd):
    """search soft 틱의 S1 가중치 시계열 — 코호트별 (duty, |Δ|, 교대율)."""
    seq = defaultdict(list)
    extrap = 0
    ceff_used = Counter()
    for r in csv.DictReader(open(os.path.join(rd, "decisions.csv"))):
        if r.get("c_eff_extrapolated") == "1":
            extrap += 1
        if r.get("c_eff_s1"):
            ceff_used[r["c_eff_s1"]] += 1
        if r["class"] != "search" or r.get("soft_applied") != "1":
            continue
        w = dict(tok.split(":") for tok in (r["soft_weights"] or "").split("|")
                 if ":" in tok)
        seq[r["cohort"]].append((float(r["ts"]), int(w.get("S1", 0))))
    per = {}
    tot_n = tot_avoid = 0
    for coh, xs in sorted(seq.items()):
        xs.sort()
        s1 = [p for _, p in xs]
        n = len(s1)
        avoid = sum(1 for p in s1 if p == 0)
        deltas = [abs(s1[i + 1] - s1[i]) for i in range(n - 1)]
        flips = sum(1 for i in range(n - 1) if (s1[i] == 0) != (s1[i + 1] == 0))
        per[coh] = {"n": n, "avoid_duty": round(avoid / n, 3) if n else None,
                    "mean_abs_dS1": round(sum(deltas) / len(deltas), 1)
                    if deltas else 0.0,
                    "flip_rate": round(flips / max(n - 1, 1), 3)}
        tot_n += n
        tot_avoid += avoid
    return {"per_cohort": per,
            "overall_avoid": round(tot_avoid / tot_n, 3) if tot_n else None,
            "extrap_rows": extrap,
            "c_eff_s1_hist": dict(ceff_used.most_common(4))}


def carry_check(rd, d12, d43):
    """도착 기반: soft 틱 의도 몫 vs 실측 몫 (사이트별 최대 |편차| %p)."""
    hm = {}
    with gzip.open(os.path.join(rd, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) > 10:
                hm[p[1]] = SITE.get(p[10].split(":")[0])
    arr = defaultdict(lambda: defaultdict(int))
    for c in (1, 2):
        for r in csv.DictReader(open(os.path.join(rd, f"load_c{c}.csv"))):
            if r["ep"] != "search" or r["warmup"] != "0":
                continue
            s = hm.get(r["request_id"])
            if s:
                arr[(c, int(float(r["send_ts"])))][s] += 1
    intend = defaultdict(float)
    meas = defaultdict(int)
    for r in csv.DictReader(open(os.path.join(rd, "decisions.csv"))):
        if r["class"] != "search" or r.get("soft_applied") != "1":
            continue
        ts12 = float(r["ts"]) - d43 + d12
        coh = 1 if r["cohort"] == "c1" else 2
        for s, k in (("S1", "carry_s1"), ("S2", "carry_s2"), ("S3", "carry_s3")):
            intend[s] += float(r[k] or 0)
        intend["S1"] += float(r["soft_overflow_eq"] or 0)
        for s, n in arr.get((coh, int(ts12 + 1)), {}).items():
            meas[s] += n
    ti, tm = sum(intend.values()), sum(meas.values())
    if not ti or not tm:
        return None
    dev = {s: round(abs(100 * intend[s] / ti - 100 * meas.get(s, 0) / tm), 1)
           for s in ("S1", "S2", "S3")}
    return {"intend_share": {s: round(100 * intend[s] / ti, 1)
                             for s in ("S1", "S2", "S3")},
            "meas_share": {s: round(100 * meas.get(s, 0) / tm, 1)
                           for s in ("S1", "S2", "S3")},
            "max_dev_pp": max(dev.values())}


def main():
    out = []
    for rd in sorted(glob.glob(os.path.join(RUNS, "v1c_*"))):
        if not os.path.exists(os.path.join(rd, "DONE")):
            continue
        meta = json.load(open(os.path.join(rd, "meta.json")))
        r = t2.one_run(rd)
        r["arm"] = arm_of(meta)
        r["order"] = os.path.getmtime(os.path.join(rd, "DONE"))
        r["osc"] = osc_metrics(rd)
        r["carry"] = carry_check(rd, meta["clock"]["d12_s"],
                                 meta["clock"]["d43_s"])
        w = r["windows"]["both"]
        r["site_class_viol"] = {k: {"rps": v["rps"], "viol": v["viol_pct"],
                                    "fc95": v["fc_p95"]}
                                for k, v in w["site_class"].items()}
        out.append(r)
    out.sort(key=lambda r: r["order"])

    arms = defaultdict(list)
    for r in out:
        if not r["suspect"]:
            arms[r["arm"]].append(r["windows"]["both"]["viol_pct"])
    agg = {a: {"n": len(v), "runs": [round(x, 3) for x in v],
               "mean": round(sum(v) / len(v), 3),
               "half_range": round((max(v) - min(v)) / 2, 3)}
           for a, v in arms.items()}

    on_a = next((v for k, v in agg.items() if k.endswith("+soft+ceff")), None)
    off_a = next((v for k, v in agg.items()
                  if k.endswith("+soft") and "+ceff" not in k), None)
    crit1 = None
    if on_a and off_a:
        diff = off_a["mean"] - on_a["mean"]
        thr = on_a["half_range"] + off_a["half_range"]
        crit1 = {"diff_pp": round(diff, 3), "threshold_pp": round(thr, 3),
                 "pass": diff > thr}
    on_runs = [r for r in out if r["arm"].endswith("+soft+ceff")
               and not r["suspect"]]
    sub = []
    for r in on_runs:
        for coh, m in r["osc"]["per_cohort"].items():
            sub.append((r["run_id"], coh, m))
    crit2 = {"details": [(rid, coh, m) for rid, coh, m in sub],
             "pass": bool(sub) and all(
                 m["flip_rate"] < 0.3 and m["mean_abs_dS1"] < 30
                 for _, _, m in sub)
             and all(r["osc"]["overall_avoid"] is not None
                     and r["osc"]["overall_avoid"] < 1 / 3 for r in on_runs)}
    devs = [r["carry"]["max_dev_pp"] for r in on_runs if r["carry"]]
    crit3 = {"max_dev_pp_per_run": devs,
             "pass": bool(devs) and all(d <= 6.0 for d in devs)}

    res = {"runs": [{"run_id": r["run_id"], "arm": r["arm"],
                     "suspect": r["suspect"],
                     "viol_both": r["windows"]["both"]["viol_pct"],
                     "osc": r["osc"], "carry": r["carry"],
                     "site_class_viol": r["site_class_viol"]} for r in out],
           "agg": agg, "crit1": crit1, "crit2": crit2, "crit3": crit3,
           "overall_pass": bool(crit1 and crit1["pass"] and crit2["pass"]
                                and crit3["pass"])}
    json.dump(res, open("/home/user/exp/analysis/taskB3/v1c_results.json", "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps({k: res[k] for k in ("agg", "crit1", "crit2", "crit3",
                                          "overall_pass")},
                     ensure_ascii=False, indent=1))
    for r in out:
        print(f"{r['run_id']:>11s} {r['arm']:>26s} "
              f"both={r['windows']['both']['viol_pct']:7.3f}% "
              f"osc={r['osc']['per_cohort']} carry_dev="
              f"{r['carry']['max_dev_pp'] if r['carry'] else None}")


if __name__ == "__main__":
    main()

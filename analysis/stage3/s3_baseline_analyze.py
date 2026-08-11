#!/usr/bin/env python3
"""STAGE3 S3-0 §2.1-3/§2.3 — baseline 등가성 + stale 실측 (무교란 런).

런별:
  - 위반율(전체·코호트별), 달성 rps, 분배(site_share)
  - f_c 등가성: obs_state 정상 상태(마지막 30 s) (site,class) value p50
  - stale 실측 (PREREG_S3 §2-3 단위): src ∈ {prior, prior_fill} tick 을
      · traffic0  = 그 (site,class) 창 표본 n == 0 (I-6 설계 거동 — 제외)
      · warm      = 런 시작 10 s 이내 (기동 초기 — 별도 표기)
      · **starved** = n > 0 인데 n < n_min (트래픽 있는데 미달 — 카운트 대상)
    로 분류. P-S3-3 판정 입력 = starved.
"""
import csv
import json
import os
import sys
from collections import defaultdict

N_MIN_FC = 100


def pctl(xs, q=0.5):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def analyze(rd):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    summ = json.load(open(os.path.join(rd, "summary.json")))
    n_coh = int(meta.get("arm", {}).get("effective", {}).get("n_cohorts", 2))
    # 전 구간(무교란) 위반율 — 코호트 가중 합산과 코호트별
    tot_n = tot_v = 0
    by_coh = {}
    for sec in ("pre", "during", "post"):
        for c, v in (summ["sections"].get(sec, {}).get("by_cohort") or {}).items():
            d = by_coh.setdefault(c, [0, 0])
            d[0] += v["n"]
            d[1] += v["n"] * v["slo_violation_rate"]
            tot_n += v["n"]
            tot_v += v["n"] * v["slo_violation_rate"]
    dur = summ["sections"].get("during", {})

    # obs_state: stale 분류 + f_c 정상 상태 p50
    t0 = None
    stale = defaultdict(int)
    fcv = defaultdict(list)
    rows = list(csv.DictReader(open(os.path.join(rd, "obs_state.csv"))))
    if rows:
        t0 = float(rows[0]["ts"])
        t_end = float(rows[-1]["ts"])
    for r in rows:
        ts = float(r["ts"])
        k = (r["site"], r["class"])
        src = r["src"]
        n = int(r["n"])
        if src == "obs":
            if ts >= t_end - 30 and r["p95_ms"]:
                fcv[k].append(float(r["p95_ms"]))
            continue
        if n == 0:
            stale[("traffic0",) + k] += 1
        elif ts - t0 < 10.0:
            stale[("warm",) + k] += 1
        elif n < N_MIN_FC:
            stale[("starved",) + k] += 1
        else:
            stale[("other",) + k] += 1      # n>=n_min 인데 비-obs = stale_ttl/fill
    starved = {f"{s}/{c}": v for (cat, s, c), v in stale.items() if cat == "starved"}
    other = {f"{s}/{c}": v for (cat, s, c), v in stale.items() if cat == "other"}
    return {
        "run_id": meta["run_id"], "n_cohorts": n_coh,
        "viol_pct_total": round(100 * tot_v / tot_n, 3) if tot_n else None,
        "viol_pct_by_cohort": {c: round(100 * v / n, 3)
                               for c, (n, v) in sorted(by_coh.items())},
        "achieved_rps": dur.get("achieved_rps"),
        "site_share": dur.get("site_share"),
        "fc_p50_obs_last30s": {f"{s}/{c}": round(pctl(v), 3)
                               for (s, c), v in sorted(fcv.items())},
        "stale_starved": starved or 0,
        "stale_other_nonobs": other or 0,
    }


def main():
    root = sys.argv[1]
    out = []
    for rid in sorted(os.listdir(root)):
        rd = os.path.join(root, rid)
        if os.path.isdir(rd) and os.path.exists(os.path.join(rd, "DONE")):
            out.append(analyze(rd))
    json.dump(out, open(os.path.join(root, "s3_baseline_results.json"), "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

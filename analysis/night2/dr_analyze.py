#!/usr/bin/env python3
"""과제4 분석 — 드리프트 판정 (사전 등록 §4.3, PROGRESS.md).

드리프트 없음 = |야간 평균 − 주간 평균| ≤ (야간 반범위 + 주간 반범위), arm별.
주간 기준 (B3 v1c): ceff on 6.50±0.85 / off 30.59±2.85.
드리프트 있음 → 방향·크기 + thermal.json 상관 확인.
"""
import csv
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, "/home/user/exp/analysis/night-20260810")
import t2_policy_repeat as t2  # noqa: E402

DAY = {"on": (6.50, 0.85), "off": (30.59, 2.85)}
RUNS = "/home/user/exp/runs/night2-20260811/dr"


def main():
    arms = defaultdict(list)
    order = []
    therm = {}
    for rd in sorted(glob.glob(os.path.join(RUNS, "dr_*"))):
        if not os.path.exists(os.path.join(rd, "DONE")):
            continue
        meta = json.load(open(os.path.join(rd, "meta.json")))
        eff = meta["arm"]["effective"]
        arm = "on" if eff.get("c_eff") else "off"
        r = t2.one_run(rd)
        v = r["windows"]["both"]["viol_pct"]
        arms[arm].append(v)
        order.append((os.path.basename(rd), arm, round(v, 3), r["suspect"]))
        try:
            tj = json.load(open(os.path.join(rd, "thermal.json")))
            pkg = [ln for ln in tj.get("S1", []) if "x86_pkg_temp" in ln]
            therm[os.path.basename(rd)] = pkg[0] if pkg else None
        except OSError:
            pass
    out = {"runs_in_order": order, "thermal_S1_pkg": therm}
    for arm, vs in sorted(arms.items()):
        mean = sum(vs) / len(vs)
        hr = (max(vs) - min(vs)) / 2
        dmean, dhr = DAY[arm]
        diff = mean - dmean
        drift = abs(diff) > (hr + dhr)
        out[arm] = {"night_runs": [round(v, 3) for v in vs],
                    "night_mean": round(mean, 3), "night_half_range": round(hr, 3),
                    "day_mean": dmean, "day_half_range": dhr,
                    "diff_pp": round(diff, 3),
                    "threshold_pp": round(hr + dhr, 3),
                    "verdict": "드리프트 있음" if drift else "드리프트 없음"}
    out["overall"] = ("드리프트 없음" if all(
        out[a]["verdict"] == "드리프트 없음" for a in arms) else "드리프트 있음")
    json.dump(out, open("/home/user/exp/analysis/night2/dr_results.json", "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

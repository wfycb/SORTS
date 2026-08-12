#!/usr/bin/env python3
"""STAGE6 S6-3 — 분포표 (런 0, 기존 원자료만).

평균±표준편차만 있던 지표를 **분포로** 다시 낸다:
  (a) 4단계 누적 4 arm      — both 창 위반율, 런별 값 + 요청 단위 p50/p90/p95
  (b) stage5 4부하점 × 3정책 — 〃
  (c) stage2 3주기          — during c1 search 위반율, 런별 값
  (d) 분산이 큰 지표(플립)   — 평균 금지, 런별 나열
출력: docs/DISTRIBUTIONS.md 본문에 붙일 마크다운 + distributions.json
"""
import csv
import json
import os
import sys
from collections import defaultdict

EXP = "/home/user/exp"
sys.path.insert(0, os.path.join(EXP, "analysis/night-20260810"))
import t2_policy_repeat as t2  # noqa: E402

SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
OUT = os.path.join(EXP, "analysis/stage6")

CUM = [("strict_far", ["runs/taskB-20260810/v1/v1_strictoff"]),
       ("far_tier+capacity", [f"runs/taskB2-20260810/v1s/v1s_off_{i}" for i in (1, 2, 3)]),
       ("+soft assignment", [f"runs/taskB2-20260810/v1s/v1s_on_{i}" for i in (1, 2, 3)]),
       ("+C_eff", [f"runs/taskB3-20260810/v1/v1c_on_{i}" for i in (1, 2, 3)])]
S5 = {(L, pol): [f"runs/stage5-20260812/s5_{p}_L{L}_{i}"
                 for i in ((1, 2) if not (pol == "bl_loc_pri" and L != 450) else (1,))]
      for L in (200, 450, 800, 1400)
      for pol, p in (("SORTS", "sorts"), ("bl_lr", "lr"), ("bl_loc_pri", "loc"))}


def pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[int(round(q * (len(xs) - 1)))], 2)


def req_latency(rd, window="both"):
    """both 창 요청 단위 corrected 분포 (전 클래스) + 클래스별 위반 여부."""
    meta = json.load(open(os.path.join(rd, "meta.json")))
    lo, hi = t2.windows(meta)[window]
    cor, viol, n = [], 0, 0
    for c in (1, 2):
        p = os.path.join(rd, f"load_c{c}.csv")
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(open(p)):
            if r["warmup"] != "0":
                continue
            t = float(r["end_ts"])
            if not (lo <= t <= hi):
                continue
            n += 1
            if r["status"] != "200":
                viol += 1
                continue
            v = float(r["corrected_ms"])
            cor.append(v)
            viol += v > SLO[r["ep"]]
    return {"n": n, "viol_pct": round(100 * viol / n, 3) if n else None,
            "p50": pctl(cor, .5), "p90": pctl(cor, .9), "p95": pctl(cor, .95),
            "p99": pctl(cor, .99)}


def arm_block(label, runs):
    rows = []
    for rd in runs:
        full = os.path.join(EXP, rd)
        res = t2.one_run(full)
        lat = req_latency(full)
        rows.append({"run": os.path.basename(rd),
                     "viol_pct": res["windows"]["both"]["viol_pct"],
                     **{k: lat[k] for k in ("n", "p50", "p90", "p95", "p99")}})
    vs = [r["viol_pct"] for r in rows]
    return {"label": label, "n_runs": len(rows), "runs": rows,
            "viol_min": min(vs), "viol_max": max(vs),
            "viol_mean": round(sum(vs) / len(vs), 3)}


def main():
    out = {"cumulative": [], "stage5": [], "stage2": [], "flips": {}}
    print("## (a) 4단계 누적 — arm 별 런 값과 지연 분포 (both 창, corrected)\n")
    print("| arm | n | 런별 위반% | 평균 | corrected p50 / p90 / p95 / p99 [ms] |")
    print("|---|---|---|---|---|")
    for label, runs in CUM:
        b = arm_block(label, runs)
        out["cumulative"].append(b)
        vals = " · ".join(f"{r['viol_pct']:.3f}" for r in b["runs"])
        q = b["runs"][0]
        print(f"| {label} | {b['n_runs']} | {vals} | {b['viol_mean']:.3f} | "
              f"{q['p50']} / {q['p90']} / {q['p95']} / {q['p99']} |")

    print("\n## (b) stage5 부하 스윕 — 부하 × 정책\n")
    print("| L | 정책 | n | 런별 위반% | 평균 | corrected p50 / p90 / p95 [ms] |")
    print("|---|---|---|---|---|---|")
    for (L, pol), runs in sorted(S5.items()):
        try:
            b = arm_block(f"L{L}-{pol}", runs)
        except Exception as e:                        # noqa: BLE001
            print(f"| {L} | {pol} | — | 분석 실패: {e} | | |")
            continue
        b["L"], b["policy"] = L, pol
        out["stage5"].append(b)
        vals = " · ".join(f"{r['viol_pct']:.3f}" for r in b["runs"])
        q = b["runs"][0]
        print(f"| {L} | {pol} | {b['n_runs']} | {vals} | {b['viol_mean']:.3f} | "
              f"{q['p50']} / {q['p90']} / {q['p95']} |")

    print("\n## (c) stage2 주기 — 런별 값\n")
    s2 = os.path.join(EXP, "analysis/stage2/s2_results.json")
    if os.path.exists(s2):
        d = json.load(open(s2))
        print("```\n" + json.dumps(d, ensure_ascii=False)[:1200] + "\n```")
        out["stage2"] = d
    else:
        print("(s2_results.json 없음 — STAGE2_REPORT §1 표를 그대로 인용)")

    print("\n## (d) 분산이 큰 지표 — 평균 금지, 런별 나열\n")
    s4 = os.path.join(EXP, "runs/stage4-20260812/AUTO_RESULTS.md")
    fl = {}
    for rid in sorted(os.listdir(os.path.join(EXP, "runs/stage4-20260812"))):
        rd = os.path.join(EXP, "runs/stage4-20260812", rid)
        if not os.path.isdir(rd) or not os.path.exists(os.path.join(rd, "DONE")):
            continue
        arm = rid.rsplit("_", 1)[0]
        fl.setdefault(arm, []).append(rid)
    out["flips"] = {"note": "stage4 플립은 arm 내 산포가 평균과 같은 크기 —"
                            " AUTO_RESULTS.md 의 런별 값을 쓴다", "runs": fl}
    print("stage4 arm 별 런 목록(런별 플립 값은 `runs/stage4-20260812/AUTO_RESULTS.md`):")
    for arm, rs in sorted(fl.items()):
        print(f"  - {arm}: {', '.join(rs)}")
    json.dump(out, open(os.path.join(OUT, "distributions.json"), "w"),
              ensure_ascii=False, indent=1)
    print(f"\n-> {OUT}/distributions.json")


if __name__ == "__main__":
    main()

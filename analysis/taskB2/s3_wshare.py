#!/usr/bin/env python3
"""작업B2 단계0a 판정: C(S3) 상한 + w 공유 (S3 데이터).

입력: knee.py 산출 CSV — 야간 t1c S3 4점(400/800/1200/1600, 48conn) +
s0b 신규 6점(동일 48conn). 판정 기준 (사전 등록 PROGRESS.md §E — s0 과 동일):
  (1) w 공유 — S1 w(0.278/0.178) 고정, C(S3)+클래스 곡선만 적합, 잔차 상대
      RMSE ≤ 2×0.50. 보조: 조성별 브래킷 교집합 비공집합.
  (2) C(S3) 점추정 + 부트스트랩 CI, 하한 832 와 비교.
붕괴점이 안 잡힌 조성은 그대로 보고 (브래킷 상한 = inf).
"""
import json
import random
import sys

sys.path.insert(0, "/home/user/exp/analysis/taskB-prep")
import west  # noqa: E402

west.COMP.update({
    "t1c_s3mix": (2 / 9, 3 / 9, 4 / 9),
    "s0b_s3m0": (2 / 9, 3 / 9, 4 / 9),
    "s0b_s3m1": (0.125, 0.75, 0.125),
    "s0b_s3m2": (0.75, 0.125, 0.125),
})
W_S1 = {"reserve": 0.2775, "recommend": 0.1775}
S1_TRAIN_RMSE = 0.50
CSVS = ["/home/user/exp/analysis/night-20260810/capacity_knee_s3.csv",
        "/home/user/exp/analysis/taskB2/s3_composition.csv"]


def load_s3():
    runs = {}
    import csv as _csv
    for path in CSVS:
        for r in _csv.DictReader(open(path)):
            pre, c = west.comp_of(r["run_id"])
            if pre is None or r["site"] != "S3":
                continue
            d = runs.setdefault(r["run_id"], {"pre": pre, "comp": c,
                                              "total": float(r["achieved_rps_total"]),
                                              "fc": {}, "viol": {}})
            if r["fc_p95"]:
                d["fc"][r["class"]] = float(r["fc_p95"])
                d["viol"][r["class"]] = float(r["viol_rate"])
    out = []
    for rid, d in sorted(runs.items()):
        d["run_id"] = rid
        d["rps"] = {c: d["total"] * s for c, s in zip(west.CLASSES, d["comp"])}
        d["collapsed"] = max(d["viol"].values()) > west.COLLAPSE_VIOL
        out.append(d)
    return out


def fit_c_only(fit_runs, all_runs, wr, wrec, c_grid):
    best = None
    for C in c_grid:
        if not west.feasible_cand(all_runs, wr, wrec, C):
            continue
        sse, params = west.fit_curves(fit_runs, wr, wrec, C)
        if not params or "search" not in params:
            continue
        if best is None or sse < best[0]:
            best = (sse, C, params)
    return best


def main():
    random.seed(20260810)
    allr = load_s3()
    fitr = [r for r in allr if not r["collapsed"]]
    print("S3 런:", [(r["run_id"], r["pre"], round(r["total"], 1),
                    "붕괴" if r["collapsed"] else "정상") for r in allr])

    cg = west.frange(600, 1800, 5)
    wr, wrec = W_S1["reserve"], W_S1["recommend"]

    br = {}
    for r in allr:
        L = west.leff(r, wr, wrec)
        lo, hi = br.setdefault(r["pre"], [0.0, float("inf")])
        if r["collapsed"]:
            br[r["pre"]][1] = min(hi, L)
        else:
            br[r["pre"]][0] = max(lo, L)
    inter_lo = max(v[0] for v in br.values())
    inter_hi = min(v[1] for v in br.values())
    n_bracketed = sum(1 for v in br.values() if v[1] < 1e17)
    print("브래킷 (S1 w 고정):",
          {k: [round(v[0], 1), round(v[1], 1) if v[1] < 1e17 else "inf"]
           for k, v in br.items()})
    print(f"교집합: [{inter_lo:.1f}, "
          f"{inter_hi if inter_hi < 1e17 else float('inf'):.1f}] "
          f"{'— 성립' if inter_lo < inter_hi else '★공집합 — w 공유 의심'} "
          f"(붕괴 브래킷 확보 조성 {n_bracketed}/{len(br)})")

    b = fit_c_only(fitr, allr, wr, wrec, cg)
    if b is None:
        print("★C-only 적합 불가 — 판정 불가로 보고")
        return
    sse, C, params = b
    n = sum(p["n_pts"] for p in params.values())
    rmse = (sse / n) ** 0.5
    bypre = {}
    for r in fitr:
        bypre.setdefault(r["pre"], []).append(r)
    cs = []
    for _ in range(200):
        samp = []
        for pre, rs in bypre.items():
            samp += [random.choice(rs) for _ in rs]
        bb = fit_c_only(samp, allr, wr, wrec, cg)
        if bb:
            cs.append(bb[1])
    cs.sort()
    ci = [cs[int(0.05 * (len(cs) - 1))], cs[int(0.95 * (len(cs) - 1))]] if cs else None

    free = west.grid_fit(fitr, allr)

    verdict_share = rmse <= 2 * S1_TRAIN_RMSE and inter_lo < inter_hi
    out = {
        "w_fixed": {"reserve": wr, "recommend": wrec},
        "C_S3": C, "C_S3_ci90": ci,
        "rmse_rel": round(rmse, 4), "threshold": 2 * S1_TRAIN_RMSE,
        "bracket_intersection": [round(inter_lo, 1),
                                 (round(inter_hi, 1) if inter_hi < 1e17 else None)],
        "brackets_by_comp": {k: [round(v[0], 1),
                                 (round(v[1], 1) if v[1] < 1e17 else None)]
                             for k, v in br.items()},
        "n_comp_bracketed": n_bracketed,
        "free_fit_S3": ({"w_reserve": free[1], "w_recommend": free[2],
                         "C": free[3]} if free else None),
        "w_share_pass": bool(verdict_share),
        "lowerbound_832_in_range": (ci[0] <= 832 if ci else None),
        "curve_params": params,
        "collapsed": [r["run_id"] for r in allr if r["collapsed"]],
    }
    json.dump(out, open("/home/user/exp/analysis/taskB2/s3_wshare.json", "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ("C_S3", "C_S3_ci90", "rmse_rel",
                                          "bracket_intersection",
                                          "w_share_pass", "free_fit_S3")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

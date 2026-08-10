#!/usr/bin/env python3
"""작업B 단계0 판정: w 사이트 공유 + C(S2).

입력: knee.py 산출 CSV (S2 런 — 야간 t1c M0 5점 + s0 신규 6점).
판정 (사전 등록, runs/taskB-20260810/PROGRESS.md):
  (1) w 공유 — S1 적합 w(0.278/0.178) 고정, S2 데이터에 C(S2)+클래스 곡선만
      적합했을 때 잔차 상대 RMSE ≤ 2×(과제A S1 train 0.50). 보조: 브래킷
      교차(3조성의 [max 비붕괴 L_eff, min 붕괴 L_eff] 구간이 교집합을 가짐).
  (2) C(S2) 점추정 + 부트스트랩 CI, 파생값 520 과 비교.
보조: S2 데이터 단독 자유 w 적합 → S1 w 와 비교 (참고용).
"""
import json
import random
import sys

sys.path.insert(0, "/home/user/exp/analysis/taskB-prep")
import west  # noqa: E402

west.COMP.update({
    "t1c_s2mix": (2 / 9, 3 / 9, 4 / 9),
    "s0_s2m0": (2 / 9, 3 / 9, 4 / 9),
    "s0_s2m1": (0.125, 0.75, 0.125),
    "s0_s2m2": (0.75, 0.125, 0.125),
})
W_S1 = {"reserve": 0.2775, "recommend": 0.1775}
S1_TRAIN_RMSE = 0.50
CSV = "/home/user/exp/analysis/taskB/s2_composition.csv"


def load_s2():
    runs = {}
    import csv as _csv
    for r in _csv.DictReader(open(CSV)):
        pre, c = west.comp_of(r["run_id"])
        if pre is None or r["site"] != "S2":
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
    allr = load_s2()
    fitr = [r for r in allr if not r["collapsed"]]
    print("S2 런:", [(r["run_id"], r["pre"], r["total"],
                    "붕괴" if r["collapsed"] else "정상") for r in allr])

    cg = west.frange(350, 750, 5)
    wr, wrec = W_S1["reserve"], W_S1["recommend"]

    # 브래킷 (w 고정): 조성별 [max 비붕괴 L_eff, min 붕괴 L_eff]
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
    print("브래킷 (S1 w 고정):",
          {k: [round(v[0], 1), round(v[1], 1) if v[1] < 1e17 else "inf"]
           for k, v in br.items()})
    print(f"교집합: [{inter_lo:.1f}, "
          f"{inter_hi if inter_hi < 1e17 else float('inf'):.1f}] "
          f"{'— 성립' if inter_lo < inter_hi else '★공집합 — w 공유 의심'}")

    b = fit_c_only(fitr, allr, wr, wrec, cg)
    if b is None:
        print("★C-only 적합 불가 (제약 만족 후보 없음) — 판정 불가로 보고")
        return
    sse, C, params = b
    n = sum(p["n_pts"] for p in params.values())
    rmse = (sse / n) ** 0.5
    # 부트스트랩 (조성 층화)
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

    # 보조: 자유 w 적합 (S2 단독)
    free = west.grid_fit(fitr, allr)

    verdict_share = rmse <= 2 * S1_TRAIN_RMSE and inter_lo < inter_hi
    out = {
        "w_fixed": {"reserve": wr, "recommend": wrec},
        "C_S2": C, "C_S2_ci90": ci,
        "rmse_rel": round(rmse, 4), "threshold": 2 * S1_TRAIN_RMSE,
        "bracket_intersection": [round(inter_lo, 1),
                                 (round(inter_hi, 1) if inter_hi < 1e17 else None)],
        "free_fit_S2": ({"w_reserve": free[1], "w_recommend": free[2],
                         "C": free[3]} if free else None),
        "w_share_pass": bool(verdict_share),
        "derived_520_in_ci": (ci[0] <= 520 <= ci[1]) if ci else None,
        "curve_params": params,
        "collapsed": [r["run_id"] for r in allr if r["collapsed"]],
    }
    json.dump(out, open("/home/user/exp/analysis/taskB/s0_wshare.json", "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

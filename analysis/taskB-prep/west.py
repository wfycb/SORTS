#!/usr/bin/env python3
"""과제 A: 가중 등가 부하 모형 적합.

모형 (PROGRESS.md 사전 등록):
    L_eff(run) = Σ_c w[c]·rps[c],  w[search]=1
    f_c_p95(c, run) = a_c + b_c · u/(1−u),  u = L_eff/C   (M/M/1 형, 클래스별 a,b)
적합: (w_r, w_rec, C) 격자 탐색 × 클래스별 가중 최소제곱(상대오차, 닫힌형).
붕괴 런(위반율>50%, 비정상 구간)은 적합에서 제외하고 표기.
CI: 런 단위 부트스트랩 200회 (조성 층화). 홀드아웃: M3 전체 제외 적합 → M3 예측.

입력: knee.py 가 만든 CSV들 (run_id, class, n, fc_p95, viol_rate, achieved_rps_total ...).
사용: python3 west.py <csv> ... --out weight_fit.json
"""
import argparse
import csv
import json
import random

COMP = {  # run_id 접두 -> (reserve, search, recommend) 트래픽 비중
    "t1_s1mix": (2/9, 3/9, 4/9), "t1b_s1mix": (2/9, 3/9, 4/9),
    "t1_s1srch": (0.0, 1.0, 0.0), "t1b_s1srch": (0.0, 1.0, 0.0),
    "a_m1": (0.125, 0.75, 0.125),
    "a_m2": (0.75, 0.125, 0.125),
    "a_m3": (0.125, 0.125, 0.75),
}
CLASSES = ("reserve", "search", "recommend")
COLLAPSE_VIOL = 0.50


def comp_of(run_id):
    for pre, c in COMP.items():
        if run_id.startswith(pre):
            return pre, c
    return None, None


def load_runs(csvs):
    runs = {}
    for path in csvs:
        for r in csv.DictReader(open(path)):
            pre, c = comp_of(r["run_id"])
            if pre is None or r["site"] != "S1":
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
        d["rps"] = {c: d["total"] * s for c, s in zip(CLASSES, d["comp"])}
        d["collapsed"] = max(d["viol"].values()) > COLLAPSE_VIOL
        out.append(d)
    return out


def leff(run, wr, wrec):
    return run["rps"]["search"] + wr * run["rps"]["reserve"] + wrec * run["rps"]["recommend"]


def fit_curves(runs, wr, wrec, C):
    """클래스별 fc = a + b·x (x=u/(1−u)) 상대오차 WLS. 반환 (sse_rel, params)."""
    sse, params = 0.0, {}
    for cls in CLASSES:
        pts = []
        for r in runs:
            fc = r["fc"].get(cls)
            if fc is None or fc <= 0:      # 음수 f_c(I-2 소응답 과다차감)는 제외
                continue
            u = min(leff(r, wr, wrec) / C, 0.99)
            pts.append((u / (1 - u), fc))
        if len(pts) < 3:
            continue
        # WLS: min Σ ((a+bx−y)/y)^2  -> 가중 1/y^2 닫힌형
        sw = sx = sy = sxx = sxy = 0.0
        for x, y in pts:
            w = 1.0 / (y * y)
            sw += w; sx += w * x; sy += w * y; sxx += w * x * x; sxy += w * x * y
        det = sw * sxx - sx * sx
        if abs(det) < 1e-12:
            continue
        a = (sxx * sy - sx * sxy) / det
        b = (sw * sxy - sx * sy) / det
        if b < 0:
            b = 0.0
            a = sy / sw
        e = sum(((a + b * x - y) / y) ** 2 for x, y in pts)
        sse += e
        params[cls] = {"a": round(a, 3), "b": round(b, 3), "n_pts": len(pts)}
    return sse, params


def feasible_cand(all_runs, wr, wrec, C):
    """모형 의미론 제약: 비붕괴 런은 L_eff < 0.97C (유한 f_c ⇒ 용량 미만),
    붕괴 런은 L_eff ≥ 0.97C (비정상 ⇒ 용량 도달). 이 제약이 없으면
    '용량 초과인데 멀쩡' 같은 모순 후보가 SSE 를 통과한다 (1차 적합에서
    실제 발생 — u 캡 퇴화). 튜닝이 아니라 사전 등록 모형식 그 자체의 강제다."""
    for r in all_runs:
        u = leff(r, wr, wrec) / C
        if r["collapsed"]:
            if u < 0.97:
                return False
        elif u > 0.97:
            return False
    return True


def _scan(fit_runs, all_runs, wr_grid, wrec_grid, C_grid):
    best = None
    for wr in wr_grid:
        for wrec in wrec_grid:
            for C in C_grid:
                if not feasible_cand(all_runs, wr, wrec, C):
                    continue
                sse, params = fit_curves(fit_runs, wr, wrec, C)
                if not params or "search" not in params:
                    continue
                if best is None or sse < best[0]:
                    best = (sse, wr, wrec, C, params)
    return best


def grid_fit(fit_runs, all_runs):
    """2단계 coarse→fine (전수 격자는 계산량 폭발 — 결과는 동일 해상도)."""
    b = _scan(fit_runs, all_runs, frange(0.05, 1.0, 0.05),
              frange(0.05, 1.0, 0.05), frange(250, 340, 5))
    if b is None:
        return None
    _, wr, wrec, C, _ = b
    return _scan(fit_runs, all_runs,
                 frange(max(0.025, wr - 0.06), min(1.2, wr + 0.06), 0.0125),
                 frange(max(0.025, wrec - 0.06), min(1.2, wrec + 0.06), 0.0125),
                 frange(max(245, C - 6), C + 6, 1))


def frange(a, b, step):
    out, x = [], a
    while x <= b + 1e-9:
        out.append(round(x, 4))
        x += step
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csvs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--boot", type=int, default=200)
    a = ap.parse_args()
    random.seed(20260810)

    allruns = load_runs(a.csvs)
    fit_runs = [r for r in allruns if not r["collapsed"]]
    excl = [r["run_id"] for r in allruns if r["collapsed"]]

    sse, wr, wrec, C, params = grid_fit(fit_runs, allruns)
    npts = sum(p["n_pts"] for p in params.values())
    rmse_rel = (sse / npts) ** 0.5

    # 부트스트랩 (조성 층화 재표집)
    boots = []
    bypre = {}
    for r in fit_runs:
        bypre.setdefault(r["pre"], []).append(r)
    for _ in range(a.boot):
        sample = []
        for pre, rs in bypre.items():
            sample += [random.choice(rs) for _ in rs]
        # 제약(붕괴/비붕괴 브래킷)은 재표집하지 않는다 — 관측된 사실이다.
        b = grid_fit(sample, allruns)
        if b:
            boots.append((b[1], b[2], b[3]))
    def ci(vals):
        vs = sorted(vals)
        return [vs[int(0.05 * (len(vs) - 1))], vs[int(0.95 * (len(vs) - 1))]]
    cis = {"w_reserve": ci([b[0] for b in boots]),
           "w_recommend": ci([b[1] for b in boots]),
           "C_S1": ci([b[2] for b in boots])} if boots else None

    # 홀드아웃: M3 제외 적합 -> M3 예측
    tr = [r for r in fit_runs if r["pre"] != "a_m3"]
    ho = [r for r in fit_runs if r["pre"] == "a_m3"]
    all_wo_m3 = [r for r in allruns if r["pre"] != "a_m3"]
    hout = None
    if ho:
        hsse, hwr, hwrec, hC, hparams = grid_fit(tr, all_wo_m3)
        hn = sum(p["n_pts"] for p in hparams.values())
        errs = []
        for r in ho:
            u = min(leff(r, hwr, hwrec) / hC, 0.99)
            x = u / (1 - u)
            for cls in CLASSES:
                fc = r["fc"].get(cls)
                p = hparams.get(cls)
                if fc is None or fc <= 0 or p is None:
                    continue
                pred = p["a"] + p["b"] * x
                errs.append(((pred - fc) / fc) ** 2)
        hout = {"fit_wo_M3": {"w_reserve": hwr, "w_recommend": hwrec, "C_S1": hC},
                "train_rmse_rel": round((hsse / hn) ** 0.5, 4),
                "holdout_rmse_rel": round((sum(errs) / len(errs)) ** 0.5, 4),
                "n_holdout_pts": len(errs)}

    out = {"w": {"search": 1.0, "reserve": wr, "recommend": wrec}, "C_S1": C,
           "rmse_rel_insample": round(rmse_rel, 4), "n_fit_pts": npts,
           "n_fit_runs": len(fit_runs), "excluded_collapsed": excl,
           "curve_params": params, "ci90": cis, "holdout_M3": hout,
           "runs": [{"run_id": r["run_id"], "pre": r["pre"], "total": r["total"],
                     "L_eff": round(leff(r, wr, wrec), 1),
                     "u": round(leff(r, wr, wrec) / C, 3),
                     "fc": r["fc"], "collapsed": r["collapsed"]} for r in allruns]}
    json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ("w", "C_S1", "rmse_rel_insample",
                                          "ci90", "holdout_M3",
                                          "excluded_collapsed")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

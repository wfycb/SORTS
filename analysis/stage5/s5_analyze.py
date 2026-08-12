#!/usr/bin/env python3
"""STAGE5 분석 — 부하 스윕 격차 곡선 + 사전 등록 판정 (PREREG_S5 확정본).

주 지표 = `both` 창 총 위반율(양 코호트·전 클래스, corrected > SLO) —
analysis/night-20260810/t2_policy_repeat.py::one_run 정의 재사용(앵커 6.50 % 와
동일 산식). 방출: s5_results.json · s5_gap_curve.csv(총 부하 축 + K비 축).
"""
import csv
import json
import os
import sys
import math
from collections import defaultdict

sys.path.insert(0, "/home/user/exp/analysis/night-20260810")
import t2_policy_repeat as t2  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "/home/user/exp/runs/stage5-20260812"
W = {"search": 1.0, "reserve": 0.278, "recommend": 0.178}
K_BAND = 105.4          # C_eff(S1 | 1600k) [eq] — PREREG §1.2
K_UNL = 206.1           # C_eff(S1 | 무제한)
SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
POL = {"sorts_reactive": "SORTS", "bl_lr": "bl_lr", "bl_loc_pri": "bl_loc_pri"}


def pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[int(round(q * (len(xs) - 1)))], 1)


def lag_and_rate(rd, meta, lo, hi):
    """both 창의 밀림(corrected−service) p50 · 달성 rps · 오류 수."""
    lag, n, err, svc = [], 0, 0, []
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
                err += 1
                continue
            lag.append(float(r["corrected_ms"]) - float(r["service_ms"]))
            svc.append(float(r["service_ms"]))
    dur = max(hi - lo, 1e-9)
    return {"n": n, "err": err, "rps": round(n / dur, 1),
            "lag_p50": pctl(lag, .5), "lag_p95": pctl(lag, .95),
            "svc_p50": pctl(svc, .5), "svc_p95": pctl(svc, .95)}


def classify_shortfall(rec):
    """P-S5-5′ 갈래 분류. (a) 생성기 구성 부족 / (b) 시스템 용량 초과 / ok."""
    if rec["achieved_pct"] >= 98.0:
        return "ok"
    # (b) 판별: service 자체 상승 + S1 f_c 가 무릎 초과(K비>1)
    if (rec["svc_p50"] or 0) > 32.0 or (rec["k_ratio_demand"] or 0) > 1.0:
        return "b_system"
    return "a_gen"


def one(rd):
    rid = os.path.basename(rd)
    meta = json.load(open(os.path.join(rd, "meta.json")))
    summ = json.load(open(os.path.join(rd, "summary.json")))
    res = t2.one_run(rd)
    win = t2.windows(meta)
    lo, hi = win["both"]
    lr = lag_and_rate(rd, meta, lo, hi)
    L = meta["total_rps"]
    both = res["windows"]["both"]
    sc = both["site_class"]
    eq_served = sum(W[k.split("_", 1)[1]] * v["rps"]
                    for k, v in sc.items() if k.startswith("S1_"))
    s1_n = sum(v["n"] for k, v in sc.items() if k.startswith("S1_"))
    tot_n = sum(v["n"] for v in sc.values()) or 1
    share = s1_n / tot_n
    scale = L / lr["rps"] if lr["rps"] else 1.0        # 미달분 보정 = 수요 환산
    rec = {
        "run": rid, "policy": POL.get(meta["policy"], meta["policy"]),
        "L": L, "conn": meta["connections"],
        "viol_both": both["viol_pct"], "viol_c1only": res["windows"]["c1only"]["viol_pct"],
        "n_both": both["n"], "achieved_rps": lr["rps"],
        "achieved_pct": round(100 * lr["rps"] / L, 1),
        "err": lr["err"], "lag_p50": lr["lag_p50"], "lag_p95": lr["lag_p95"],
        "svc_p50": lr["svc_p50"], "svc_p95": lr["svc_p95"],
        "s1_share": round(share, 4),
        "eq_s1_served": round(eq_served, 1),
        "eq_s1_demand": round(eq_served * scale, 1),
        "k_ratio_served": round(eq_served / K_BAND, 2),
        "k_ratio_demand": round(eq_served * scale / K_BAND, 2),
        "s1_knee_ratio_400": (summ.get("sections", {}).get("during", {})
                              .get("s1_knee_ratio")),
        "site_class": {k: {"rps": v["rps"], "viol_pct": v["viol_pct"],
                           "fc_p95": v["fc_p95"], "slack_ms": v["slack_ms"]}
                       for k, v in sc.items()},
        "suspect": res["suspect"],
    }
    rec["shortfall"] = classify_shortfall(rec)
    return rec


def logx(x0, y0, x1, y1, yt):
    """(x,y) 두 점 사이에서 y=yt 가 되는 x — y 는 로그, x 는 선형."""
    y0 = max(y0, 1e-4)
    y1 = max(y1, 1e-4)
    if y1 == y0:
        return None
    f = (math.log(yt) - math.log(y0)) / (math.log(y1) - math.log(y0))
    return x0 + f * (x1 - x0)


def cross(points, thresh):
    """[(L, y)] 오름차순에서 y 가 처음 thresh 를 넘는 L (로그-선형 보간)."""
    pts = sorted(points)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if y0 <= thresh < y1:
            return logx(x0, y0, x1, y1, thresh)
    if pts and pts[0][1] > thresh:
        return pts[0][0]        # 첫 점에서 이미 초과 — 하한 미브래킷
    return None


def main():
    recs = []
    for rid in sorted(os.listdir(OUT)):
        rd = os.path.join(OUT, rid)
        if not (os.path.isdir(rd) and os.path.exists(os.path.join(rd, "DONE"))):
            continue
        try:
            recs.append(one(rd))
        except Exception as e:                       # noqa: BLE001
            print(f"  ! {rid} 분석 실패: {type(e).__name__}: {e}")
    if not recs:
        print("분석 대상 런 없음")
        return 1

    main_recs = [r for r in recs if r["conn"] == (32 if r["L"] == 1400 else 16)]
    ctrl = [r for r in recs if r not in main_recs]

    # ---- 정책 × 부하 집계
    agg = defaultdict(list)
    for r in main_recs:
        agg[(r["L"], r["policy"])].append(r)
    print("\n## 정책 × 부하 (both 창)\n")
    hdr = (f"{'L':>5s} {'정책':<11s} {'n':>2s} {'위반%':>9s} {'±':>6s} {'S1몫':>6s} "
           f"{'eq(S1)':>7s} {'K비':>5s} {'달성%':>6s} {'밀림p50':>9s} {'분류':>9s}")
    print(hdr)
    table = {}
    for (L, pol), rs in sorted(agg.items()):
        vs = [x["viol_both"] for x in rs if x["viol_both"] is not None]
        mean = sum(vs) / len(vs) if vs else None
        sd = (math.sqrt(sum((v - mean) ** 2 for v in vs) / (len(vs) - 1))
              if len(vs) > 1 else 0.0)
        kd = sum(x["k_ratio_demand"] for x in rs) / len(rs)
        eqd = sum(x["eq_s1_demand"] for x in rs) / len(rs)
        ach = sum(x["achieved_pct"] for x in rs) / len(rs)
        table[(L, pol)] = {"mean": mean, "sd": sd, "n": len(rs), "k": kd,
                           "eq": eqd, "achieved_pct": ach,
                           "runs": [x["run"] for x in rs],
                           "shortfall": [x["shortfall"] for x in rs]}
        print(f"{L:>5d} {pol:<11s} {len(rs):>2d} {mean:>9.3f} {sd:>6.3f} "
              f"{sum(x['s1_share'] for x in rs)/len(rs):>6.3f} {eqd:>7.1f} "
              f"{kd:>5.2f} {ach:>6.1f} {str(rs[0]['lag_p50']):>9s} "
              f"{','.join(sorted(set(x['shortfall'] for x in rs))):>9s}")

    # ---- 격차 곡선
    loads = sorted({L for (L, _) in table})
    curve = []
    for L in loads:
        s = table.get((L, "SORTS"))
        comps = {p: table[(L, p)] for p in ("bl_lr", "bl_loc_pri") if (L, p) in table}
        if not s or not comps:
            continue
        best = min(comps.items(), key=lambda kv: kv[1]["mean"])
        curve.append({"L": L, "sorts": s["mean"], "best_comp": best[0],
                      "best_comp_viol": best[1]["mean"],
                      "gap": round(best[1]["mean"] - s["mean"], 3),
                      "k_lr": table[(L, "bl_lr")]["k"] if (L, "bl_lr") in table else None,
                      "k_sorts": s["k"],
                      "viol_lr": table[(L, "bl_lr")]["mean"] if (L, "bl_lr") in table else None,
                      "viol_loc": (table[(L, "bl_loc_pri")]["mean"]
                                   if (L, "bl_loc_pri") in table else None)})
    print("\n## 격차 곡선\n")
    print(f"{'L':>5s} {'K비(lr)':>8s} {'SORTS':>8s} {'bl_lr':>8s} {'loc_pri':>9s} "
          f"{'최선비교군':>10s} {'격차%p':>8s}")
    for c in curve:
        print(f"{c['L']:>5d} {c['k_lr']:>8.2f} {c['sorts']:>8.3f} "
              f"{str(round(c['viol_lr'], 3)):>8s} {str(round(c['viol_loc'], 3)):>9s} "
              f"{c['best_comp']:>10s} {c['gap']:>8.3f}")

    with open(os.path.join(OUT, "s5_gap_curve.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["L_total_rps", "k_ratio_bl_lr", "k_ratio_sorts",
                    "viol_sorts_pct", "viol_bl_lr_pct", "viol_bl_loc_pri_pct",
                    "best_comparator", "gap_pp"])
        for c in curve:
            w.writerow([c["L"], c["k_lr"], c["k_sorts"], c["sorts"], c["viol_lr"],
                        c["viol_loc"], c["best_comp"], c["gap"]])

    # ---- 사전 등록 판정
    j = {}
    gaps = [(c["L"], c["gap"]) for c in curve]
    if gaps:
        mx = max(gaps, key=lambda x: x[1])
        interior = mx[0] not in (loads[0], loads[-1])
        j["P-S5-1"] = {"max_gap_L": mx[0], "max_gap_pp": mx[1],
                       "max_k_ratio": next(c["k_lr"] for c in curve if c["L"] == mx[0]),
                       "verdict": "역U 성립(내부 점 최대)" if interior
                       else "역U 미확인(최대가 끝점)"}
        lo_gap = next((g for L, g in gaps if L == loads[0]), None)
        j["P-S5-2"] = {"L": loads[0], "gap_pp": lo_gap,
                       "verdict": "통과" if lo_gap is not None and lo_gap >= -0.1
                       else "미달"}
        c05 = cross([(c["L"], max(c["gap"], 1e-4)) for c in curve], 0.5)
        kk = None
        if c05 is not None:
            pts = sorted((c["L"], c["k_lr"]) for c in curve)
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                if x0 <= c05 <= x1:
                    kk = y0 + (y1 - y0) * (c05 - x0) / (x1 - x0)
        branch = ("판정 불가" if kk is None else
                  "A(원 문구 유지)" if kk <= 1.2 else
                  "B(문구 교체)" if kk <= 2.0 else "C(정지·보고)")
        j["P-S5-4"] = {"gap_0.5pp_at_L": c05, "k_ratio_at_onset": kk,
                       "branch": branch}
        for pol in ("bl_loc_pri", "bl_lr", "SORTS"):
            pts = [(L, table[(L, pol)]["mean"]) for L in loads if (L, pol) in table]
            j.setdefault("P-S5-3", {})[pol] = {"pts": pts, "onset_10pct_L": cross(pts, 10.0)}
        o = j["P-S5-3"]
        try:
            ok = (o["bl_loc_pri"]["onset_10pct_L"] is not None and
                  all(o[p]["onset_10pct_L"] is None or
                      o["bl_loc_pri"]["onset_10pct_L"] < o[p]["onset_10pct_L"]
                      for p in ("bl_lr", "SORTS")))
            o["verdict"] = "통과(loc_pri 가 가장 낮은 부하에서 붕괴)" if ok else "미달·확인 필요"
        except Exception:                            # noqa: BLE001
            o["verdict"] = "판정 불가"
    j["P-S5-5'"] = {r["run"]: {"achieved_pct": r["achieved_pct"],
                               "class": r["shortfall"], "lag_p50": r["lag_p50"],
                               "svc_p50": r["svc_p50"]} for r in recs}
    if ctrl:
        base = [r for r in main_recs if r["L"] == 800]
        j["conn_control"] = {
            "runs": {r["run"]: {"policy": r["policy"], "conn": r["conn"],
                                "viol_both": r["viol_both"]} for r in ctrl},
            "delta_vs_16conn": {
                r["policy"]: round(
                    r["viol_both"] - (sum(b["viol_both"] for b in base
                                          if b["policy"] == r["policy"]) /
                                      max(len([b for b in base if b["policy"] == r["policy"]]), 1)), 3)
                for r in ctrl
                if any(b["policy"] == r["policy"] for b in base)}}
    print("\n## 사전 등록 판정\n")
    print(json.dumps(j, ensure_ascii=False, indent=1))
    json.dump({"runs": recs, "table": {f"{L}|{p}": v for (L, p), v in table.items()},
               "curve": curve, "judgments": j},
              open(os.path.join(OUT, "s5_results.json"), "w"),
              ensure_ascii=False, indent=1)
    print(f"\n-> {OUT}/s5_results.json · s5_gap_curve.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())

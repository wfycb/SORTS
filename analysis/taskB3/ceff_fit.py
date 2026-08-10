#!/usr/bin/env python3
"""작업B3 단계0 판정: C_eff(S1, band) — SLO-유효 용량 (사전 등록 §A).

정의: 밴드에서 max-클래스 위반율이 1% 를 넘지 않는 최대 등가 도착률.
산출: 위반율 로그-선형 보간의 1% 교차. CI = 교차 괄호 측정 2점.
입력: knee.py CSV (신규 s0_* ) + 야간 t1 혼합 (무제한, 16conn 동일 프로파일).
"""
import csv
import json
import math

EQ_PER_RAW = (2 / 9) * 0.278 + (3 / 9) * 1.0 + (4 / 9) * 0.178   # M0
CSVS = ["/home/user/exp/analysis/night-20260810/capacity_knee_s1.csv",
        "/home/user/exp/analysis/taskB3/s0_ceff.csv"]
THRESH = 0.01


def band_of(run_id):
    if run_id.startswith("t1_s1mix") or run_id.startswith("s0_unl"):
        return "unlimited"
    if run_id.startswith("s0_b2300"):
        return "2300"
    if run_id.startswith("s0_b1600"):
        return "1600"
    return None


def main():
    pts = {}
    for path in CSVS:
        try:
            rows = list(csv.DictReader(open(path)))
        except OSError:
            continue
        for r in rows:
            b = band_of(r["run_id"])
            if b is None or r["site"] != "S1" or r.get("mix") not in ("mixed", None, ""):
                continue
            d = pts.setdefault((b, r["run_id"]),
                               {"eq": float(r["achieved_rps_total"]) * EQ_PER_RAW,
                                "viol": {}, "fc": {}})
            d["viol"][r["class"]] = float(r["viol_rate"])
            d["fc"][r["class"]] = float(r["fc_p95"]) if r["fc_p95"] else None

    out = {}
    for band in ("unlimited", "2300", "1600"):
        series = sorted(({"run": rid, "eq": d["eq"],
                          "max_viol": max(d["viol"].values()),
                          "viol": d["viol"], "fc": d["fc"]}
                         for (b, rid), d in pts.items() if b == band),
                        key=lambda x: x["eq"])
        print(f"== band {band}")
        for s in series:
            print(f"  {s['run']:>16s} eq={s['eq']:6.1f} max_viol={s['max_viol']:8.5f} "
                  f"viol={ {k: round(v,4) for k,v in s['viol'].items()} }")
        below = [s for s in series if s["max_viol"] <= THRESH]
        above = [s for s in series if s["max_viol"] > THRESH]
        if not below or not above:
            out[band] = {"c_eff": None, "note": "브래킷 실패 "
                         + ("(전부 >1%)" if not below else "(전부 ≤1%)"),
                         "series": [(s["run"], round(s["eq"], 1),
                                     s["max_viol"]) for s in series]}
            print(f"  ★브래킷 실패: {out[band]['note']}")
            continue
        lo = max(below, key=lambda s: s["eq"])
        hi = min((s for s in above if s["eq"] > lo["eq"]),
                 key=lambda s: s["eq"], default=None)
        if hi is None:
            out[band] = {"c_eff": None, "note": "역전(고부하 비위반) — 판정 불가",
                         "series": [(s["run"], round(s["eq"], 1),
                                     s["max_viol"]) for s in series]}
            print("  ★역전 — 판정 불가")
            continue
        v0 = max(lo["max_viol"], 1e-5)
        v1 = hi["max_viol"]
        f = (math.log(THRESH) - math.log(v0)) / (math.log(v1) - math.log(v0))
        c = lo["eq"] + f * (hi["eq"] - lo["eq"])
        out[band] = {"c_eff": round(c, 1), "ci_bracket": [round(lo["eq"], 1),
                                                          round(hi["eq"], 1)],
                     "cross_pts": [lo["run"], hi["run"]],
                     "series": [(s["run"], round(s["eq"], 1), s["max_viol"])
                                for s in series]}
        print(f"  C_eff={c:.1f} eq (브래킷 [{lo['eq']:.1f}, {hi['eq']:.1f}], "
              f"{lo['run']}~{hi['run']})")
    if out.get("unlimited", {}).get("c_eff"):
        u = out["unlimited"]["c_eff"]
        for b in ("2300", "1600"):
            if out.get(b, {}).get("c_eff"):
                out[b]["drop_vs_unlimited_pct"] = round(
                    100 * (1 - out[b]["c_eff"] / u), 1)
    json.dump(out, open("/home/user/exp/analysis/taskB3/ceff_fit.json", "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps({b: {k: v for k, v in d.items() if k != "series"}
                      for b, d in out.items()}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""GRAPH-DATA v1 §3 검증 — prof/ 산출 CSV 를 기존 그림·대장·설정과 대조."""
import csv, json, os, sys
EXP = "/home/user/exp"
P = os.path.join(EXP, "figures/data/prof")
D = os.path.join(EXP, "figures/data")


def rd(p, enc="utf-8-sig"):
    return list(csv.DictReader(open(p, encoding=enc)))


def chk(name, a, b, tol, unit=""):
    ok = (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= tol)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name:<52} {a} vs {b}  (허용 {tol}{unit})")
    return ok


allok = True
print("1. 설정 단일 출처(sorts.yaml) 대조")
y = open(os.path.join(EXP, "sorts.yaml")).read()
g1 = rd(os.path.join(P, "G1_delay_breakdown.csv"))
slo = {r["class"]: float(r["slo_ms"]) for r in g1}
dnet = {r["site"]: float(r["backhaul_ms"]) for r in g1}
allok &= chk("SLO reserve/search/recommend", tuple(slo[k] for k in ("reserve", "search", "recommend")) == (35.0, 45.0, 35.0), True, 0)
allok &= chk("d_net S1/S2/S3", tuple(dnet[k] for k in ("S1", "S2", "S3")) == (2.0, 15.0, 25.0), True, 0)

print("\n2. G1 잔차 (Σ4구간 p50 − 실측 전체 p50)")
res = [abs(float(r["residual_ms"])) for r in g1]
g1p = rd(os.path.join(P, "G1_delay_breakdown_p95.csv"))
resp = [abs(float(r["residual_ms"])) for r in g1p]
print(f"  p50 판: n={len(res)}  최대 {max(res):.3f} ms  중앙 {sorted(res)[len(res)//2]:.3f} ms")
print(f"  p95 판: n={len(resp)} 최대 {max(resp):.3f} ms  중앙 {sorted(resp)[len(resp)//2]:.3f} ms")
allok &= chk("p50 잔차 최대 < 0.10 ms", max(res) < 0.10, True, 0)

print("\n3. G1 radio_ms_model ↔ 예산 표(F2_budget_table.csv) d_acc")
bt = {(r["class"], r["band_kbit"]): float(r["d_acc_ms"]) for r in rd(os.path.join(D, "F2_budget_table.csv"), "utf-8")}
for cls in ("search", "reserve", "recommend"):
    got = {float(r["radio_ms_model"]) for r in g1 if r["class"] == cls and r["band"] == "degraded"}
    allok &= chk(f"d_acc({cls}@1600k)", got.pop(), bt[(cls, "1600")], 0.001, " ms")

print("\n4. G2 search 사이트 몫 ↔ f1_L450_sites_by_class.csv (SORTS)")
g2 = {int(r["t_sec"]): r for r in rd(os.path.join(P, "G2_radio_timeseries.csv"))}
mx = 0.0
n = 0
for r in rd(os.path.join(D, "f1_L450_sites_by_class.csv"), "utf-8"):
    if r["policy"] != "SORTS" or r["class"] != "search":
        continue
    t = int(float(r["t_rel_s"]))
    if t not in g2 or not g2[t]["share_s1_pct"]:
        continue
    for s in ("S1", "S2", "S3"):
        mx = max(mx, abs(float(r[f"{s}_pct"]) - float(g2[t][f"share_{s.lower()}_pct"])))
    n += 1
allok &= chk(f"몫 최대 절대차 ({n}초 대조)", mx, 0.0, 0.02, " %p")

print("\n5. G4 ↔ f1_L450_sites.csv (전 클래스 몫, 값 재사용)")
src = {(r["policy"], int(float(r["t_rel_s"]))): r for r in rd(os.path.join(D, "f1_L450_sites.csv"), "utf-8")}
mx = 0.0
for r in rd(os.path.join(P, "G4_policy_share.csv")):
    o = src[(r["policy"], int(r["t_sec"]))]
    for s in ("S1", "S2", "S3"):
        mx = max(mx, abs(float(o[f"{s}_pct"]) - float(r[f"share_{s.lower()}_pct"])))
allok &= chk("몫 최대 절대차", mx, 0.0, 0.0, " %p")

print("\n6. G5 ↔ f3_cumulative.csv / 대장 §4 (95.0 → 74.6 → 28.1 → 6.50)")
g5 = rd(os.path.join(P, "G5_layer_cumulative.csv"))
f3 = rd(os.path.join(D, "f3_cumulative.csv"), "utf-8")
for a, b in zip(g5, f3):
    allok &= chk(f"{a['layer']}", float(a["violation_pct"]), float(b["viol_pct"]), 0.0, " %")

print("\n6b. G3/G3b 예산 열 항등식·짝 대조")
import statistics as _st
for f, lbl, arm in (("G3_server_timeseries.csv", "T3", "far_tier"),
                    ("G3b_server_timeseries_strictfar.csv", "T2", "strict_far")):
    rr = rd(os.path.join(P, f))
    bud = 45.0 - 5.0 - 25.0 - 4474 * 8 / 6000 * 1.10          # c1(6000k) 기준
    ok = all(abs(float(r["fc_budget_s3_ms"]) - bud) <= 0.005 for r in rr)
    ident = all(abs((float(r["fc_budget_s3_ms"]) - float(r["fc_s3_ms_dnet"]))
                    - float(r["budget_s3_ms"])) <= 0.011
                for r in rr if r["budget_s3_ms"] and r["fc_s3_ms_dnet"])
    med = lambda ph, c: round(_st.median([float(r[c]) for r in rr
                                          if r["phase"] == ph and r[c]]), 2)
    neg = sum(1 for r in rr if r["phase"] == "stress" and r["budget_s3_ms"]
              and float(r["budget_s3_ms"]) < 0)
    nst = sum(1 for r in rr if r["phase"] == "stress")
    print(f"  [{'OK ' if ok and ident else 'FAIL'}] {lbl} {arm:<10} "
          f"f_c(S3) {med('pre','fc_s3_ms_dnet')}->{med('stress','fc_s3_ms_dnet')} ms  "
          f"예산잔여 {med('pre','budget_s3_ms')}->{med('stress','budget_s3_ms')} ms  "
          f"소진 {neg}/{nst}초  S3몫 {med('pre','share_s3_pct')}->{med('stress','share_s3_pct')} %")
    allok &= ok and ident

print("\n7. G4b both 창 위반율 ↔ STAGE5_REPORT §(s5_results.json, n=2 평균·sd)")
g4b = {(r["policy"], r["window"]): r for r in rd(os.path.join(P, "G4b_policy_violation.csv"))}
s5 = json.load(open(os.path.join(EXP, "runs/stage5-20260812/s5_results.json")))["table"]
for pol in ("SORTS", "bl_lr", "bl_loc_pri"):
    t = s5[f"450|{pol}"]
    mine = float(g4b[(pol, "both")]["viol_pct"])
    half = t["sd"] * (2 ** 0.5) / 2          # n=2: 두 런은 mean ± sd/sqrt(2)*... = mean ± |a-b|/2
    lo, hi = t["mean"] - half, t["mean"] + half
    ok = abs(min(abs(mine - lo), abs(mine - hi))) <= 0.002
    print(f"  [{'OK ' if ok else 'FAIL'}] {pol:<11} 런_1 {mine:.3f} % ∈ 2런 {{{lo:.3f}, {hi:.3f}}} "
          f"(대장 평균 {t['mean']:.3f} ± sd {t['sd']:.3f}, n={t['n']})")
    allok &= ok

print("\n전체 판정:", "통과" if allok else "불일치 있음")
sys.exit(0 if allok else 1)

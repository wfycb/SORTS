#!/usr/bin/env python3
"""P4 핵심 수치 재계산 (지시서 v11). **원자료에서 다시 계산한다** — 보고서 인용 금지.

원자료:
  runs/demo-20260805/<run>/load_c{1,2}.csv   부하생성기 요청 레코드
  runs/demo-20260805/<run>/envoy_access.log.gz  front Envoy access log (18필드)
  runs/demo-20260805/<run>/{meta,marks,decisions}.json/csv
  n2/n2_*.csv, n2/n2_*_envoy.csv                N2 밴드별 (Envoy 무관측 증명)

조인 키: x-request-id (필드2). 구간 절단은 러너와 동일 (marks ±2s, end_ts .12).
"""
import csv
import glob
import gzip
import json
import os

RUNS = os.path.expanduser("~/exp/runs/demo-20260805")
N2 = os.path.expanduser("~/n2")
OUT = os.path.expanduser("~/exp/presentation/tables")
os.makedirs(OUT, exist_ok=True)

SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
EXPECT_BYTES = {"reserve": 36, "search": 4474, "recommend": 200}
SITE_OF = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
C1, C2 = "10.46.0.6", "10.46.0.7"
EP_OF = {"/hotels": "search", "/reservation": "reserve", "/recommendations": "recommend"}


def pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[int(round(q * (len(xs) - 1)))], 3)


def jload(rid, name):
    return json.load(open(os.path.join(RUNS, rid, name)))


def hostmap(rid):
    """request_id -> (site, envoy_start_ts, duration_ms, upstream_rt_us)"""
    hm = {}
    with gzip.open(os.path.join(RUNS, rid, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 18:
                continue
            try:
                site = SITE_OF.get(p[10].split(":")[0])
                dur = float(p[6]) if p[6].strip() else None
                us = int(p[17]) if p[17].strip().isdigit() else None
                hm[p[1]] = (site, float(p[0]), dur, us)
            except ValueError:
                continue
    return hm


def load_rows(rid):
    rows = []
    for c in (1, 2):
        for r in csv.DictReader(open(os.path.join(RUNS, rid, f"load_c{c}.csv"))):
            if r["warmup"] != "0":
                continue
            r["cohort"] = c
            rows.append(r)
    return rows


def is_valid(r):
    if r["status"] != "200":
        return False
    e = EXPECT_BYTES.get(r["ep"])
    if e is None:
        return False
    b = int(r["bytes_recv"])
    return abs(b - e) <= (e * 0.10 if e > 1000 else 0)


def violated(r):
    return (not is_valid(r)) or float(r["corrected_ms"]) > SLO[r["ep"]]


def section(rid, name):
    return jload(rid, "meta.json")["sections_abs_12"][name]


POLICIES = [("D2_sorts_radio", "SORTS"), ("D4_rr_radio", "Round Robin"),
            ("D5_lr_radio", "Least Request"), ("D3_s3_radio", "Static-Far (no reaction)")]

print("=" * 100)
print("표 1. radio 교란 during — 코호트1 search 위반율 (원자료 재계산)")
t1 = []
cache = {}
for rid, label in POLICIES:
    lo, hi = section(rid, "during")
    rows = load_rows(rid)
    cache[rid] = rows
    sub = [r for r in rows if r["cohort"] == 1 and r["ep"] == "search"
           and lo <= float(r["end_ts"]) < hi]
    v = sum(1 for r in sub if violated(r))
    t1.append({"policy": label, "run": rid, "n": len(sub), "violations": v,
               "violation_pct": round(100 * v / len(sub), 4) if sub else None,
               "source": f"runs/demo-20260805/{rid}/load_c1.csv + meta.json(sections_abs_12.during)"})
    print(f"  {label:26s} n={len(sub):6d}  위반={v:6d}  {100*v/len(sub):8.4f}%")
with open(f"{OUT}/p4_t1_violation_by_policy.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(t1[0])); w.writeheader(); w.writerows(t1)

print("=" * 100)
print("표 2. '위반율 = S3 몫' 검증 — 사이트별 조건부 위반율")
t2 = []
for rid, label in POLICIES:
    lo, hi = section(rid, "during")
    hm = hostmap(rid)
    sub = [r for r in cache[rid] if r["cohort"] == 1 and r["ep"] == "search"
           and lo <= float(r["end_ts"]) < hi]
    bysite = {}
    unjoined = 0
    for r in sub:
        h = hm.get(r["request_id"])
        s = h[0] if h else None
        if s is None:
            unjoined += 1
            continue
        d = bysite.setdefault(s, [0, 0])
        d[0] += 1
        d[1] += violated(r)
    tot = sum(d[0] for d in bysite.values())
    s3n, s3v = bysite.get("S3", [0, 0])
    o_n = sum(bysite[s][0] for s in bysite if s != "S3")
    o_v = sum(bysite[s][1] for s in bysite if s != "S3")
    share_s3 = 100 * s3n / tot if tot else 0
    viol = 100 * sum(d[1] for d in bysite.values()) / tot if tot else 0
    row = {"policy": label, "n_joined": tot, "unjoined": unjoined,
           "S3_share_pct": round(share_s3, 3),
           "violation_pct": round(viol, 3),
           "diff_pp": round(viol - share_s3, 3),
           "viol_given_S3_pct": round(100 * s3v / s3n, 3) if s3n else None,
           "viol_given_S1S2_pct": round(100 * o_v / o_n, 3) if o_n else None,
           "n_S3": s3n, "n_S1S2": o_n,
           "source": f"{rid}/load_c1.csv JOIN envoy_access.log.gz on x-request-id"}
    t2.append(row)
    print(f"  {label:26s} S3몫={share_s3:7.3f}%  위반율={viol:7.3f}%  차이={viol-share_s3:+6.3f}%p"
          f"  | S3행 위반={row['viol_given_S3_pct']}%  S1S2행 위반={row['viol_given_S1S2_pct']}%"
          f"  (미조인 {unjoined})")
with open(f"{OUT}/p4_t2_share_equals_violation.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(t2[0])); w.writeheader(); w.writerows(t2)

print("=" * 100)
print("표 3. D2 6유닛 분리 (during)")
rid = "D2_sorts_radio"
hm = hostmap(rid)
lo, hi = section(rid, "during")
t3 = []
for coh, cn in ((1, "c1"), (2, "c2")):
    for ep in ("reserve", "search", "recommend"):
        sub = [r for r in cache[rid] if r["cohort"] == coh and r["ep"] == ep
               and lo <= float(r["end_ts"]) < hi]
        dist = {}
        for r in sub:
            h = hm.get(r["request_id"])
            if h and h[0]:
                dist[h[0]] = dist.get(h[0], 0) + 1
        tot = sum(dist.values()) or 1
        v = sum(1 for r in sub if violated(r))
        cor = [float(r["corrected_ms"]) for r in sub if is_valid(r)]
        t3.append({"unit": f"{cn}_{ep}", "n": len(sub),
                   "S1_pct": round(100 * dist.get("S1", 0) / tot, 2),
                   "S2_pct": round(100 * dist.get("S2", 0) / tot, 2),
                   "S3_pct": round(100 * dist.get("S3", 0) / tot, 2),
                   "violation_pct": round(100 * v / len(sub), 4) if sub else None,
                   "corrected_p50": pctl(cor, .5), "corrected_p95": pctl(cor, .95),
                   "source": f"{rid} load_c{coh}.csv JOIN envoy log"})
        print(f"  {cn}_{ep:10s} n={len(sub):5d}  S1/S2/S3={t3[-1]['S1_pct']:6.2f}/"
              f"{t3[-1]['S2_pct']:6.2f}/{t3[-1]['S3_pct']:6.2f}  위반={t3[-1]['violation_pct']:7.4f}%"
              f"  p95={t3[-1]['corrected_p95']}")
with open(f"{OUT}/p4_t3_six_units.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(t3[0])); w.writeheader(); w.writerows(t3)

print("=" * 100)
print("표 4. 반응 지연과 잔여 위반")
meta = jload(rid, "meta.json")
d12, d43 = meta["clock"]["d12_s"], meta["clock"]["d43_s"]
marks = jload(rid, "marks.json")["marks"]
on = next(m for m in marks if m["what"] == "radio_on")
inj43 = on["t43_done"]
inj12 = inj43 - d43 + d12
dec = list(csv.DictReader(open(os.path.join(RUNS, rid, "decisions.csv"))))
det = next(r for r in dec if r["cohort"] == "c1" and r["class"] == "search"
           and r["changed"] == "1" and r["chosen_site"] == "S2")
det43 = float(det["ts"])
app43 = det43 + float(det["apply_latency_ms"]) / 1000.0
app12 = app43 - d43 + d12
c1s = [r for r in cache[rid] if r["cohort"] == 1 and r["ep"] == "search"]
gap = [r for r in c1s if inj12 <= float(r["end_ts"]) < app12]
gapv = sum(1 for r in gap if violated(r))
dsub = [r for r in c1s if lo <= float(r["end_ts"]) < hi]
dv = sum(1 for r in dsub if violated(r))
t4 = {"inject_done_t43": round(inj43, 3), "detect_t43": round(det43, 3),
      "apply_done_t43": round(app43, 3),
      "detect_delay_s": round(det43 - inj43, 3),
      "apply_latency_ms": det["apply_latency_ms"],
      "total_reaction_s": round(app43 - inj43, 3),
      "gap_n": len(gap), "gap_violations": gapv,
      "during_violations": dv,
      "gap_share_of_during_pct": round(100 * gapv / dv, 2) if dv else None,
      "source": f"{rid}/marks.json(radio_on.t43_done) + decisions.csv + load_c1.csv"}
for k, v in t4.items():
    print(f"  {k:26s} {v}")
with open(f"{OUT}/p4_t4_residual.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(t4)); w.writeheader(); w.writerow(t4)

# 1초 버킷
buckets = {}
for r in c1s:
    t = float(r["end_ts"]) - inj12
    if -5 <= t < 8:
        b = int(t) if t >= 0 else int(t) - 1
        d = buckets.setdefault(b, [0, 0])
        d[0] += 1
        d[1] += violated(r)
with open(f"{OUT}/p4_t4b_1s_buckets.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["t_rel_inject_s", "n", "violations", "violation_pct"])
    for b in sorted(buckets):
        n, v = buckets[b]
        w.writerow([b, n, v, round(100 * v / n, 2)])
print("  1초 버킷 (주입 기준):")
for b in sorted(buckets):
    n, v = buckets[b]
    print(f"    t{b:+d}s  n={n:4d} 위반={v:4d} ({100*v/n:5.1f}%)")

print("=" * 100)
print("표 5. Envoy 구조적 무관측 (N2 원자료)")
t5 = []
BANDS = [("A_baseline", "무교란"), ("B_normal", "정상 20Mbps"), ("B_fair", "Fair 4.5Mbps"),
         ("B_poor", "Poor 2.3Mbps"), ("B_extreme", "극단 1.6Mbps")]
for tag, label in BANDS:
    p = f"{N2}/n2_{tag}_search.csv"
    pe = f"{N2}/n2_{tag}_search_envoy.csv"
    if not (os.path.exists(p) and os.path.exists(pe)):
        continue
    e2e = sorted(float(r["service_ms"]) for r in csv.DictReader(open(p))
                 if r["status"] == "200")
    # envoy CSV 는 헤더 없는 원시 access log. 필드7(idx6)=%DURATION%(정수 ms),
    # 필드16(idx15)=COMMON_DURATION(DS_RX_BEG:DS_TX_END:us) — 같은 양의 µs 정밀도.
    dur_ms, dur_us = [], []
    for line in open(pe):
        f_ = line.rstrip("\n").split(",")
        if len(f_) < 16 or f_[4] != "200":
            continue
        if f_[6].strip().isdigit():
            dur_ms.append(float(f_[6]))
        if f_[15].strip().isdigit():
            dur_us.append(int(f_[15]) / 1000.0)
    t5.append({"band": label, "n": len(e2e),
               "loadgen_e2e_p50_ms": pctl(e2e, .5),
               "envoy_duration_p50_ms": pctl(dur_ms, .5),
               "envoy_duration_us_p50_ms": pctl(dur_us, .5),
               "radio_segment_p50_ms": round(pctl(e2e, .5) - pctl(dur_us, .5), 3),
               "source": f"n2/n2_{tag}_search.csv[service_ms] , "
                         f"n2/n2_{tag}_search_envoy.csv[fld7 %DURATION%, fld16 DS_RX_BEG:DS_TX_END:us]"})
for r in t5:
    print(f"  {r['band']:14s} E2E p50={r['loadgen_e2e_p50_ms']:8.3f}  "
          f"Envoy DURATION p50={r['envoy_duration_p50_ms']:6.1f}ms(정수) "
          f"/ {r['envoy_duration_us_p50_ms']:7.3f}ms(µs)  "
          f"무선구간={r['radio_segment_p50_ms']:8.3f}")
if len(t5) >= 2:
    lo_b = [r for r in t5 if "정상" in r["band"]]
    hi_b = [r for r in t5 if "극단" in r["band"]]
    if lo_b and hi_b:
        dr = hi_b[0]["radio_segment_p50_ms"] - lo_b[0]["radio_segment_p50_ms"]
        de = hi_b[0]["envoy_duration_us_p50_ms"] - lo_b[0]["envoy_duration_us_p50_ms"]
        print(f"  >> 정상 -> 극단: 무선구간 {dr:+.3f} ms 변하는 동안 "
              f"Envoy 관측 {de:+.3f} ms 변함")
        t5.append({"band": "정상->극단 변화량", "n": "",
                   "loadgen_e2e_p50_ms": round(hi_b[0]["loadgen_e2e_p50_ms"] - lo_b[0]["loadgen_e2e_p50_ms"], 3),
                   "envoy_duration_p50_ms": round(hi_b[0]["envoy_duration_p50_ms"] - lo_b[0]["envoy_duration_p50_ms"], 3),
                   "envoy_duration_us_p50_ms": round(de, 3),
                   "radio_segment_p50_ms": round(dr, 3), "source": "위 두 행의 차"})
with open(f"{OUT}/p4_t5_envoy_blind.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(t5[0])); w.writeheader(); w.writerows(t5)

print("=" * 100)
print("표 6. D6 램프 전환 + 계단 검산")
rid6 = "D6_sorts_ramp"
meta6 = jload(rid6, "meta.json")
t0_43 = meta6["t_meas"] - meta6["clock"]["d12_s"] + meta6["clock"]["d43_s"]
dec6 = list(csv.DictReader(open(os.path.join(RUNS, rid6, "decisions.csv"))))
cfg = {"GB": 5.0, "SLO": 45.0,
       "d_net": {"S1": 2.0, "S2": 15.0, "S3": 25.0},
       "f_c": {"S1": 6.899, "S2": 4.287, "S3": 4.019}, "ovh": 1.10, "bytes": 4474}
t6 = []
for r in dec6:
    if r["changed"] != "1" or r["cohort"] != "c1" or r["class"] != "search":
        continue
    rate = r["observed_rate_kbit"]
    if rate:
        d_acc = cfg["bytes"] * 8 / float(rate) * cfg["ovh"]
        sl = {s: cfg["SLO"] - cfg["GB"] - cfg["d_net"][s] - cfg["f_c"][s] - d_acc
              for s in ("S3", "S2", "S1")}
        recomputed = next((s for s in ("S3", "S2", "S1") if sl[s] > 0), "S1")
    else:
        d_acc, sl, recomputed = 0.0, {"S1": None, "S2": None, "S3": None}, "S3"
    t6.append({"t_rel_s": round(float(r["ts"]) - t0_43, 2),
               "observed_rate_kbit": rate or "(무제한)",
               "d_acc_ms": round(d_acc, 3),
               "slack_S3": None if sl["S3"] is None else round(sl["S3"], 2),
               "slack_S2": None if sl["S2"] is None else round(sl["S2"], 2),
               "slack_S1": None if sl["S1"] is None else round(sl["S1"], 2),
               "chosen": r["chosen_site"], "recomputed": recomputed,
               "match": "O" if r["chosen_site"] == recomputed else "불일치",
               "apply_latency_ms": r["apply_latency_ms"],
               "source": f"{rid6}/decisions.csv + 독립 재계산(v10 §0 파라미터)"})
    x = t6[-1]
    print(f"  t={x['t_rel_s']:+8.2f}s rate={str(x['observed_rate_kbit']):>10s}kbit "
          f"d_acc={x['d_acc_ms']:7.3f}  slack S3/S2/S1={x['slack_S3']}/{x['slack_S2']}/{x['slack_S1']}"
          f"  선택={x['chosen']} 재계산={x['recomputed']} {x['match']}")
with open(f"{OUT}/p4_t6_ramp_transitions.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(t6[0])); w.writeheader(); w.writerows(t6)
print("\n완료 ->", OUT)

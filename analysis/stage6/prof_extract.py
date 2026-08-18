#!/usr/bin/env python3
"""지도교수 회신 대응 — 지연 요소 분해 CSV 추출 (GRAPH-DATA v1, 2026-08-13).

새 런 없음. 기존 원자료(`runs/**/load_c*.csv` + `envoy_access.log.gz`)만 훑는다.
요청 단위 조인 키는 `x-request-id` (부하생성기 생성 → Envoy 필드 2 보존).

산출(`figures/data/prof/`, 전부 UTF-8 BOM):
  G1_delay_breakdown.csv      / _p95.csv   구간 분해 (policy×class×band×site)
  G1b_model_vs_measured.csv                d_acc 모델 vs 실측 증분 + 헤더 검산
  G2_radio_timeseries.csv                  무선 축 1 s 시계열 (SORTS L450)
  G3_server_timeseries.csv                 서버 축 1 s 시계열 (T3, S3 stress)
  G4_policy_share.csv / G4b_policy_violation.csv   정책별 배정·both 창 위반율
  G5_layer_cumulative.csv                  층별 기여

분해 정의 (전부 같은 호스트에서 잰 구간 길이의 차분 — 시계 보정 불필요):
  무선 구간(왕복) = load.service_ms − Envoy 필드16
  코어(Envoy) 처리 = 필드16 − 필드18
  백홀 + 서버 처리 = 필드18
    · d_net 기준  : backhaul = {2,15,25}(sorts.yaml), server = 필드18 − d_net
    · 실측 기준   : backhaul = 필드17 사이트 중앙값, server = 필드18 − 그 값
  두 기준의 합(백홀+서버)은 같으므로 total/residual 은 하나뿐이다.
"""
import argparse
import csv
import gzip
import json
import math
import os
import sys
from collections import defaultdict

EXP = "/home/user/exp"
OUT = os.path.join(EXP, "figures/data/prof")
DATA = os.path.join(EXP, "figures/data")

SITE_OF_IP = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
SITES = ["S1", "S2", "S3"]
SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}          # sorts.yaml
D_NET = {"S1": 2.0, "S2": 15.0, "S3": 25.0}                          # sorts.yaml
RESP_BYTES = {"reserve": 36, "search": 4474, "recommend": 200}       # sorts.yaml
OVERHEAD = 1.10                                                      # sorts.yaml
GUARD = 2.0                        # 마크 전후 배제 폭 (t2_policy_repeat 와 동일)
SMALL_N = 100                      # n 부족 플래그 임계
MSS = 1360                         # ogstun MTU 1400 − TCP/IP 40 (헤더 검산용)
IPTCP = 40

F1_RUNS = os.path.join(EXP, "runs/stage5-20260812")
F1_POLS = [("SORTS", "s5_sorts"), ("bl_lr", "s5_lr"), ("bl_loc_pri", "s5_loc")]
G3_RUNS = [                        # 서버 축 — 짝을 이루는 두 arm (예산 잔여가 갈랐다)
    ("G3_server_timeseries.csv", "T3_fartier_both_server", "far_tier"),
    ("G3b_server_timeseries_strictfar.csv", "T2_strictfar_both_server", "strict_far"),
]
# 서버 축 런의 f_c 예산 = SLO(search) − GB − d_net(S3) − d_acc(밴드).
# c1 에 상시 6000 kbit 가 걸려 있어 c1 쪽이 더 좁다 — **좁은 쪽(=구속하는 쪽)**을 쓴다.
#   c1(6000 k): 45 − 5 − 25 − 6.562 = 8.438 ms      c2(무제한): 15.000 ms
GB_MS = 5.0
STANDING_KBIT = 6000


# ── 공통 ────────────────────────────────────────────────────────────────────
def pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[int(round(q * (len(xs) - 1)))]


def r2(x, nd=2):
    return None if x is None else round(x, nd)


def is_valid(r):
    """대장(t2_policy_repeat.is_valid)과 동일한 유효 요청 판정."""
    if r["status"] != "200":
        return False
    e = RESP_BYTES.get(r["ep"])
    b = int(r["bytes_recv"])
    return e is not None and abs(b - e) <= (e * 0.10 if e > 1000 else 0)


def read_envoy(rd):
    """request_id -> (envoy_total_ms, cx_ms, upstream_ms, site)."""
    env = {}
    with gzip.open(os.path.join(rd, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 18:
                continue
            try:
                env[p[1]] = (int(p[15]) / 1000.0, int(p[16]) / 1000.0,
                             int(p[17]) / 1000.0,
                             SITE_OF_IP.get(p[10].split(":")[0], "?"))
            except ValueError:
                continue
    return env


def windows_12(meta, names):
    """마크 이름 순서 -> .12 시계 기준 (구간명, lo, hi) 목록. GUARD 배제 포함.

    t2_policy_repeat.windows 와 같은 규약:
      발효 시각 = t43_done − d43 + d12,  발행 시각 = t_issue + d12.
    """
    d12, d43 = meta["clock"]["d12_s"], meta["clock"]["d43_s"]
    mk = {x["what"]: x for x in meta["marks"]}
    eff = lambda w: mk[w]["t43_done"] - d43 + d12
    iss = lambda w: mk[w]["t_issue"] + d12
    t0, dur = meta["t_meas"], meta["duration"]
    out = [("pre", t0, iss(names[0]) - GUARD)]
    for i, w in enumerate(names):
        nxt = names[i + 1] if i + 1 < len(names) else None
        lo = eff(w) + GUARD
        hi = (iss(nxt) - GUARD) if nxt else (t0 + dur)
        out.append((w, lo, hi))
    return out


def phase_fn(meta, names, labels):
    """절대시각(.12) -> 구간 라벨. GUARD 안이면 'transition'."""
    wins = windows_12(meta, names)
    lab = dict(zip([w[0] for w in wins], labels))

    def f(t):
        for name, lo, hi in wins:
            if lo <= t <= hi:
                return lab[name]
        return "transition"
    return f


def join_run(rd, meta, env, valid_only=True):
    """(t_abs, cohort, ep, service, corrected, envoy_total, cx, up, site) 목록."""
    rows, seen, excl = [], 0, 0
    for c in (1, 2):
        p = os.path.join(rd, f"load_c{c}.csv")
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(open(p)):
            if r["warmup"] != "0":
                continue
            seen += 1
            if valid_only and not is_valid(r):
                excl += 1
                continue
            e = env.get(r["request_id"])
            if e is None:
                excl += 1
                continue
            rows.append((float(r["end_ts"]), c, r["ep"], float(r["service_ms"]),
                         float(r["corrected_ms"]), e[0], e[1], e[2], e[3]))
    rows.sort()
    return rows, seen, excl


def backhaul_meas(rows):
    """사이트별 필드17(커넥션 수립) 중앙값 = 페이로드-무관 백홀 실측 상수."""
    by = defaultdict(list)
    for t, c, ep, svc, cor, envt, cx, up, site in rows:
        by[site].append(cx)
    return {s: round(pctl(v, 0.5), 3) for s, v in by.items()}


def d_acc(ep, rate_kbit):
    """결정식이 쓰는 접속 지연 추정 (sorts_ctl: B×8/rate×overhead)."""
    if not rate_kbit:
        return 0.0
    return RESP_BYTES[ep] * 8.0 / rate_kbit * OVERHEAD


def write_csv(name, fields, rows):
    path = os.path.join(OUT, name)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  -> {name}  ({len(rows)} rows)")
    return len(rows)


# ── G1 / G1b : 지연 요소 분해 ───────────────────────────────────────────────
def load_f1_series():
    """L450 3정책 런을 조인해 요청 단위 분해 레코드로 편다."""
    series = []
    for pol, pfx in F1_POLS:
        rd = os.path.join(F1_RUNS, f"{pfx}_L450_1")
        meta = json.load(open(os.path.join(rd, "meta.json")))
        env = read_envoy(rd)
        rows, seen, excl = join_run(rd, meta, env)
        ph = phase_fn(meta, ["c1_extreme", "c2_extreme", "clear_all"],
                      ["pre", "c1only", "both", "post"])
        bh_meas = backhaul_meas(rows)
        series.append(dict(policy=pol, rd=rd, meta=meta, rows=rows, phase=ph,
                           bh_meas=bh_meas, seen=seen, excl=excl))
    return series


def g1(series, q, fname):
    cells = defaultdict(list)
    for s in series:
        for t, c, ep, svc, cor, envt, cx, up, site in s["rows"]:
            p = s["phase"](t)
            if p == "transition":
                continue
            deg = (p == "both") or (p == "c1only" and c == 1)
            band = "degraded" if deg else "normal"
            cells[(s["policy"], ep, band, site)].append(
                (svc, svc - envt, envt - up, up, s["bh_meas"].get(site)))
    rows = []
    for (pol, ep, band, site) in sorted(cells):
        v = cells[(pol, ep, band, site)]
        n = len(v)
        radio = pctl([x[1] for x in v], q)
        core = pctl([x[2] for x in v], q)
        upq = pctl([x[3] for x in v], q)
        bh_m = v[0][4]
        srv_dnet = upq - D_NET[site]
        srv_meas = upq - bh_m
        total = radio + core + upq
        meas = pctl([x[0] for x in v], q)
        rows.append(dict(
            policy=pol, **{"class": ep}, band=band, site=site, n=n,
            n_small=1 if n < SMALL_N else 0,
            radio_ms=r2(radio), radio_ms_model=r2(d_acc(ep, 1600 if band == "degraded" else None)),
            core_ms=r2(core, 3),
            backhaul_ms=r2(D_NET[site]), backhaul_ms_meas=r2(bh_m, 3),
            server_ms=r2(srv_dnet), server_ms_meas=r2(srv_meas),
            total_ms=r2(total), total_measured_ms=r2(meas),
            residual_ms=r2(total - meas, 3), slo_ms=SLO[ep]))
    return write_csv(fname, list(rows[0]), rows), rows


def g1b(series):
    """클래스별: 밴드 인가로 늘어난 무선 구간 실측 증분 vs 모델 d_acc.

    증분 = p50(degraded) − p50(normal), 3정책·전 사이트 풀링(무선 구간은
    사이트 무관 — G1 에서 확인). 헤더 검산 H 는 단일 패킷 클래스
    (reserve/recommend) 의 함의 바이트로 적합한 뒤 search 에 외삽한다.
    """
    pool = defaultdict(lambda: defaultdict(list))
    for s in series:
        for t, c, ep, svc, cor, envt, cx, up, site in s["rows"]:
            p = s["phase"](t)
            if p == "transition":
                continue
            deg = (p == "both") or (p == "c1only" and c == 1)
            pool[ep]["degraded" if deg else "normal"].append(svc - envt)
    RATE = 1600.0
    implied = {}
    for ep in RESP_BYTES:
        d = pctl(pool[ep]["degraded"], 0.5) - pctl(pool[ep]["normal"], 0.5)
        implied[ep] = d * RATE / 8.0                       # netem 이 센 바이트
    # 단일 패킷 클래스로 프레임 오버헤드(HTTP 응답 헤더 + IP/TCP 40 B) 적합
    frame = sum(implied[ep] - RESP_BYTES[ep] for ep in ("reserve", "recommend")) / 2.0
    h_http = frame - IPTCP
    rows = []
    for ep in ("search", "reserve", "recommend"):
        body = RESP_BYTES[ep]
        payload = body + h_http
        npkt = max(1, math.ceil(payload / MSS))
        total_est = payload + IPTCP * npkt
        meas = pctl(pool[ep]["degraded"], 0.5) - pctl(pool[ep]["normal"], 0.5)
        m_body = body * 8.0 / RATE * OVERHEAD
        m_hdr = total_est * 8.0 / RATE
        rows.append(dict(**{"class": ep}, bytes_body=body,
                         bytes_total_est=round(total_est, 1),
                         h_http_fit_b=round(h_http, 1), pkts_est=npkt,
                         d_acc_model_ms=r2(m_body),
                         radio_delta_meas_ms=r2(meas),
                         err_pct=r2((m_body - meas) / meas * 100, 1),
                         d_acc_model_hdr_ms=r2(m_hdr),
                         err_pct_hdr=r2((m_hdr - meas) / meas * 100, 1),
                         n=len(pool[ep]["degraded"]) + len(pool[ep]["normal"])))
    return write_csv("G1b_model_vs_measured.csv", list(rows[0]), rows), rows


# ── G2 / G3 : 1 s 시계열 ────────────────────────────────────────────────────
def bucket_series(rows, ph, t0, dur, bh_meas):
    """초 버킷 -> {phase, lat[class] p50, share[site](search), fc[site] search p95}.

    `fc` 는 **search 클래스만** 모은다 — 시스템의 f_c 정의(sorts.yaml: "f_c 는
    service p95")와 TASKA_REPORT §의 "S3 search f_c p95" 인용에 맞춘다.
    """
    lat = defaultdict(lambda: defaultdict(list))
    fc = defaultdict(lambda: defaultdict(list))
    share = defaultdict(lambda: defaultdict(int))
    phase = {}
    for t, c, ep, svc, cor, envt, cx, up, site in rows:
        k = int(t - t0)
        if k < 0 or k >= int(dur):
            continue
        phase.setdefault(k, ph(t))
        lat[k][ep].append(cor)
        if ep == "search":
            fc[k][site].append(up - bh_meas.get(site, 0.0))
            share[k][site] += 1
    return lat, fc, share, phase


def g2():
    rd = os.path.join(F1_RUNS, "s5_sorts_L450_1")
    meta = json.load(open(os.path.join(rd, "meta.json")))
    env = read_envoy(rd)
    rows, seen, excl = join_run(rd, meta, env)
    ph = phase_fn(meta, ["c1_extreme", "c2_extreme", "clear_all"],
                  ["pre", "c1only", "both", "post"])
    t0, dur = meta["t_meas"], meta["duration"]
    d12, d43 = meta["clock"]["d12_s"], meta["clock"]["d43_s"]
    mk = {x["what"]: x["t43_done"] - d43 + d12 - t0 for x in meta["marks"]}
    lat, _, share, phase = bucket_series(rows, ph, t0, dur, {})
    out = []
    for k in range(int(dur)):
        b1 = 1600 if mk["c1_extreme"] <= k < mk["clear_all"] else ""
        b2 = 1600 if mk["c2_extreme"] <= k < mk["clear_all"] else ""
        tot = sum(share[k].values())
        row = {"t_sec": k, "band_kbit_c1": b1, "band_kbit_c2": b2}
        for cls in ("search", "reserve", "recommend"):
            row[f"lat_{cls}_ms"] = r2(pctl(lat[k][cls], 0.5))
        for cls in ("search", "reserve", "recommend"):
            row[f"slo_{cls}"] = SLO[cls]
        for s in SITES:
            row[f"share_{s.lower()}_pct"] = r2(100.0 * share[k][s] / tot, 2) if tot else None
        row["n_search"] = tot
        row["phase"] = phase.get(k, "")
        out.append(row)
    return write_csv("G2_radio_timeseries.csv", list(out[0]), out), meta, rows, excl, seen


def g3(fname, run_name, arm):
    rd = os.path.join(EXP, "runs/taskA-20260809", run_name)
    meta = json.load(open(os.path.join(rd, "meta.json")))
    env = read_envoy(rd)
    rows, seen, excl = join_run(rd, meta, env)
    ph = phase_fn(meta, ["stress_on", "stress_off"], ["pre", "stress", "post"])
    t0, dur = meta["t_meas"], meta["duration"]
    bh = backhaul_meas(rows)
    lat, fc, share, phase = bucket_series(rows, ph, t0, dur, bh)
    # 구속하는 코호트(c1, 상시 밴드) 기준 f_c 예산
    fc_budget = SLO["search"] - GB_MS - D_NET["S3"] - d_acc("search", STANDING_KBIT)
    out = []
    for k in range(int(dur)):
        tot = sum(share[k].values())
        row = {"t_sec": k}
        for s in SITES:
            v = pctl(fc[k][s], 0.95)          # f_c 정의 = p95
            row[f"fc_{s.lower()}_ms"] = r2(v)
            row[f"fc_{s.lower()}_ms_dnet"] = r2(v + bh.get(s, 0.0) - D_NET[s]) if v is not None else None
        # 예산은 결정식과 같은 기준(d_net)으로 뺀다
        v3 = row["fc_s3_ms_dnet"]
        row["fc_budget_s3_ms"] = r2(fc_budget)
        row["budget_s3_ms"] = r2(fc_budget - v3) if v3 is not None else None
        for cls in ("search", "reserve", "recommend"):
            row[f"lat_{cls}_ms"] = r2(pctl(lat[k][cls], 0.5))
        for cls in ("search", "reserve", "recommend"):
            row[f"slo_{cls}"] = SLO[cls]
        for s in SITES:
            row[f"share_{s.lower()}_pct"] = r2(100.0 * share[k][s] / tot, 2) if tot else None
        row["n_search"] = tot
        row["phase"] = phase.get(k, "")
        out.append(row)
    return write_csv(fname, list(out[0]), out), meta, bh, excl, seen


# ── G4 / G5 ─────────────────────────────────────────────────────────────────
def g4(series):
    """정책별 사이트 몫 (전 클래스) — f1_L450_sites.csv 재사용 + phase 부여."""
    rel = {}
    for s in series:
        m = s["meta"]
        t0 = m["t_meas"]
        rel[s["policy"]] = [(nm, lo - t0, hi - t0)
                            for nm, lo, hi in windows_12(m, ["c1_extreme", "c2_extreme", "clear_all"])]
    lab = {"pre": "pre", "c1_extreme": "c1only", "c2_extreme": "both", "clear_all": "post"}
    src = os.path.join(DATA, "f1_L450_sites.csv")
    out = []
    for r in csv.DictReader(open(src)):
        t = float(r["t_rel_s"])
        p = "transition"
        for nm, lo, hi in rel.get(r["policy"], []):
            if lo <= t <= hi:
                p = lab[nm]
                break
        out.append({"t_sec": int(t), "policy": r["policy"],
                    "share_s1_pct": float(r["S1_pct"]), "share_s2_pct": float(r["S2_pct"]),
                    "share_s3_pct": float(r["S3_pct"]), "n": int(r["S1"]) + int(r["S2"]) + int(r["S3"]),
                    "phase": p})
    return write_csv("G4_policy_share.csv", list(out[0]), out)


def g4b(series):
    """both 창 위반율 — 대장과 같은 산식(t2_policy_repeat.one_run) 재사용."""
    sys.path.insert(0, os.path.join(EXP, "analysis/night-20260810"))
    import t2_policy_repeat as t2
    out = []
    for s in series:
        res = t2.one_run(s["rd"])
        for wname in ("c1only", "both"):
            w = res["windows"][wname]
            row = {"policy": s["policy"], "window": wname, "n": w["n"],
                   "viol_pct": round(w["viol_pct"], 3)}
            for cls in ("search", "reserve", "recommend"):
                tot = vio = 0
                for k, d in w["by_cohort_class"].items():
                    if k.endswith("_" + cls):
                        tot += d["n"]
                        vio += d["n"] * d["viol_pct"] / 100.0
                row[f"viol_pct_{cls}"] = round(100.0 * vio / tot, 3) if tot else None
            out.append(row)
    return write_csv("G4b_policy_violation.csv", list(out[0]), out), out


def g5():
    src = os.path.join(DATA, "f3_cumulative.csv")
    out = []
    for r in csv.DictReader(open(src)):
        vs = [float(x) for x in r["values"].split(";")]
        n = len(vs)
        sd = (sum((v - sum(vs) / n) ** 2 for v in vs) / (n - 1)) ** 0.5 if n > 1 else None
        note = r["layers"]
        if n == 1:
            note += "  ★n=1 — 표준편차 없음(단일 런)"
        out.append({"layer": r["label"], "violation_pct": round(float(r["viol_pct"]), 3),
                    "stdev": round(sd, 3) if sd is not None else "", "n": n,
                    "runs": r["runs"], "values": r["values"], "note": note})
    return write_csv("G5_layer_cumulative.csv", list(out[0]), out)


def main():
    ap = argparse.ArgumentParser()
    ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    rep = {}
    print("G1/G1b — L450 3정책 조인")
    series = load_f1_series()
    for s in series:
        print(f"  {s['policy']:<11} 유효 {len(s['rows'])} / 본측정 {s['seen']} "
              f"(제외 {s['excl']}, {100*s['excl']/s['seen']:.3f}%)  백홀 실측 {s['bh_meas']}")
        rep[f"join_{s['policy']}"] = (len(s["rows"]), s["seen"], s["excl"], s["bh_meas"])
    rep["G1"], g1rows = g1(series, 0.5, "G1_delay_breakdown.csv")
    rep["G1p95"], _ = g1(series, 0.95, "G1_delay_breakdown_p95.csv")
    rep["G1b"], g1brows = g1b(series)
    print("G2 — 무선 축 시계열")
    rep["G2"], m2, _, e2, s2 = g2()
    print("G3/G3b — 서버 축 시계열 (짝)")
    for fname, run_name, arm in G3_RUNS:
        key = fname.split("_")[0]
        rep[key], m3, bh3, e3, s3 = g3(fname, run_name, arm)
        print(f"  {run_name:<26} arm={arm:<10} 백홀 실측 {bh3}  "
              f"유효 {s3-e3}/{s3} (제외 {e3}, {100*e3/s3:.3f}%)")
    print("G4/G4b/G5")
    rep["G4"] = g4(series)
    rep["G4b"], g4brows = g4b(series)
    rep["G5"] = g5()
    json.dump({k: v for k, v in rep.items()},
              open(os.path.join(OUT, "_extract_report.json"), "w"),
              ensure_ascii=False, indent=1, default=str)
    print("\n행 수:", {k: v for k, v in rep.items() if isinstance(v, int)})
    return 0


if __name__ == "__main__":
    sys.exit(main())

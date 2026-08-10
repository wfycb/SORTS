#!/usr/bin/env python3
"""Phase 4 분석 (지시 §4~§6).

  --gate  : 재현성 게이트만 판정 (R1/R2 vs demo-20260805, ±0.40%p / 조인율 /
            바이트 이탈). 미달이면 exit 4 (정지 조건 4).
  기본    : 축별 §6 표 + shadow 4방향 귀속표 + 감지지연(§4) + I-6 계측(§5)
            + 커버리지/추정 bias. 산출: 표는 stdout(markdown), 원자료는
            p4_tables.json.

위반율은 전부 summary.json 의 corrected 기준 slo_violation_rate (demo 와
동일 관례). 시계: obs_state/decisions ts 는 .43, sections 는 .12 —
meta.clock 의 d12/d43 으로 옮겨 조인한다 (t43 = t12 - d12 + d43).
"""
from __future__ import annotations

import argparse
import csv
import glob
import gzip
import json
import os
import sys

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "..", "runs", "phase4-20260807")
DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "..", "runs", "demo-20260805")
SITES = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
D_NET_MS = {"S1": 2.0, "S2": 15.0, "S3": 25.006}   # 사후분석 관례 (s6_calib)
GATE_TOL_PP = 0.40          # §2.1: CV 0.32~0.40% 안 -> ±0.40%p
GATE_TARGETS = {"R1_rr_radio": ("D4_rr_radio", 33.4595),
                "R2_lr_radio": ("D5_lr_radio", 22.7712)}
OSC_THRESHOLD = 6           # during 120s 에 유닛당 전환 6회+ = 진동 플래그
                            # (demo 상수판은 런 전체 4회)


def jload(*p):
    path = os.path.join(*p)
    return json.load(open(path)) if os.path.exists(path) else None


def read_csv(*p):
    path = os.path.join(*p)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def c1_search_viol(summary, sec="during"):
    try:
        return 100.0 * summary["sections"][sec]["by_cohort"]["1"][
            "by_endpoint"]["search"]["slo_violation_rate"]
    except (KeyError, TypeError):
        return None


def run_paths():
    out = []
    for d in sorted(glob.glob(os.path.join(RUNS_DIR, "*"))):
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "meta.json")):
            out.append(d)
    return out


def load_run(d):
    rid = os.path.basename(d)
    meta = jload(d, "meta.json")
    summ = jload(d, "summary.json")
    return {
        "rid": rid, "dir": d, "meta": meta, "summary": summ,
        "suspect": jload(d, "SUSPECT"),
        "skipped": jload(d, "SKIPPED"),
        "arm": (meta or {}).get("arm"),
        "band": (meta or {}).get("standing_band_kbit"),
        "decisions": read_csv(d, "decisions.csv"),
        "obs_state": read_csv(d, "obs_state.csv"),
        "marks": (meta or {}).get("marks", []),
        "clock": (meta or {}).get("clock", {}),
    }


def t43_of_12(r, t12):
    return t12 - r["clock"].get("d12_s", 0.0) + r["clock"].get("d43_s", 0.0)


def sec43(r, name):
    lo, hi = r["meta"]["sections_abs_12"][name]
    return t43_of_12(r, lo), t43_of_12(r, hi)


def stress_on_t43(r):
    for m in r["marks"]:
        if m.get("what") == "stress_on":
            return m.get("t43_done", m["t_done"] + r["clock"].get("d43_s", 0.0))
    return None


# ---------------------------------------------------------------- 감지지연 §4
def detection_delay(r):
    """§4 3시점: 주입(t_issue 기준) → 임계 초과 → 라우팅 변경 [s].

    기준은 stress_on 의 **지시 시각**(t_issue)이다 — stress 는 ssh 왕복
    (~2s) 동안 이미 램프되므로 t_done 기준으로 재면 감지가 마크보다 빨라
    음수가 되고, t_done 이후만 스캔하면 첫 사이클을 놓치고 진동의 두 번째
    사이클을 잡는다 (S2 런에서 실측한 함정).
    """
    m_on = next((m for m in r["marks"] if m.get("what") == "stress_on"), None)
    if m_on is None:
        return None
    t_done = m_on.get("t43_done", m_on["t_done"] + r["clock"].get("d43_s", 0))
    t_issue = t_done - m_on.get("apply_lat_s", 0.0)
    cross = flip = None
    for row in r["decisions"]:
        if row["cohort"] != "c1" or row["class"] != "search":
            continue
        ts = float(row["ts"])
        if ts < t_issue:
            continue
        if cross is None and float(row["slack_s3"]) < 0:
            cross = round(ts - t_issue, 2)
        if flip is None and row["changed"] == "1" and row["chosen_site"] != "S3":
            flip = round(ts - t_issue, 2)
        if cross is not None and flip is not None:
            break
    return {"threshold_cross_s": cross, "route_change_s": flip,
            "stress_apply_lat_s": round(m_on.get("apply_lat_s", 0.0), 2)}


# ------------------------------------------------------------ shadow 4방향 §6
def shadow_attribution(r):
    per_unit_flip = {}
    patterns = {}
    n = 0
    for row in r["decisions"]:
        n += 1
        key = (row["chosen_site_const"], row["chosen_site_bytesonly"],
               row["chosen_site_fconly"], row["chosen_site"])
        patterns[key] = patterns.get(key, 0) + 1
        u = (row["cohort"], row["class"])
        d = per_unit_flip.setdefault(u, {"n": 0, "diff_const": 0})
        d["n"] += 1
        if row["chosen_site_const"] != row["chosen_site"]:
            d["diff_const"] += 1
    flip_rate = (100.0 * sum(d["diff_const"] for d in per_unit_flip.values())
                 / n) if n else None
    return {"n_ticks": n, "flip_rate_const_vs_live_pct": flip_rate,
            "patterns": {"/".join(k): v for k, v in
                         sorted(patterns.items(), key=lambda kv: -kv[1])},
            "per_unit": {f"{c}:{k}": v for (c, k), v in per_unit_flip.items()}}


# ------------------------------------------------------------------- I-6 §5
def i6_metrics(r):
    cells = {}
    for row in r["obs_state"]:
        key = (row["site"], row["class"])
        c = cells.setdefault(key, {"prev": None, "trans": 0,
                                   "obs2prior": [], "src_n": {}})
        src = row["src"]
        c["src_n"][src] = c["src_n"].get(src, 0) + 1
        if c["prev"] is not None and src != c["prev"]:
            c["trans"] += 1
            if c["prev"] == "obs" and src.startswith("prior"):
                lo = row["last_obs_ms"]
                pr = row["prior_ms"]
                if lo and pr:
                    c["obs2prior"].append(round(float(lo) - float(pr), 3))
        c["prev"] = src
    switch = {}
    for row in r["decisions"]:
        if row["changed"] == "1":
            u = f"{row['cohort']}:{row['class']}"
            switch[u] = switch.get(u, 0) + 1
    out = {}
    for (site, klass), c in sorted(cells.items()):
        n = sum(c["src_n"].values())
        out[f"{site}/{klass}"] = {
            "coverage_obs_pct": round(100.0 * c["src_n"].get("obs", 0) / n, 2) if n else None,
            "src_transitions": c["trans"],
            "obs2prior_gaps_ms": c["obs2prior"][:20],
            "src_n": c["src_n"]}
    osc = {u: k for u, k in switch.items() if k >= OSC_THRESHOLD}
    return {"cells": out, "unit_switch_counts": switch, "oscillating": osc}


# ------------------------------------------------------- 진동 계측 (§4, I-9)
def osc_metrics(r):
    """유닛별 전환 시각/주기/듀티 + S3 f_c 한계 사이클 상하한 (during 창).

    진동의 근인은 I-9(부하 되먹임)다. f_c_src 가 obs 를 유지하는지(I-6 배제
    근거)도 셀별로 같이 낸다. 창은 교란 마크(start~end, .43 시계) 기준.
    """
    t_on = stress_on_t43(r)
    if t_on is None:
        # radio 런은 radio_on 마크 기준
        for m in r["marks"]:
            if m.get("phase") == "start":
                t_on = m.get("t43_done", m["t_done"] + r["clock"].get("d43_s", 0))
                break
    t_off = None
    for m in reversed(r["marks"]):
        if m.get("phase") == "end":
            t_off = m.get("t43_done", m["t_done"] + r["clock"].get("d43_s", 0))
            break
    if t_on is None or t_off is None:
        return None
    units = {}
    for row in r["decisions"]:
        ts = float(row["ts"])
        if not (t_on <= ts < t_off):
            continue
        u = f"{row['cohort']}:{row['class']}"
        d = units.setdefault(u, {"switch_ts": [], "n_ticks": 0, "s3_ticks": 0})
        d["n_ticks"] += 1
        if row["chosen_site"] == "S3":
            d["s3_ticks"] += 1
        if row["changed"] == "1":
            d["switch_ts"].append(ts)
    fc_s3 = {}
    for row in r["obs_state"]:
        ts = float(row["ts"])
        if row["site"] == "S3" and row["src"] == "obs" and row["value_ms"] \
                and t_on <= ts < t_off:
            fc_s3.setdefault(row["class"], []).append(float(row["value_ms"]))
    out = {}
    for u, d in sorted(units.items()):
        ts = d["switch_ts"]
        periods = [round(b - a, 2) for a, b in zip(ts, ts[1:])]
        klass = u.split(":")[1]
        xs = sorted(fc_s3.get(klass, []))
        out[u] = {
            "n_switches": len(ts),
            "period_s": {"min": min(periods), "med": periods[len(periods) // 2],
                         "max": max(periods)} if periods else None,
            "duty_S3_pct": round(100.0 * d["s3_ticks"] / d["n_ticks"], 1)
            if d["n_ticks"] else None,
            "fc_S3_obs_ms": {"min": round(xs[0], 2),
                             "p25": round(xs[len(xs) // 4], 2),
                             "p75": round(xs[3 * len(xs) // 4], 2),
                             "max": round(xs[-1], 2)} if xs else None,
        }
    return out


# ------------------------------------------------------- 추정 bias (사후 조인)
def posthoc_bias(r):
    """섹션별 (site,class) 사후 f_c p95 vs obs_state(src=obs) value_ms 평균."""
    d = r["dir"]
    sl = os.path.join(d, "envoy_access.log.gz")
    if not (os.path.exists(sl) and r["obs_state"]):
        return None
    ep_of = {}
    for c in (1, 2):
        for row in read_csv(d, f"load_c{c}.csv"):
            if row["warmup"] == "0" and row["status"] == "200":
                ep_of[row["request_id"]] = row["ep"]
    samples = {}      # (sec, site, class) -> [fc]
    secs43 = {name: sec43(r, name) for name in ("pre", "during", "post")}
    with gzip.open(sl, "rt", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 18 or not p[17].strip().isdigit():
                continue
            ep = ep_of.get(p[1])
            site = SITES.get(p[10].split(":")[0])
            if not ep or not site:
                continue
            try:
                ts = float(p[0])
            except ValueError:
                continue
            for name, (lo, hi) in secs43.items():
                if lo <= ts < hi:
                    samples.setdefault((name, site, ep), []).append(
                        int(p[17]) / 1000.0 - D_NET_MS[site])
                    break
    obs_avg = {}      # (sec, site, class) -> [value_ms...]
    for row in r["obs_state"]:
        if row["src"] != "obs" or not row["value_ms"]:
            continue
        ts = float(row["ts"])
        for name, (lo, hi) in secs43.items():
            if lo <= ts < hi:
                obs_avg.setdefault((name, row["site"], row["class"]),
                                   []).append(float(row["value_ms"]))
                break
    out = {}
    for key, xs in sorted(samples.items()):
        if len(xs) < 20:
            continue
        xs = sorted(xs)
        p95 = xs[max(0, min(len(xs) - 1, int(round(.95 * (len(xs) - 1)))))]
        ov = obs_avg.get(key)
        if not ov:
            continue
        mean_obs = sum(ov) / len(ov)
        out["/".join(key)] = {"posthoc_p95": round(p95, 3),
                              "obs_mean_p95": round(mean_obs, 3),
                              "bias_ms": round(mean_obs - p95, 3),
                              "n_obs_win": len(ov)}
    return out


# ------------------------------------------------------------------ §6 표
def table_row(r):
    s = r["summary"]
    du = s["sections"]["during"] if s else {}
    share = du.get("site_share") or {}
    dd = detection_delay(r)
    sh = shadow_attribution(r) if r["decisions"] else None
    i6 = i6_metrics(r) if r["obs_state"] else None
    first_change = None
    for row in r["decisions"]:
        if row["changed"] == "1":
            t0 = t43_of_12(r, r["meta"]["t_meas"])
            first_change = round(float(row["ts"]) - t0, 1)
            break
    cov = None
    if i6:
        c = i6["cells"].get("S3/search")
        cov = c["coverage_obs_pct"] if c else None
    return {
        "run": r["rid"],
        "arm": (f"bytes={r['arm']['effective']['est_resp_bytes']}"
                f",fc={r['arm']['effective']['est_f_c']}"
                f",W={r['arm']['effective']['window_s']}") if r["arm"] else r["meta"]["policy"],
        "suspect": bool(r["suspect"]),
        "c1_search_viol_during_pct": round(c1_search_viol(s), 4) if s else None,
        "first_change_rel_s": first_change,
        "n_changes": sum(i6["unit_switch_counts"].values()) if i6 else None,
        "detect_cross_s": dd and dd["threshold_cross_s"],
        "detect_flip_s": dd and dd["route_change_s"],
        "shadow_flip_const_vs_live_pct": sh and (
            round(sh["flip_rate_const_vs_live_pct"], 3)
            if sh["flip_rate_const_vs_live_pct"] is not None else None),
        "fc_coverage_S3_search_pct": cov,
        "edge_S1_rps": round((share.get("S1") or 0.0)
                             * (du.get("achieved_rps") or 0.0), 1) if s else None,
        "join_rate": s and s.get("join_rate"),
        "byte_dev": s and du.get("byte_deviation_rate"),
        "oscillating_units": (sorted(i6["oscillating"]) if i6 else []),
    }


def gate():
    bad = []
    for rid, (demo_rid, target) in GATE_TARGETS.items():
        s = jload(RUNS_DIR, rid, "summary.json")
        if not s:
            bad.append(f"{rid}: summary 없음")
            continue
        v = c1_search_viol(s)
        dv = abs(v - target)
        ok = dv <= GATE_TOL_PP and s.get("join_rate") == 1.0 \
            and (s["sections"]["during"].get("byte_deviation_rate") or 0) == 0
        print(f"{rid}: viol={v:.4f}%  demo({demo_rid})={target:.4f}%  "
              f"|Δ|={dv:.4f}%p (허용 {GATE_TOL_PP})  "
              f"join={s.get('join_rate')}  "
              f"bytes_dev={s['sections']['during'].get('byte_deviation_rate')}"
              f"  -> {'통과' if ok else '미달'}")
        if not ok:
            bad.append(rid)
    if bad:
        print(f"\n재현성 게이트 미달: {bad} — 정지 조건 4. 본측정 진행 금지.")
        return 4
    print("\n재현성 게이트 통과 — 테스트베드는 IP 복구 전후 동일하다고 본다.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", action="store_true")
    a = ap.parse_args()
    if a.gate:
        return gate()

    runs = [load_run(d) for d in run_paths()]
    tables = {"rows": [], "shadow": {}, "i6": {}, "bias": {}, "delay": {}}
    for r in runs:
        if r["skipped"]:
            tables["rows"].append({"run": r["rid"], "arm": "-", "SKIPPED": True})
            continue
        row = table_row(r)
        tables["rows"].append(row)
        if r["decisions"]:
            tables["shadow"][r["rid"]] = shadow_attribution(r)
        if r["obs_state"]:
            tables["i6"][r["rid"]] = i6_metrics(r)
            b = posthoc_bias(r)
            if b:
                tables["bias"][r["rid"]] = b
        if r["decisions"]:
            om = osc_metrics(r)
            if om:
                tables.setdefault("osc", {})[r["rid"]] = om
        dd = detection_delay(r)
        if dd:
            tables["delay"][r["rid"]] = dd

    cols = ["run", "arm", "c1_search_viol_during_pct", "first_change_rel_s",
            "n_changes", "detect_cross_s", "detect_flip_s",
            "shadow_flip_const_vs_live_pct", "fc_coverage_S3_search_pct",
            "edge_S1_rps", "join_rate", "byte_dev", "suspect",
            "oscillating_units"]
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for row in tables["rows"]:
        print("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "p4_tables.json")
    json.dump(tables, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n원자료 -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

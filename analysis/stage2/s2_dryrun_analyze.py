#!/usr/bin/env python3
"""STAGE2 S2-0-3 드라이런 분석 — 주기 파라미터 실효 확인.

런별(decisions.csv):
  - 실측 루프 주기: tick(고유 ts) 간격의 히스토그램/분위수 [ms]
  - 오버런 비율: 간격 > T×(1+톨러런스) 인 반복의 몫 (톨러런스 10 % 사전 고정
    — sleep 양자화·로그 flush 지터를 오버런으로 세지 않기 위한 최소 여유)
  - apply 호출 수: changed==1 행 수 (tick 당 duty 도 병기)
컨트롤러 CPU(s2dry_cpu.log, .43 1 s 샘플): 런 창과 조인해 코어 점유율 %
  (Δ(utime+stime)/HZ / Δt × 100; sorts_ctl 은 단일 스레드 — 100 % = 포화).

플래그 규칙(사전 등록, 지시 S2-0-3): 오버런 > 1 % 또는 CPU 포화 시 해당 arm
플래그 — 제외 여부는 G1 에서 판단.
"""
import csv
import json
import os
import sys
from collections import Counter

HZ = 100          # .43 CONFIG_HZ (getconf CLK_TCK 확인값)
TOL = 0.10


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def analyze_run(rd):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    period = float(meta.get("arm", {}).get("effective", {}).get("ctl_period_s")
                   or meta.get("ctl_period_s") or 1.0)
    ts, changed, lat_ms = [], 0, []
    seen = set()
    n_rows = 0
    for r in csv.DictReader(open(os.path.join(rd, "decisions.csv"))):
        n_rows += 1
        t = float(r["ts"])
        if t not in seen:
            seen.add(t)
            ts.append(t)
        if r["changed"] == "1":
            changed += 1
            if r.get("apply_latency_ms"):
                lat_ms.append(float(r["apply_latency_ms"]))
    ts.sort()
    gaps = [(b - a) * 1000.0 for a, b in zip(ts, ts[1:])]
    thresh = period * 1000.0 * (1 + TOL)
    over = [g for g in gaps if g > thresh]
    hist = Counter()
    for g in gaps:
        # 히스토그램 버킷: T 의 0.5 배 간격
        hist[round(g / (period * 500.0)) * (period * 500.0) / 1000.0] += 1
    return {
        "run_id": meta["run_id"], "T_s": period,
        "n_ticks": len(ts), "span_s": round(ts[-1] - ts[0], 1) if ts else 0,
        "gap_ms": {"p50": round(pct(gaps, 0.5), 2), "p95": round(pct(gaps, 0.95), 2),
                   "p99": round(pct(gaps, 0.99), 2), "max": round(max(gaps), 2)}
        if gaps else None,
        "overrun_pct": round(100.0 * len(over) / len(gaps), 3) if gaps else None,
        "overrun_thresh_ms": round(thresh, 1),
        "apply_calls": changed,
        "apply_per_tick": round(changed / len(ts), 4) if ts else None,
        "hist_bucket_s": {f"{k:.3f}": v for k, v in sorted(hist.items())},
        "t0": ts[0] if ts else None, "t1": ts[-1] if ts else None,
    }


def cpu_join(cpulog, runs):
    """샘플 (ts pid utime stime) 를 런 tick 창과 조인해 CPU% 산출."""
    samples = []
    for line in open(cpulog):
        p = line.split()
        if len(p) == 4:
            try:
                samples.append((float(p[0]), int(p[1]), int(p[2]) + int(p[3])))
            except ValueError:
                pass
    for r in runs:
        if not r["t0"]:
            r["cpu_pct"] = None
            continue
        xs = [(t, pid, j) for (t, pid, j) in samples if r["t0"] <= t <= r["t1"]]
        # pid 가 바뀌면 (재기동) 조각별로 잇는다
        tot_j, tot_t = 0, 0.0
        for i in range(1, len(xs)):
            t0, p0, j0 = xs[i - 1]
            t1, p1, j1 = xs[i]
            if p0 == p1 and j1 >= j0:
                tot_j += j1 - j0
                tot_t += t1 - t0
        r["cpu_pct"] = round(100.0 * (tot_j / HZ) / tot_t, 1) if tot_t else None
        r["cpu_samples"] = len(xs)
    return runs


def main():
    outroot = sys.argv[1]
    runs = []
    for rid in sorted(os.listdir(outroot)):
        rd = os.path.join(outroot, rid)
        if os.path.isdir(rd) and os.path.exists(os.path.join(rd, "decisions.csv")):
            runs.append(analyze_run(rd))
    cpulog = os.path.join(outroot, "s2dry_cpu.log")
    if os.path.exists(cpulog):
        cpu_join(cpulog, runs)
    out = os.path.join(outroot, "s2_dryrun_results.json")
    json.dump(runs, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"# S2-0-3 드라이런 결과 ({outroot})\n")
    print("| run | T | ticks | gap p50/p95/p99/max [ms] | 오버런% (>T×1.1) | apply 호출 | apply/tick | CPU% |")
    print("|---|---|---|---|---|---|---|---|")
    for r in runs:
        g = r["gap_ms"] or {}
        flag = ""
        if (r["overrun_pct"] or 0) > 1.0:
            flag = " ★FLAG(오버런>1%)"
        if (r.get("cpu_pct") or 0) > 90:
            flag += " ★FLAG(CPU포화)"
        print(f"| {r['run_id']} | {r['T_s']} | {r['n_ticks']} | "
              f"{g.get('p50')}/{g.get('p95')}/{g.get('p99')}/{g.get('max')} | "
              f"{r['overrun_pct']} | {r['apply_calls']} | {r['apply_per_tick']} | "
              f"{r.get('cpu_pct')}{flag} |")
    print(f"\n원자료: {out}")


if __name__ == "__main__":
    main()

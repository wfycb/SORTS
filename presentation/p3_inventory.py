#!/usr/bin/env python3
"""P3 실험 인벤토리. 읽기 전용 — runs/ 를 훑어 런 수·요청 수·소요를 합산한다."""
import csv
import glob
import json
import os
import time

RUNS = os.path.expanduser("~/exp/runs")
OUT = os.path.expanduser("~/exp/presentation/tables")
os.makedirs(OUT, exist_ok=True)

rows = []
for batch in sorted(os.listdir(RUNS)):
    bdir = os.path.join(RUNS, batch)
    if not os.path.isdir(bdir):
        continue
    runs = sorted(d for d in os.listdir(bdir) if os.path.isdir(os.path.join(bdir, d)))
    n_req = 0
    t_first, t_last = None, None
    pols, dists = set(), set()
    n_done = 0
    for r in runs:
        rd = os.path.join(bdir, r)
        if os.path.exists(os.path.join(rd, "DONE")):
            n_done += 1
        mp = os.path.join(rd, "meta.json")
        if os.path.exists(mp):
            m = json.load(open(mp))
            pols.add(m.get("policy"))
            dists.add(m.get("disturb"))
            ts = m.get("t_start")
            if ts:
                t_first = ts if t_first is None else min(t_first, ts)
                t_last = ts if t_last is None else max(t_last, ts)
        sp = os.path.join(rd, "summary.json")
        if os.path.exists(sp):
            s = json.load(open(sp))
            # 측정구간 요청 수 = 세 구간 n 합 (warmup 제외는 러너가 이미 함)
            n_req += sum(s["sections"][k]["n"] for k in s["sections"])
    dur_s = (t_last - t_first) if (t_first and t_last) else None
    rows.append({
        "batch": batch, "n_runs": len(runs), "n_done": n_done,
        "policies": "/".join(sorted(p for p in pols if p)),
        "disturbs": "/".join(sorted(d for d in dists if d)),
        "first_run_start": time.strftime("%Y-%m-%d %H:%M", time.localtime(t_first)) if t_first else "",
        "measured_requests": n_req,
        "span_min": round(dur_s / 60, 1) if dur_s else "",
        "path": f"runs/{batch}",
    })

with open(f"{OUT}/p3_experiment_inventory.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

tot_runs = sum(r["n_runs"] for r in rows)
tot_req = sum(r["measured_requests"] for r in rows)
print(f"{'배치':26s}{'런':>4s}{'DONE':>6s}{'측정요청':>12s}{'시작':>18s}{'구간(분)':>9s}")
for r in rows:
    print(f"{r['batch']:26s}{r['n_runs']:4d}{r['n_done']:6d}{r['measured_requests']:12,d}"
          f"{r['first_run_start']:>18s}{str(r['span_min']):>9s}")
print(f"{'합계':26s}{tot_runs:4d}{'':6s}{tot_req:12,d}")

# 캘리브레이션 계열 (runs/ 밖) 요청 수
HOME = os.path.expanduser("~")
calib = []
for name, pat in (("M1 f_c (구)", "m1/m1_*.csv"), ("M1 f_c (v2)", "m1_v2/m1_*.csv"),
                  ("M2 무릎 (구)", "n3/n3_*.csv"), ("M2 무릎 (v2)", "n3_v2/n3_*.csv"),
                  ("N2 d_acc", "n2/n2_*[!v].csv"), ("S6 stress", "s6/*/load_c*.csv"),
                  ("T1 계단", "exp/t1_stair/t1_*.csv"), ("M2 혼합(구m2)", "m2/m2_*.csv"),
                  ("M3", "m3/m3_*.csv")):
    n = 0
    files = glob.glob(os.path.join(HOME, pat))
    for p in files:
        if p.endswith("_envoy.csv"):
            continue
        try:
            with open(p) as fh:
                n += max(sum(1 for _ in fh) - 1, 0)
        except Exception:
            pass
    if files:
        calib.append({"group": name, "files": len(files), "rows": n})
with open(f"{OUT}/p3_calibration_inventory.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["group", "files", "rows"])
    w.writeheader()
    w.writerows(calib)
print("\n캘리브레이션 계열 (runs/ 밖, CSV 행 수 = 요청 수):")
for c in calib:
    print(f"  {c['group']:16s} 파일 {c['files']:3d}  요청 {c['rows']:10,d}")
print(f"  {'소계':16s} {'':3s}       요청 {sum(c['rows'] for c in calib):10,d}")
print(f"\n총 요청 (배치 + 캘리브레이션) = {tot_req + sum(c['rows'] for c in calib):,d}")

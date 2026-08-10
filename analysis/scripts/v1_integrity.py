#!/usr/bin/env python3
"""STEP V1 — 데이터 무결성 확인 (지시서 v7 §2)."""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import common as C  # noqa: E402

EXPECT_KNEE = {"site_s3": 0.0, "bl_rr": 0.67, "bl_lr": 0.56, "bl_loc": 1.14}

rows = []
for rid in C.RUN_IDS:
    s = C.load_json(rid, "summary.json")
    mk = C.marks_rel(rid)
    st = next((m for m in mk if m["phase"] == "start"), None)
    en = next((m for m in reversed(mk) if m["phase"] == "end"), None)
    # 교란 구간 길이 = 적용완료(t_done) ~ 해제지시(t_issue)
    dur_len = (en["t_issue"] - st["t_done"]) if (st and en) else None
    sec = s["sections"]
    d = sec["during"]
    rows.append({
        "run_id": rid,
        "policy": s["policy"],
        "disturb": s["disturb"],
        "n_pre": sec["pre"]["n"], "n_during": d["n"], "n_post": sec["post"]["n"],
        "byte_dev_pre": sec["pre"]["byte_deviation_rate"],
        "byte_dev_during": d["byte_deviation_rate"],
        "byte_dev_post": sec["post"]["byte_deviation_rate"],
        "join_rate": s["join_rate"],
        "achieved_rps_pre": sec["pre"]["achieved_rps"],
        "achieved_rps_during": d["achieved_rps"],
        "achieved_rps_post": sec["post"]["achieved_rps"],
        "achieved_ratio_during": round(d["achieved_rps"] / s["target_total_rps"], 4),
        "disturb_len_s": round(dur_len, 2) if dur_len is not None else "",
        "during_window_rel": f"{d['window_rel_s'][0]:.1f}-{d['window_rel_s'][1]:.1f}",
        "s1_knee_ratio_pre": sec["pre"]["s1_knee_ratio"],
        "s1_knee_ratio_during": d["s1_knee_ratio"],
        "s1_knee_ratio_expected": EXPECT_KNEE[s["policy"]],
        "s1_share_during": d["s1_share"],
        "unjoined_during": d["site_distribution"]["unjoined"],
        "clock_off_p50_ms": s["clock_offset_est_ms"]["p50"],
    })

out = os.path.join(C.ANA, "tables", "table0_integrity.csv")
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

hdr = ("run_id                 join   dev_pre/dur/post        rps_dur  달성  "
       "교란s   knee(dur/기대)")
print(hdr)
print("-" * len(hdr))
for r in rows:
    print(f"{r['run_id']:22s} {r['join_rate']:.4f} "
          f"{r['byte_dev_pre']:.5f}/{r['byte_dev_during']:.5f}/{r['byte_dev_post']:.5f} "
          f"{r['achieved_rps_during']:8.1f} {r['achieved_ratio_during']*100:5.1f}% "
          f"{str(r['disturb_len_s']):>7s}  "
          f"{r['s1_knee_ratio_during']:.2f}/{r['s1_knee_ratio_expected']:.2f}")
print(f"\n-> {out}")

# 이상치 표시
print("\n[검토 필요]")
flag = 0
for r in rows:
    msgs = []
    if r["join_rate"] != 1.0:
        msgs.append(f"조인율 {r['join_rate']}")
    for k in ("byte_dev_pre", "byte_dev_during", "byte_dev_post"):
        if r[k] and r[k] > 0:
            msgs.append(f"{k}={r[k]}")
    if r["policy"] == "site_s3" and r["achieved_ratio_during"] < 0.99:
        msgs.append(f"site_s3 달성률 {r['achieved_ratio_during']*100:.1f}%<99%")
    if r["disturb_len_s"] != "" and abs(r["disturb_len_s"] - 120) > 5:
        msgs.append(f"교란길이 {r['disturb_len_s']}s")
    if msgs:
        flag += 1
        print(f"  {r['run_id']:22s} {'; '.join(msgs)}")
if not flag:
    print("  없음")

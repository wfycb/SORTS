#!/usr/bin/env python3
"""이미 끝난 런의 원자료로 summary.json 을 다시 계산한다 (지시서 v6 §3.4).

필드가 추가/개명돼도 런을 다시 돌릴 필요가 없다. 원자료(load_c*.csv,
envoy_access.log.gz)와 meta.json 만 있으면 된다.
    python3 recompute.py <batch_dir>
"""
import json
import os
import sys

sys.path.insert(0, "/home/user/exp")
import run_all  # noqa: E402

batch = sys.argv[1]
for rid in sorted(os.listdir(batch)):
    rundir = os.path.join(batch, rid)
    meta_p = os.path.join(rundir, "meta.json")
    if not os.path.isdir(rundir) or not os.path.exists(meta_p):
        continue
    m = json.load(open(meta_p))
    run = {"run_id": m["run_id"], "policy": m["policy"], "disturb": m["disturb"],
           "total_rps": m.get("total_rps")}
    # 개정 A §3.1: 구간은 벽시계 마크에서 다시 만든다. 옛 산출물은 마크가
    # (what, ts, spec) 형식이고 clock 정보가 없어 d12=0 으로 근사한다
    # (실측 오프셋 약 20ms, GUARD 2s 에 비해 무시 가능).
    d12 = m.get("clock", {}).get("d12_s", 0.0)
    marks = []
    for mk in m.get("marks", []):
        if isinstance(mk, dict) and "t_issue" in mk:
            marks.append(mk)
        else:                                     # 구형: {"what","ts","spec"}
            w = mk["what"] if isinstance(mk, dict) else mk[0]
            ts = mk["ts"] if isinstance(mk, dict) else mk[1]
            ph = ("start" if w in ("radio_on", "stress_on", "ramp_0")
                  else "end" if w in ("radio_off", "stress_off", "ramp_clear")
                  else "other")
            marks.append({"what": w, "phase": ph, "t_issue": ts, "t_done": ts})
    nominal = json.load(open("/home/user/exp/manifest_smoke.json"))
    nom = next((r for r in nominal["runs"] if r["run_id"] == m["run_id"]), None)
    ds = float(nom["disturb_start"]) if nom else 0.0
    de = float(nom["disturb_end"]) if nom else float(m["duration"])
    sections = run_all.build_sections(marks, m["t_meas"], m["duration"],
                                      d12, ds, de)
    hm = run_all.load_hostmap(os.path.join(rundir, "envoy_access.log.gz"))
    res = run_all.summarize(rundir, run, m["t_meas"], hm, sections)
    json.dump(res, open(os.path.join(rundir, "summary.json"), "w"),
              ensure_ascii=False, indent=1)
    d = res["sections"]["during"]
    print(f"  {rid:>20s}  S1 {d['s1_share_rps']:>7.1f}/s  "
          f"무릎비 {d['s1_knee_ratio']:>5.2f}  조인율 {res['join_rate']}")
print("재계산 완료")

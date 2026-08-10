#!/usr/bin/env python3
"""부수발견① 승격 검증: c1 단독 극단 창의 커넥션별 위반율 (I-8 복권 대조)."""
import csv, json, os, sys
RUNS = "/home/user/exp/runs/taskA-20260809"
SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
GUARD = 2.0

def win43(m):
    d43 = m["clock"]["d43_s"]
    mk = {x["what"]: x for x in m["marks"]}
    return {"c1only": (mk["c1_extreme"]["t43_done"]+GUARD,
                       mk["c2_extreme"]["t_issue"]+d43-GUARD),
            "both": (mk["c2_extreme"]["t43_done"]+GUARD,
                     mk["clear_all"]["t_issue"]+d43-GUARD)}

for run in ("T10_fartier_both_edge","T11_strictfar_both_edge"):
    rd = os.path.join(RUNS, run)
    m = json.load(open(os.path.join(rd,"meta.json")))
    d12, d43 = m["clock"]["d12_s"], m["clock"]["d43_s"]
    W = win43(m)
    print("==", run)
    for wname in ("c1only","both"):
        lo43, hi43 = W[wname]
        lo, hi = lo43-d43+d12, hi43-d43+d12
        for coh in ("c1","c2"):
            per = {}
            for r in csv.DictReader(open(os.path.join(rd,"load_%s.csv"%coh))):
                if r["warmup"] == "1":
                    continue
                t = float(r["end_ts"])
                if not (lo <= t <= hi):
                    continue
                c = per.setdefault(int(r["conn"]), [0,0])
                c[0] += 1
                bad = (r["status"] != "200") or (float(r["corrected_ms"]) > SLO[r["ep"]])
                if bad:
                    c[1] += 1
            if not per:
                continue
            tot = sum(v[0] for v in per.values()); vio = sum(v[1] for v in per.values())
            rates = sorted(round(100*v[1]/v[0],1) for v in per.values() if v[0])
            print("  %-7s %-3s n=%6d  전체 %5.2f%%  커넥션별(%d개): %s" %
                  (wname, coh, tot, 100*vio/tot, len(per), rates))

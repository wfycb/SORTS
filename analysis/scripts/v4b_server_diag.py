#!/usr/bin/env python3
"""V4 보조 — 서버 교란이 실제로 S3 를 열화시켰는지, 분배가 정말 안 움직였는지.

지표 f_c = Envoy 필드18(업스트림 왕복) − d_net. 서버 처리시간이다.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import common as C  # noqa: E402

out = []
for pol in C.POLICIES:
    for dis in ("none", "server"):
        rid = f"A_{dis}_{pol}"
        df = C.load_df(rid)
        sec = C.sections_of(rid)
        for sname in ("pre", "during"):
            a, b = sec[sname]
            s = df[(df["t_rel"] >= a) & (df["t_rel"] < b) & (df["valid"] == 1)]
            for site in ("S1", "S2", "S3"):
                ss = s[(s["site"] == site) & s["fc_ms"].notna()]
                if not len(ss):
                    continue
                sh = 100 * (s["site"] == site).sum() / max(
                    (s["site"].isin(["S1", "S2", "S3"])).sum(), 1)
                out.append({
                    "policy": pol, "disturb": dis, "section": sname, "site": site,
                    "n": len(ss),
                    "share_pct": round(sh, 3),
                    "fc_p50": round(ss["fc_ms"].quantile(.50), 3),
                    "fc_p95": round(ss["fc_ms"].quantile(.95), 3),
                    "fc_p99": round(ss["fc_ms"].quantile(.99), 3),
                })
d = pd.DataFrame(out)
p = os.path.join(C.ANA, "tables", "table3b_fc_by_site.csv")
d.to_csv(p, index=False)
print(f"-> {p}\n")

piv = d.pivot_table(index=["policy", "site"], columns=["disturb", "section"],
                    values="fc_p95")
print("[f_c p95 (ms) — 서버 처리시간]")
print(piv.round(2).to_string())
print("\n[사이트 몫 % — server 런 pre vs during]")
sp = d[d["disturb"] == "server"].pivot_table(index=["policy", "site"],
                                             columns="section", values="share_pct")
sp["delta_pp"] = (sp["during"] - sp["pre"]).round(3)
print(sp.round(3).to_string())

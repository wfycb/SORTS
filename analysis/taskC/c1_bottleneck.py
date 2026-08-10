#!/usr/bin/env python3
"""양 코호트 창의 남은 붕괴는 어디서 오는가 — 사이트별로 가른다.

request_id 로 load csv(진실 지연)와 envoy_access.log(라우팅)를 조인해
"위반이 특정 사이트로 간 요청에 몰려 있는가"를 본다.
몰려 있으면 사이트 용량(=엣지 경합, 작업 B 대상)이고,
전 사이트에 고르게 퍼져 있으면 접속측 공유 병목이다.
"""
import csv, gzip, json, os, sys
from collections import defaultdict

EXP = "/home/user/exp"
EK = json.load(open(os.path.join(EXP, "envoy_keys.json")))
SCS = {c: tuple(EK["cluster_sites"][c]) for c in EK["sorts_clusters"]}
IP_SITE = {ip: s for s, ip in EK["site_ip"].items()}
SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
GUARD = 2.0


def window(m, which):
    d12, d43 = m["clock"]["d12_s"], m["clock"]["d43_s"]
    mk = {x["what"]: x for x in m["marks"]}
    pair = {"c1only": ("c1_extreme", "c2_extreme"),
            "both": ("c2_extreme", "clear_all")}[which]
    lo43, hi43 = mk[pair[0]]["t43_done"] + GUARD, mk[pair[1]]["t_issue"] + d43 - GUARD
    return lo43 - d43 + d12, hi43 - d43 + d12


def main(rd, which="both"):
    m = json.load(open(os.path.join(rd, "meta.json")))
    lo, hi = window(m, which)
    # rid -> (site, cluster)
    site_of = {}
    with gzip.open(os.path.join(rd, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 18:
                continue
            sites = SCS.get(p[9])
            if sites is None:
                continue
            site = sites[0] if len(sites) == 1 else IP_SITE.get(p[10].split(":")[0])
            site_of[p[1]] = site
    agg = defaultdict(lambda: [0, 0, []])
    miss = 0
    for coh in ("c1", "c2"):
        for r in csv.DictReader(open(os.path.join(rd, "load_%s.csv" % coh))):
            if r["warmup"] == "1":
                continue
            t = float(r["end_ts"])
            if not (lo <= t <= hi):
                continue
            s = site_of.get(r["request_id"])
            if s is None:
                miss += 1
                continue
            bad = r["status"] != "200" or float(r["corrected_ms"]) > SLO[r["ep"]]
            for key in ((s,), (s, r["ep"])):
                a = agg[key]
                a[0] += 1
                a[1] += bad
                a[2].append(float(r["service_ms"]))
    print("== %s / %s 창  (조인 실패 %d건)" % (os.path.basename(rd), which, miss))
    dur = hi - lo
    print("   %-14s %8s %9s %9s %9s" % ("사이트/클래스", "요청", "rps", "위반%", "svc p95"))
    for key in sorted(agg, key=lambda k: (k[0], len(k), k[1:] )):
        n, bad, svc = agg[key]
        svc.sort()
        lbl = "/".join(key)
        print("   %-14s %8d %9.1f %9.2f %9.1f"
              % (lbl, n, n / dur, 100 * bad / n, svc[int(0.95 * (len(svc) - 1))]))


if __name__ == "__main__":
    rd = sys.argv[1]
    for w in (sys.argv[2:] or ["c1only", "both"]):
        main(rd, w)
        print()

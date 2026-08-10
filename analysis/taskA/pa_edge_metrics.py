#!/usr/bin/env python3
"""작업 A 추가 지표: 엣지(S1) 개방과 개방 시 유입.

보고 요구:
  1) 엣지가 열린 tick 비율 + 그 시점의 S1 유입 rps — "평시 0" 만으로는
     부족하다. 열렸을 때 얼마가 들어가는지가 작업 B 의 입력이다.
  2) {S1} 단독 구간의 클래스별 S1 도달 rps — S1 search 단독 한계 ~200 rps
     대비 실제 유입.

방법: decisions.csv(ts=.43 벽시계)와 envoy_access.log(.43 동일 시계)를
초 단위로 조인한다. 유닛(cohort x class)별 feasible_set 에 S1 이 든 초를
개방 구간으로 보고, 그 구간의 해당 유닛 S1 도달 rps 를 센다.

sorts_reactive 런 전용 (bl_* 런은 feasible_set 이 없다).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import sys

EXP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EK = json.load(open(os.path.join(EXP, "envoy_keys.json")))
SORTS_CLUSTER_SITES = {c: tuple(EK["cluster_sites"][c]) for c in EK["sorts_clusters"]}
IP_SITE = {ip: s for s, ip in EK["site_ip"].items()}
PATH_CLASS = (("/hotels", "search"), ("/reservation", "reserve"),
              ("/recommendations", "recommend"))
# tb-load 혼합비 reserve=1 : search=1.5 : recommend=2, 코호트당 400 rps
OFFERED = {"reserve": 400 / 4.5, "search": 400 * 1.5 / 4.5,
           "recommend": 400 * 2 / 4.5}


def class_of(path):
    for p, k in PATH_CLASS:
        if path.startswith(p):
            return k
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rundir")
    ap.add_argument("--cohort-ips", default="10.46.0.6,10.46.0.7",
                    help="c1,c2 UE 주소 (XFF 판별)")
    a = ap.parse_args()
    c1, c2 = a.cohort_ips.split(",")
    coh_of = {c1: "c1", c2: "c2"}

    # ---- decisions: 유닛별 초 -> (S1 포함 여부, S1 단독 여부)
    dec_p = os.path.join(a.rundir, "decisions.csv")
    rows = list(csv.DictReader(open(dec_p)))
    if not rows or "feasible_set" not in rows[0]:
        sys.exit("feasible_set 열 없음 — sorts_reactive(작업 A) 런이 아니다")
    open_sec = {}      # unit -> {sec: "only"|"in"}
    n_tick = {}        # unit -> [총 tick, S1 포함 tick, S1 단독 tick]
    for r in rows:
        unit = f"{r['cohort']}_{r['class']}"
        sec = int(float(r["ts"]))
        m = [x for x in r["feasible_set"].split("|") if x]
        c = n_tick.setdefault(unit, [0, 0, 0])
        c[0] += 1
        if "S1" in m:
            c[1] += 1
            os_u = open_sec.setdefault(unit, {})
            # tick=1s 라 그 초 전체를 개방으로 본다
            os_u[sec] = "only" if m == ["S1"] else "in"
            if m == ["S1"]:
                c[2] += 1

    # ---- access log: 초당 사이트/유닛 도달
    log_p = os.path.join(a.rundir, "envoy_access.log.gz")
    s1_rps_all = {}                       # sec -> S1 총 도달 (전 유닛)
    unit_s1 = {}                          # unit -> {sec: n}
    opener = gzip.open if log_p.endswith(".gz") else open
    with opener(log_p, "rt", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 18:
                continue
            cluster = p[9]
            sites = SORTS_CLUSTER_SITES.get(cluster)
            if sites is None:
                continue
            site = sites[0] if len(sites) == 1 else IP_SITE.get(p[10].split(":")[0])
            if site != "S1":
                continue
            coh = coh_of.get(p[12])
            klass = class_of(p[3])
            if coh is None or klass is None:
                continue
            sec = int(float(p[0]))
            s1_rps_all[sec] = s1_rps_all.get(sec, 0) + 1
            unit_s1.setdefault(f"{coh}_{klass}", {}).setdefault(sec, 0)
            unit_s1[f"{coh}_{klass}"][sec] += 1

    print(f"== {a.rundir}")
    print(f"{'unit':14s} {'tick':>5s} {'S1포함%':>8s} {'S1단독%':>8s} "
          f"{'개방시 유닛S1 rps(평균/최대)':>28s} {'S1단독시 rps':>12s} {'제시rps':>8s}")
    any_open = set()
    for unit in sorted(n_tick):
        tot, inc, only = n_tick[unit]
        secs = open_sec.get(unit, {})
        any_open |= set(secs)
        u_rps = [unit_s1.get(unit, {}).get(s, 0) for s in sorted(secs)]
        only_secs = [s for s, v in secs.items() if v == "only"]
        o_rps = [unit_s1.get(unit, {}).get(s, 0) for s in sorted(only_secs)]
        mean_u = sum(u_rps) / len(u_rps) if u_rps else 0.0
        max_u = max(u_rps) if u_rps else 0
        mean_o = sum(o_rps) / len(o_rps) if o_rps else 0.0
        klass = unit.split("_", 1)[1]
        print(f"{unit:14s} {tot:5d} {100*inc/tot:8.1f} {100*only/tot:8.1f} "
              f"{mean_u:14.1f} /{max_u:5d}       {mean_o:12.1f} "
              f"{OFFERED[klass]:8.1f}")
    if any_open:
        tot_rps = [s1_rps_all.get(s, 0) for s in sorted(any_open)]
        print(f"-- 어느 유닛이든 개방된 초 {len(any_open)}s: S1 총유입 "
              f"평균 {sum(tot_rps)/len(tot_rps):.1f} rps / "
              f"최대 {max(tot_rps)} rps / p95 "
              f"{sorted(tot_rps)[int(round(0.95*(len(tot_rps)-1)))]} rps")
    else:
        print("-- 엣지 개방 없음 (전 유닛 S1 포함 0 tick)")
    closed = [v for s, v in s1_rps_all.items()
              if s not in any_open]
    if closed:
        print(f"-- 비개방 초의 S1 유입 (0 이어야 함): 합 {sum(closed)}건")


if __name__ == "__main__":
    main()

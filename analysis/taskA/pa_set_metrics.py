#!/usr/bin/env python3
"""작업 A §5.4: 허용 집합 기준 진동·전환 지표.

p4_analyze.py 를 고치지 않는다 — Phase 4 결과 재분석 경로를 깨면 안 된다.
chosen_site 는 집합 크기 2+ 에서 빈 값이라 기존 전환 지표가 무의미해지므로,
여기서 feasible_set 열 기준으로 새로 계산한다.

지표 (유닛 = cohort x class):
  set_transitions  feasible_set 이 바뀐 횟수 (Phase 4 의 "전환 80회" 대응.
                   단 다른 양이다 — strict_far 전환은 전량 이동, 집합 전환은
                   구성 변화. 표에 나란히 둘 때 이 구분을 명시할 것)
  s3_incl_pct      S3 가 집합에 있던 tick 비율 [%] (Phase 4 "듀티 34%" 의
                   진짜 비교 대상. strict_far 에선 chosen==S3 비율과 일치)
  s2/s1_incl_pct   동일 정의. s1_incl_pct 는 엣지 보존 확인(평시 0 이어야)
  mean_set_size    평균 집합 크기
  expectant_pct    EXPECTANT tick 비율
  cluster_dist     subset_cluster 분포

구판 decisions.csv (feasible_set 열 없음, Phase 0~4) 는 chosen_site 를
크기 1 집합으로 읽어 같은 지표를 낸다 — strict_far 와 비교 가능. 출력에
[호환모드] 를 표시한다.

사용:
  python3 pa_set_metrics.py RUN_DIR_또는_decisions.csv... [--t0 EPOCH --t1 EPOCH]
  --t0/--t1: 교란창 등 구간 필터 (decisions ts = .43 벽시계 기준).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter

SITES = ("S1", "S2", "S3")


def dec_path(p):
    return os.path.join(p, "decisions.csv") if os.path.isdir(p) else p


def analyze(path, t0=None, t1=None):
    rows = list(csv.DictReader(open(path)))
    if t0 is not None:
        rows = [r for r in rows if t0 <= float(r["ts"]) <= (t1 or 1e18)]
    compat = rows and "feasible_set" not in rows[0]
    units = {}
    for r in rows:
        fs = (r["chosen_site"] if compat else r["feasible_set"])
        members = tuple(x for x in fs.split("|") if x)
        units.setdefault((r["cohort"], r["class"]), []).append((members, r))
    out = {}
    for unit, seq in sorted(units.items()):
        n = len(seq)
        trans = sum(1 for i in range(1, n) if seq[i][0] != seq[i - 1][0])
        incl = {s: 100.0 * sum(1 for m, _ in seq if s in m) / n for s in SITES}
        sizes = sum(len(m) for m, _ in seq) / n
        expect = 100.0 * sum(1 for _, r in seq if r["expectant"] == "1") / n
        cl = Counter((r.get("subset_cluster") or "|".join(m) or "?")
                     for m, r in seq)
        out[unit] = {"n": n, "set_transitions": trans,
                     "s1_incl_pct": incl["S1"], "s2_incl_pct": incl["S2"],
                     "s3_incl_pct": incl["S3"], "mean_set_size": sizes,
                     "expectant_pct": expect, "cluster_dist": dict(cl)}
    return out, compat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--t0", type=float)
    ap.add_argument("--t1", type=float)
    a = ap.parse_args()
    for p in a.paths:
        path = dec_path(p)
        try:
            res, compat = analyze(path, a.t0, a.t1)
        except (OSError, KeyError) as e:
            print(f"{p}: 읽기 실패 {e}", file=sys.stderr)
            continue
        tag = " [호환모드: chosen_site→크기1 집합]" if compat else ""
        print(f"== {p}{tag}"
              + (f"  창 {a.t0}~{a.t1}" if a.t0 else ""))
        print(f"{'unit':16s} {'tick':>5s} {'전환':>5s} {'S3포함%':>8s} "
              f"{'S2포함%':>8s} {'S1포함%':>8s} {'평균크기':>8s} {'EXP%':>6s}  분포")
        for (coh, kl), m in res.items():
            dist = " ".join(f"{k}:{v}" for k, v in
                            sorted(m["cluster_dist"].items(), key=lambda x: -x[1]))
            print(f"{coh + '_' + kl:16s} {m['n']:5d} {m['set_transitions']:5d} "
                  f"{m['s3_incl_pct']:8.1f} {m['s2_incl_pct']:8.1f} "
                  f"{m['s1_incl_pct']:8.1f} {m['mean_set_size']:8.2f} "
                  f"{m['expectant_pct']:6.1f}  {dist}")


if __name__ == "__main__":
    main()

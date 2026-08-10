#!/usr/bin/env python3
"""search 응답 바이트 수렴 분석 (작업 1 Phase 2, 지시 §2).

I-5 확정 사실: 4632 = 예약 0 상태, 4474 = 잔존 예약으로 검색 결과에서 호텔이
빠진 상태. 런 시작(reserve_reset 직후)에는 4632 인데 프라이어는 4474 라
d_acc 가 3.5% 어긋난다. 이 구간이 러너의 warmup 절단(60s) **안**에서 끝나는지
확인한다. 밖이면 기존 결과에 편향 구간이 포함돼 있다는 뜻이다 (정지 조건 4).

방법: 슬라이스마다 사이트별 search 응답의 바이트 시계열을 보고
  - t_last_4632  : 마지막 4632 관측 시각 (슬라이스 t0 기준 상대)
  - n_4632/n     : 전체 대비 4632 비율
  - t_conv       : "그 뒤로는 4474 만 나오는" 경계 = t_last_4632
을 낸다. 예약은 런 안에서 단조 누적이고 reset 은 런 사이에만 있으므로
4632 -> 4474 는 단조 전환이다 (재출현하면 그 자체를 보고).

주의: 슬라이스 t0 는 envoy 로그 첫 행이다. 부하 시작(워밍업 첫 요청)과
같으므로 warmup 60s 와 같은 축이다.
"""
from __future__ import annotations

import csv
import glob
import gzip
import os
import sys

import obs

WARMUP_S = 60.0     # manifest.json / manifest_demo.json 전 런 공통 (실측 확인)


def scan(path):
    name = os.path.basename(os.path.dirname(path))
    batch = os.path.basename(os.path.dirname(os.path.dirname(path)))
    t0 = None
    per = {}   # site -> dict(n, n4632, n4474, n_other, t_last_4632, t_first, reappear)
    with gzip.open(path, "rt", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < obs.N_FIELDS:
                continue
            if p[obs.F_RESPONSE_CODE] != "200" or p[obs.F_RESPONSE_FLAGS] != "-":
                continue
            if obs.class_of(p[obs.F_PATH]) != "search":
                continue
            site = obs.site_of(p[obs.F_UPSTREAM_CLUSTER], p[obs.F_UPSTREAM_HOST],
                               allow_host_fallback=True)
            if site is None:
                continue
            try:
                ts = float(p[obs.F_START_TIME])
                nb = int(p[obs.F_BYTES_SENT])
            except ValueError:
                continue
            if t0 is None:
                t0 = ts
            d = per.setdefault(site, {"n": 0, "n4632": 0, "n4474": 0,
                                      "n_other": 0, "t_last_4632": None,
                                      "saw_4474_before_4632": False})
            d["n"] += 1
            if nb == 4632:
                d["n4632"] += 1
                if d["n4474"] > 0:
                    d["saw_4474_before_4632"] = True   # 재출현 = 단조성 위반
                d["t_last_4632"] = ts - t0
            elif nb == 4474:
                d["n4474"] += 1
            else:
                d["n_other"] += 1
    return batch, name, per


def main():
    pats = sys.argv[1:] or ["runs/d1-20260804/*/envoy_access.log.gz",
                            "runs/demo-20260805/*/envoy_access.log.gz"]
    files = sorted(f for pat in pats for f in glob.glob(pat))
    if not files:
        sys.exit("슬라이스 없음")

    rows = []
    worst = 0.0
    violations = []
    for path in files:
        batch, name, per = scan(path)
        for site in sorted(per):
            d = per[site]
            t = d["t_last_4632"]
            rows.append((batch, name, site, d["n"], d["n4632"], d["n4474"],
                         d["n_other"], t, d["saw_4474_before_4632"]))
            if t is not None:
                worst = max(worst, t)
                if t > WARMUP_S:
                    violations.append((batch, name, site, t))

    print("=" * 100)
    print("search 바이트 수렴: 사이트별 마지막 4632 시각 (슬라이스 t0 기준, warmup={}s)"
          .format(WARMUP_S))
    print("=" * 100)
    print("{:14s} {:22s} {:4s} {:>8s} {:>7s} {:>8s} {:>6s} {:>12s} {:>6s}"
          .format("batch", "run", "site", "n", "4632", "4474", "other",
                  "t_last_4632", "재출현"))
    for (b, n, s, tot, a, c, o, t, re_) in rows:
        print("{:14s} {:22s} {:4s} {:8d} {:7d} {:8d} {:6d} {:>12s} {:>6s}"
              .format(b, n[:22], s, tot, a, c, o,
                      "-" if t is None else "{:.1f}".format(t),
                      "★있음" if re_ else ""))
    print("-" * 100)
    print("최악 t_last_4632 = {:.1f}s  (warmup {}s {})"
          .format(worst, WARMUP_S,
                  "안 — 통과" if worst <= WARMUP_S else "밖 — ★정지 조건 4"))
    if violations:
        print("\n★ warmup 밖 수렴 (정지 조건 4 후보):")
        for v in violations:
            print("  {} {} {} t={:.1f}s".format(*v))

    out = "analysis/obs_replay/byte_converge.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["batch", "run", "site", "n_search", "n_4632", "n_4474",
                    "n_other", "t_last_4632_s", "reappear"])
        for r in rows:
            w.writerow(r)
    print("\n-> " + out)


if __name__ == "__main__":
    main()

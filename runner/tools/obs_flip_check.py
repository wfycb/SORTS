#!/usr/bin/env python3
"""결정 뒤집힘 검사 (작업 1 Phase 1, 지시 §3.3 보강).

마진 표(obs_replay.py 표 4)는 셀당 **tick p95 의 중앙값** 하나로 임계를 낸다.
그건 정상상태 요약이라, 한 tick 만 튀어서 결정이 뒤집히는 경우를 못 본다.
여기서는 **tick 단위로** 상수판과 관측판의 keep/drop 을 각각 계산해서
불일치한 tick 을 전부 센다.

  keep(site, cls, band) := band >= bytes*8*overhead / (SLO - GB - d_net - f_c)
  뒤집힘 := keep(f_c=프라이어) != keep(f_c=추정)

밴드는 tick 마다 실제로 뭐였는지 로그에 없다(무선 셰이핑은 tc 쪽이다).
그래서 보고 밴드 5개 각각에 대해 "그 밴드였다면" 을 전부 돌린다.
"""
from __future__ import annotations

import argparse
import collections
import csv

import yaml

import obs
from obs_replay import REPORT_BANDS, threshold_kbit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sorts.yaml")
    ap.add_argument("--ticks", default="analysis/obs_replay/demo_strict/ticks.csv")
    a = ap.parse_args()

    cfg = yaml.safe_load(open(a.config))
    slo, gb, ov = cfg["slo_ms"], cfg["gb_ms"], cfg["overhead"]
    d_net, prior_by = cfg["d_net_ms"], cfg["resp_bytes"]

    flips = collections.Counter()
    total = collections.Counter()
    examples = collections.defaultdict(list)

    with open(a.ticks) as f:
        for r in csv.DictReader(f):
            if r["src"] != obs.SRC_OBS:
                continue
            s, c = r["site"], r["class"]
            nb = float(prior_by[c])
            fc_e = float(r["fc_est"])
            fc_p = float(r["fc_prior"])
            t_e = threshold_kbit(nb, slo[c], gb, d_net[s], fc_e, ov)
            t_p = threshold_kbit(nb, slo[c], gb, d_net[s], fc_p, ov)
            for b in REPORT_BANDS:
                key = (s, c, b)
                total[key] += 1
                if (b >= t_p) != (b >= t_e):
                    flips[key] += 1
                    if len(examples[key]) < 5:
                        examples[key].append(
                            (r["file"], r["t_rel"], fc_e, fc_p, t_e, t_p,
                             r["n"]))

    print("=" * 82)
    print("결정 뒤집힘 = 상수판 keep/drop != 관측판 keep/drop  (src=obs tick 만)")
    print("=" * 82)
    print("{:4s} {:10s} {:>7s} {:>8s} {:>8s} {:>8s}"
          .format("site", "class", "band", "ticks", "뒤집힘", "비율%"))
    any_flip = False
    for (s, c, b) in sorted(total, key=lambda k: (k[0], k[1], -k[2])):
        n = total[(s, c, b)]
        k = flips[(s, c, b)]
        if k:
            any_flip = True
        print("{:4s} {:10s} {:7d} {:8d} {:8d} {:8.3f}"
              .format(s, c, b, n, k, k / n * 100 if n else 0.0))
    if not any_flip:
        print("\n  뒤집힘 0건 — 모든 밴드에서 관측 도입이 결정을 바꾸지 않았다.")
    else:
        print("\n" + "=" * 82)
        print("뒤집힌 tick 예시 (셀·밴드당 최대 5개)")
        print("=" * 82)
        for key in sorted(examples):
            s, c, b = key
            print("\n{} {} @ {} kbit  ({}건)".format(s, c, b, flips[key]))
            print("  {:22s} {:>8s} {:>9s} {:>9s} {:>9s} {:>9s} {:>6s}"
                  .format("file", "t_rel", "f_c추정", "f_c프라이어",
                          "임계(추정)", "임계(상수)", "n"))
            for (fn, t, fe, fp, te, tp, nn) in examples[key]:
                print("  {:22s} {:>8s} {:9.3f} {:9.3f} {:>9s} {:>9s} {:>6s}"
                      .format(fn[:22], t, fe, fp,
                              "inf" if te == float("inf") else "{:.0f}".format(te),
                              "inf" if tp == float("inf") else "{:.0f}".format(tp),
                              nn))


if __name__ == "__main__":
    main()

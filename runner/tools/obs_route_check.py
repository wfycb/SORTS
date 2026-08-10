#!/usr/bin/env python3
"""라우팅 기준 뒤집힘 검사 (작업 1 Phase 2, 지시 §1).

obs_flip_check.py 의 셀 단위 keep/drop 은 라우팅 숫자가 아니다 — 실제 결정은
site_order [S3, S2, S1] 에서 slack > 0 인 첫 사이트라서,

  - S3 뒤집힘은 라우팅을 바꾼다 (첫 순회 대상).
  - S1 뒤집힘은 대개 안 바꾼다 (마지막이라 drop 이어도 S1+EXPECTANT 선택).
  - S2 뒤집힘은 S3 가 이미 drop 일 때만 바꾼다.

여기서는 tick·클래스·밴드마다 site_order 를 적용해 상수판/관측판 각각의
chosen_site 를 산출하고, **선택이 실제로 달라진 비율**을 주 지표로 낸다.

판본 정의 (Phase 4 arm 과 일치):
  상수판(const) = 프라이어 f_c + 프라이어 bytes  (Phase 0 거동)
  관측판(obs)   = ticks.csv 의 fc_est + bytes_est (est_f_c + est_resp_bytes ON)

밴드는 tick 로그에 없으므로(무선 셰이핑은 tc 쪽) 보고 밴드 각각에 대해
"그 밴드였다면"을 전부 돌린다 — obs_flip_check 와 같은 관례.
"""
from __future__ import annotations

import argparse
import collections
import csv

import yaml

import obs
from obs_replay import REPORT_BANDS

SITE_ORDER = ("S3", "S2", "S1")     # sorts.yaml site_order 와 대조 검증한다


def decide(cfg, klass, fc_by_site, d_acc):
    """slack = SLO - GB - d_net - f_c - d_acc, site_order 첫 양수. 없으면 마지막+EXPECTANT."""
    chosen, expectant = None, False
    for site in cfg["site_order"]:
        slack = (cfg["slo_ms"][klass] - cfg["gb_ms"] - cfg["d_net_ms"][site]
                 - fc_by_site[site] - d_acc)
        if slack > 0:
            chosen = site
            break
    if chosen is None:
        chosen = cfg["site_order"][-1]
        expectant = True
    return chosen, expectant


def load_ticks(path):
    """ticks.csv -> {(file,tick): {"fc_est":{(s,c):v}, "fc_prior":..., "bytes_est":{c:v}, "t_rel":t}}"""
    ticks = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            key = (r["file"], int(r["tick"]))
            e = ticks.setdefault(key, {"fc_est": {}, "fc_prior": {},
                                       "bytes_est": {}, "bytes_prior": {},
                                       "t_rel": float(r["t_rel"])})
            s, c = r["site"], r["class"]
            e["fc_est"][(s, c)] = float(r["fc_est"])
            e["fc_prior"][(s, c)] = float(r["fc_prior"])
            e["bytes_est"][c] = float(r["bytes_est"])
            e["bytes_prior"][c] = float(r["bytes_prior"])
    return ticks


def route_flips(cfg, ticks, bands=REPORT_BANDS):
    """주 지표: (tick,class,band) 중 chosen_const != chosen_obs 비율.

    반환: (total, flips, by_class_band, pair_counter, examples)
    """
    ov = cfg["overhead"]
    total = collections.Counter()
    flips = collections.Counter()
    pairs = collections.Counter()          # (const_site -> obs_site) 방향
    examples = collections.defaultdict(list)
    for (fname, tick), e in ticks.items():
        for c in obs.CLASSES:
            fc_e = {s: e["fc_est"][(s, c)] for s in obs.SITES}
            fc_p = {s: e["fc_prior"][(s, c)] for s in obs.SITES}
            for b in bands:
                d_acc_p = e["bytes_prior"][c] * 8.0 / b * ov
                d_acc_e = e["bytes_est"][c] * 8.0 / b * ov
                ch_p, _ = decide(cfg, c, fc_p, d_acc_p)
                ch_e, _ = decide(cfg, c, fc_e, d_acc_e)
                key = (c, b)
                total[key] += 1
                if ch_p != ch_e:
                    flips[key] += 1
                    pairs[(c, b, ch_p, ch_e)] += 1
                    if len(examples[key]) < 3:
                        examples[key].append((fname, e["t_rel"], ch_p, ch_e))
    return total, flips, pairs, examples


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sorts.yaml")
    ap.add_argument("--ticks", default="analysis/obs_replay/demo_strict_p2/ticks.csv")
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    cfg = yaml.safe_load(open(a.config))
    assert tuple(cfg["site_order"]) == SITE_ORDER, cfg["site_order"]
    ticks = load_ticks(a.ticks)

    total, flips, pairs, examples = route_flips(cfg, ticks)

    print("=" * 78)
    print("라우팅 기준 뒤집힘 = chosen_site(상수판) != chosen_site(관측판)  {}"
          .format(a.label or a.ticks))
    print("  ({} tick, 밴드 {}개를 각각 가정)".format(len(ticks), len(REPORT_BANDS)))
    print("=" * 78)
    print("{:10s} {:>7s} {:>8s} {:>8s} {:>8s}   {}".format(
        "class", "band", "ticks", "뒤집힘", "비율%", "방향(건수)"))
    g_tot = g_flip = 0
    for (c, b) in sorted(total, key=lambda k: (k[0], -k[1])):
        n, k = total[(c, b)], flips[(c, b)]
        g_tot += n
        g_flip += k
        dirs = ", ".join("{}->{} {}".format(p, o, v)
                         for (cc, bb, p, o), v in sorted(pairs.items())
                         if cc == c and bb == b)
        print("{:10s} {:7d} {:8d} {:8d} {:8.3f}   {}".format(
            c, b, n, k, k / n * 100 if n else 0.0, dirs))
    print("-" * 78)
    print("전체: {} / {} = {:.4f}%".format(g_flip, g_tot,
                                           g_flip / g_tot * 100 if g_tot else 0))
    if examples:
        print("\n예시 (클래스·밴드당 최대 3):")
        for key in sorted(examples):
            for (fn, t, p, o) in examples[key]:
                print("  {:10s} @{:5d}kbit  {:24s} t={:8.1f}  {} -> {}".format(
                    key[0], key[1], fn[:24], t, p, o))


if __name__ == "__main__":
    main()

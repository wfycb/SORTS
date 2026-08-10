#!/usr/bin/env python3
"""야간배치 과제3 §3.1: 해석적 후보 좁히기 (런 소모 0).

컨트롤러 모형(sorts.yaml 값)으로 밴드 b[kbit]에서
  d_acc(class) = nb*8*overhead/b [ms]
  feasible(site) iff SLO − GB − d_net − f_c − d_acc > 0
  strict_far = 가용 최원거리 1곳 / far_tier = 가용 ∩ {S3,S2} (없으면 가용 ∩ {S1})
을 계산해, 두 정책의 **집합/적재가 갈리는 밴드 구간**과 그때의 사이트별
유입(rps)을 총부하·mix 비율로 산출한다. 갈림존(바이트 2상태) 회피도 표시.

사용: python3 t3_analytic.py --bands 6000,4500,2300,1600,1000 --loads 500,600,800,1400
"""
import argparse

import yaml

CFG = "/home/user/exp/sorts.yaml"
MIX_SHARE = {"reserve": 1 / 4.5, "search": 1.5 / 4.5, "recommend": 2 / 4.5}
SEARCH_BYTES = (4474, 4632)


def d_acc(cfg, klass, band_kbit, nb=None):
    nb = nb if nb is not None else cfg["resp_bytes"][klass]
    return nb * 8.0 * cfg["overhead"] / band_kbit if band_kbit else 0.0


def feasible(cfg, klass, band_kbit, nb=None):
    ok = []
    for s in cfg["site_order"]:
        slack = (cfg["slo_ms"][klass] - cfg["gb_ms"] - cfg["d_net_ms"][s]
                 - cfg["f_c_ms"][klass][s] - d_acc(cfg, klass, band_kbit, nb))
        if slack > 0:
            ok.append(s)
    return ok


def choice(cfg, policy, klass, band_kbit, nb=None):
    f = feasible(cfg, klass, band_kbit, nb)
    if policy == "strict_far":
        return [f[0]] if f else []          # site_order 가 원거리 우선
    far = [s for s in f if s in ("S3", "S2")]
    return far if far else ([s for s in f if s == "S1"] or [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bands", default="6000,4500,3585,2300,1901,1600,1266,1000")
    ap.add_argument("--loads", default="500,600,800,1400")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(CFG))
    bands = [int(x) for x in a.bands.split(",")]
    loads = [int(x) for x in a.loads.split(",")]

    print("== 클래스×밴드: 정책별 선택 집합 (밴드 코호트 기준; 무제한 코호트는 "
          "d_acc=nb*8*1.1/20000) ==")
    print(f"{'band':>6} {'class':>10} {'d_acc':>7} {'feasible':>12} "
          f"{'strict':>8} {'far_tier':>10} {'search바이트갈림?':>8}")
    for b in bands:
        for k in ("search", "reserve", "recommend"):
            f = feasible(cfg, k, b)
            st = choice(cfg, "strict_far", k, b)
            ft = choice(cfg, "far_tier", k, b)
            div = ""
            if k == "search":
                s0 = choice(cfg, "strict_far", k, b, SEARCH_BYTES[0])
                s1 = choice(cfg, "strict_far", k, b, SEARCH_BYTES[1])
                f0 = choice(cfg, "far_tier", k, b, SEARCH_BYTES[0])
                f1 = choice(cfg, "far_tier", k, b, SEARCH_BYTES[1])
                if s0 != s1 or f0 != f1:
                    div = "★갈림존"
            mark = " ≠" if set(st) != set(ft) or (len(ft) > 1) else ""
            print(f"{b:>6} {k:>10} {d_acc(cfg, k, b):>7.2f} "
                  f"{'/'.join(f) or '-':>12} {'/'.join(st) or 'EXPECT':>8} "
                  f"{'/'.join(ft) or 'EXPECT':>10}{mark} {div}")
        print()

    print("== 밴드×총부하: 사이트별 유입 rps (양 코호트 동일 밴드 가정, "
          "far_tier 는 티어 내 균등 분산 근사) ==")
    for b in bands:
        for L in loads:
            rows = {}
            for pol in ("strict_far", "far_tier"):
                inflow = {"S1": 0.0, "S2": 0.0, "S3": 0.0}
                for k, sh in MIX_SHARE.items():
                    ch = choice(cfg, pol, k, b)
                    for s in ch:
                        inflow[s] += L * sh / len(ch)
                rows[pol] = inflow
            print(f" band={b:>5} load={L:>5}  "
                  f"strict S1/S2/S3={rows['strict_far']['S1']:.0f}/"
                  f"{rows['strict_far']['S2']:.0f}/{rows['strict_far']['S3']:.0f}"
                  f"   far_tier={rows['far_tier']['S1']:.0f}/"
                  f"{rows['far_tier']['S2']:.0f}/{rows['far_tier']['S3']:.0f}")
        print()


if __name__ == "__main__":
    main()

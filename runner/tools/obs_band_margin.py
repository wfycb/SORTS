#!/usr/bin/env python3
"""밴드-갈림존 거리 자동 검사 (작업 1 Phase 2, I-7 방어선).

search 응답 바이트는 예약 상태의 함수다 (ISSUES.md I-5/I-7): 예약 0 이면
4632, 잔존 예약으로 호텔이 빠지면 4474. 임계 rate

    t(nb) = nb * 8 * overhead / (SLO - GB - d_net - f_c)   [kbit]

는 nb 에 비례하므로 4474 기준과 4632 기준 임계 사이에 **갈림존**이 생긴다.
사용 밴드가 갈림존 안에 있으면 같은 무선 상태에서 바이트 상태에 따라
chosen_site 가 달라진다. demo-20260805 에서 결정이 안 바뀐 것은 실사용
밴드가 전부 갈림존 밖이었기 때문이고, 이는 **밴드 배치의 결과이지 설계
보장이 아니다.** 밴드·SLO·f_c 를 바꿀 때마다 이 스크립트로 재확인한다.

사용:
    python3 obs_band_margin.py                       # 기본 밴드 집합
    python3 obs_band_margin.py --bands 20000,2300    # 임의 밴드
    python3 obs_band_margin.py --ramp                # 램프 12단계 포함(기본)

종료 코드: 갈림존 안에 밴드가 있으면 1 (파이프라인에서 잡히게).
"""
from __future__ import annotations

import argparse
import sys

import yaml

# search 의 2상태 (I-5). 다른 클래스는 단일값이라 갈림존이 없다.
SEARCH_BYTES = (4474, 4632)
# run_all.py 실사용: 무제한(명목 20000), poor 2300, 램프 12단계 (§4.1)
RAMP = [round(20000 - i * (20000 - 1600) / 11) for i in range(12)]
DEFAULT_BANDS = sorted(set([20000, 2300] + RAMP), reverse=True)


def thresholds(cfg, klass, nb):
    """사이트별 keep 임계 [kbit]. 분모<=0 이면 어떤 rate 로도 불가(inf)."""
    out = {}
    for site in cfg["site_order"]:
        denom = (cfg["slo_ms"][klass] - cfg["gb_ms"] - cfg["d_net_ms"][site]
                 - cfg["f_c_ms"][klass][site])
        out[site] = float("inf") if denom <= 0 else nb * 8.0 * cfg["overhead"] / denom
    return out


def check(cfg, bands):
    """(rows, violations). row = (site, t_lo, t_hi, 최근접 밴드, 거리, 존내 밴드들)"""
    t_lo = thresholds(cfg, "search", SEARCH_BYTES[0])
    t_hi = thresholds(cfg, "search", SEARCH_BYTES[1])
    rows, violations = [], []
    for site in cfg["site_order"]:
        lo, hi = sorted((t_lo[site], t_hi[site]))
        if lo == float("inf"):
            rows.append((site, lo, hi, None, None, []))
            continue
        inside = [b for b in bands if lo < b <= hi]
        # 거리 = 갈림존 경계까지의 최소 kbit (존 밖 밴드 기준)
        dist = min((min(abs(b - lo), abs(b - hi)) for b in bands
                    if b not in inside), default=None)
        rows.append((site, lo, hi, dist, inside))
        if inside:
            violations.append((site, inside, lo, hi))
    return rows, violations


# ---- Phase 4: 스트레스 f_c 대비 구간 모드 ---------------------------------
#
# server 축은 f_c 를 크게 움직여 keep 임계가 이동한다. 상수판(prior f_c)은
# S3 를 유지하고 관측판(스트레스 f_c 관측)은 S3 를 버리는 밴드 구간이
# "대비 구간"이다. 밴드 B 에서 사이트를 유지하는 조건은 B > t(f_c) 이므로
#
#   대비 구간 = ( max(t_keep), t_reject )
#     t_keep   = 유지가 깨지면 안 되는 쪽 임계의 최대:
#                prior f_c(상수판) / 무교란 관측 f_c 상한(관측판 정상구간)
#                x 바이트 두 상태(4474/4632) 중 큰 임계
#     t_reject = 스트레스 관측 f_c 하한(최약 윈도)으로 계산한 임계의 최소
#                (nb 는 작은 쪽 4474 — 임계가 작아지는 보수 방향)
#
# 분모(SLO−GB−d_net−f_c_str) <= 0 이면 어떤 밴드에서도 관측판이 그 사이트를
# 버린다 → 대비 구간 = (t_keep, inf), 즉 "밴드 무관".
# f_c 상/하한은 s6 캘리브레이션의 2s 윈도 p95 분포에서 온다 (fc_windowed.py).

def _parse_fc(s):
    """"search=13.3,reserve=5.3,recommend=4.5" -> dict"""
    out = {}
    for part in s.split(","):
        k, v = part.split("=")
        out[k.strip()] = float(v)
    return out


def _t(cfg, klass, site, fc, nb):
    denom = cfg["slo_ms"][klass] - cfg["gb_ms"] - cfg["d_net_ms"][site] - fc
    return float("inf") if denom <= 0 else nb * 8.0 * cfg["overhead"] / denom


def contrast(cfg, site, fc_str_lo, fc_norm_hi, band):
    """클래스별 대비 구간 계산 + 후보 밴드 판정. 반환: (rows, ok_classes)"""
    rows, ok = [], []
    for klass in ("search", "reserve", "recommend"):
        nb = cfg["resp_bytes"][klass]
        nbs = SEARCH_BYTES if klass == "search" else (nb, nb)
        # 유지측: prior(상수판)와 무교란 관측 상한(관측판) 중 나쁜 쪽,
        # 바이트는 임계가 커지는 큰 쪽.
        t_keep = max(_t(cfg, klass, site, cfg["f_c_ms"][klass][site], max(nbs)),
                     _t(cfg, klass, site, fc_norm_hi[klass], max(nbs)))
        # 기각측: 최약 스트레스 윈도 + 작은 바이트 (보수).
        t_rej = _t(cfg, klass, site, fc_str_lo[klass], min(nbs))
        band_ok = (t_keep < band < t_rej) if t_rej != float("inf") \
            else (t_keep < band)
        m_keep = (band / t_keep - 1.0) * 100 if t_keep > 0 else None
        m_rej = (1.0 - band / t_rej) * 100 if t_rej != float("inf") else None
        rows.append((klass, t_keep, t_rej, band_ok, m_keep, m_rej))
        if band_ok:
            ok.append(klass)
    return rows, ok


def run_stressed_mode(cfg, a):
    fc_lo = _parse_fc(a.stressed_fc_lo)
    fc_hi = _parse_fc(a.obs_fc_normal_hi)
    band = int(a.band)
    site = a.stressed_site
    print("=" * 78)
    print(f"server 축 대비 구간 (스트레스 사이트 {site}, 후보 밴드 {band} kbit)")
    print(f"  스트레스 관측 f_c 하한: {fc_lo}")
    print(f"  무교란 관측 f_c 상한:   {fc_hi}")
    print("=" * 78)
    rows, ok = contrast(cfg, site, fc_lo, fc_hi, band)
    print("{:10s} {:>12s} {:>12s} {:>8s} {:>10s} {:>10s}".format(
        "class", "keep임계", "reject임계", "대비?", "유지여유%", "기각여유%"))
    for (klass, tk, tr, bok, mk, mr) in rows:
        print("{:10s} {:>12.0f} {:>12s} {:>8s} {:>10s} {:>10s}".format(
            klass, tk,
            "inf(무관)" if tr == float("inf") else f"{tr:.0f}",
            "예" if bok else "아니오",
            "-" if mk is None else f"{mk:+.0f}",
            "-" if mr is None else f"{mr:+.0f}"))
    # 기존 검사(바이트 갈림존)도 후보 밴드에 대해 같이 돌린다.
    _, viol = check(cfg, [band])
    if viol:
        print("★ 후보 밴드가 search 바이트 갈림존 안에 있다 — 사용 불가")
        return 1
    if "search" not in ok:
        print("★ search 대비 실패 — 정지 조건 8 해당(어떤 밴드로도 대비가 안")
        print("★ 나오는지는 keep/reject 임계 열에서 판단할 것)")
        return 1
    print(f"\nsearch 대비 성립. 대비 성립 클래스: {ok}")
    miss = [k for k in ("search", "reserve", "recommend") if k not in ok]
    if miss:
        print(f"대비 불성립 클래스: {miss} — search 우선 방침(Phase 4 §2)에 따라 진행.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sorts.yaml")
    ap.add_argument("--bands", default=None,
                    help="쉼표 구분 kbit. 기본 = 20000,2300 + 램프 12단계")
    ap.add_argument("--stressed-fc-lo", default=None,
                    help="스트레스 관측 f_c 하한 (최약 2s 윈도 p95). "
                         "예: search=10.904,reserve=3.542,recommend=3.709")
    ap.add_argument("--obs-fc-normal-hi", default=None,
                    help="무교란 관측 f_c 상한 (2s 윈도 p95 최대)")
    ap.add_argument("--stressed-site", default="S3")
    ap.add_argument("--band", default=None, help="server 축 후보 밴드 kbit")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config))
    if a.stressed_fc_lo:
        if not (a.obs_fc_normal_hi and a.band):
            ap.error("--stressed-fc-lo 에는 --obs-fc-normal-hi 와 --band 가 필요")
        return run_stressed_mode(cfg, a)
    bands = ([int(x) for x in a.bands.split(",")] if a.bands
             else DEFAULT_BANDS)

    print("=" * 78)
    print("search 바이트 갈림존 vs 사용 밴드   (bytes {} -> {})".format(*SEARCH_BYTES))
    print("밴드: {}".format(bands))
    print("=" * 78)
    rows, violations = check(cfg, bands)
    print("{:4s} {:>10s} {:>10s} {:>12s}   {}".format(
        "site", "존하한", "존상한", "최소거리", "존내 밴드"))
    for (site, lo, hi, dist, inside) in rows:
        print("{:4s} {:>10.0f} {:>10.0f} {:>12s}   {}".format(
            site, lo, hi,
            "-" if dist is None else "{:.0f}".format(dist),
            inside if inside else "없음"))
    if violations:
        print("\n" + "★" * 39)
        print("★★ 갈림존 안에 사용 밴드가 있다! 같은 무선 상태에서 예약 상태에")
        print("★★ 따라 chosen_site 가 달라진다. 밴드나 SLO 를 조정하든지,")
        print("★★ est_resp_bytes 를 켜서 관측이 바이트를 따라가게 해라 (I-7).")
        for (site, inside, lo, hi) in violations:
            print("★★   {}: {} ∈ ({:.0f}, {:.0f}]".format(site, inside, lo, hi))
        print("★" * 39)
        return 1
    print("\n갈림존 내 사용 밴드 없음 — 현 구성에서 바이트 상태는 결정을 못 바꾼다.")
    print("(이것은 밴드 배치의 결과이지 설계 보장이 아니다. 구성 변경 시 재실행.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

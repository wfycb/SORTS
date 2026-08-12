#!/usr/bin/env python3
"""STAGE5 §4.1 — L=1400 역전(격차 −15.92 %p)의 귀속 분해 (런 0, 분석 전용).

세 원인의 크기를 기존 원자료만으로 분리한다:
  (i)   C_eff 테이블 비대칭 — 반사실 결정 재계산(실행 없음, 결정 산식만
        재적용. sorts_ctl.Controller 의 실제 메서드를 그대로 호출한다)
  (ii)  고부하 진동 — 전환 구간의 위반 몫 / 배정 버스트성
  (iii) 총수요 > 총용량 — 어떤 배정으로도 불가능한 위반 하한

사용: python3 s5_attrib.py [runs_dir]
"""
import csv
import gzip
import json
import math
import os
import sys
from collections import defaultdict

EXP = "/home/user/exp"
sys.path.insert(0, EXP)
sys.path.insert(0, os.path.join(EXP, "analysis/night-20260810"))
import sorts_ctl  # noqa: E402
import t2_policy_repeat as t2  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(EXP, "runs/stage5-20260812")
W = {"search": 1.0, "reserve": 0.278, "recommend": 0.178}
SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
D_NET = {"S1": 2.0, "S2": 15.0, "S3": 25.0}
GB = 5.0
RESP_B = {"reserve": 36, "search": 4474, "recommend": 200}
C_EQ = {"S1": 279.0, "S2": 515.0, "S3": 865.0}
K_S1_1600 = 105.4
SITE_OF_IP = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}


def base_cfg(c_eff_band):
    return {
        "ctl_period_s": 1.0, "gb_ms": GB, "overhead": 1.10, "slo_ms": dict(SLO),
        "resp_bytes": dict(RESP_B), "d_net_ms": dict(D_NET),
        "f_c_ms": {"reserve": {"S1": 1.658, "S2": 1.332, "S3": 1.348},
                   "search": {"S1": 6.899, "S2": 4.287, "S3": 4.019},
                   "recommend": {"S1": 1.240, "S2": 0.975, "S3": 1.000}},
        "site_order": ["S3", "S2", "S1"], "subset_policy": "far_tier",
        "est_resp_bytes": True, "est_f_c": True, "window_s": 2.0,
        "capacity_check": True, "headroom": 0.9, "w_eq": dict(W),
        "c_eq": dict(C_EQ),
        "set_share": {"S2|S3": {"S2": 0.58, "S3": 0.42},
                      "S1|S3": {"S1": 0.65, "S3": 0.35},
                      "S1|S2": {"S1": 0.65, "S2": 0.35},
                      "S1|S2|S3": {"S1": 0.43, "S2": 0.33, "S3": 0.24}},
        "soft_assign": True, "c_eff": True, "c_eff_band": c_eff_band,
        "cohorts": {"c1": 4096, "c2": 8192},
        "envoy_admin": "http://127.0.0.1:9901", "iface": "ogstun",
    }


def replay(rows_by_tick, ctl):
    """로그된 slack·r_eq 로 far_tier 분기를 그대로 재적용 (decide_live 사본).

    반환: 사이트별 배정 eq 합계 · EXPECTANT 틱 수 · cap 차단 수."""
    order = ctl.cfg["site_order"]
    edge = order[-1]
    tot = defaultdict(float)
    exp_n = blocked_n = 0
    for _ts, rows in rows_by_tick:
        planned = {s: 0.0 for s in order}
        for r in rows:
            slacks = {s: float(r[f"slack_{s.lower()}"]) for s in order}
            r_eq = W[r["class"]] * float(r["unit_rate_rps"] or 0.0)
            rate = float(r["observed_rate_kbit"]) if r["observed_rate_kbit"] else None
            cmap = {s: ctl.c_of(s, rate)[0] for s in order}
            if r_eq <= 0:
                continue
            far = tuple(s for s in order[:-1] if slacks[s] > 0)
            F, blk = ctl.capacity_filter(far, planned, r_eq, cmap)
            blocked_n += len(blk)
            if F:
                feas, exp = F, False
            elif slacks[edge] > 0:
                if ctl._cap_room(edge, planned, r_eq, cmap) >= 0:
                    feas, exp = (edge,), False
                else:
                    feas, exp = (edge,), True
                    blocked_n += 1
            else:
                feas, exp = (edge,), True
            if exp:
                exp_n += 1
                alloc, over, _obj, _pct = ctl.soft_alloc(slacks, planned, r_eq, cmap)
                for s, v in alloc.items():
                    planned[s] += v
                    tot[s] += v
                if over > 0:
                    planned[edge] += over
                    tot[edge] += over
            else:
                for s, sh in ctl.shares_of(feas).items():
                    planned[s] += sh * r_eq
                    tot[s] += sh * r_eq
    return dict(tot), exp_n, blocked_n


def load_ticks(rd, meta, lo, hi):
    d43, d12 = meta["clock"]["d43_s"], meta["clock"]["d12_s"]
    by = defaultdict(list)
    for r in csv.DictReader(open(os.path.join(rd, "decisions.csv"))):
        t = float(r["ts"]) - d43 + d12
        if lo <= t <= hi:
            by[r["ts"]].append(r)
    return sorted(by.items())


def per_second_sites(rd, meta, lo, hi):
    """초당 사이트별 완료 건수 — 진동의 버스트성 측정용."""
    hm = {}
    with gzip.open(os.path.join(rd, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) > 17 and p[17].strip().isdigit():
                hm[p[1]] = p[10].split(":")[0]
    per = defaultdict(lambda: defaultdict(int))
    viol_by_sec = defaultdict(lambda: [0, 0])
    for c in (1, 2):
        for r in csv.DictReader(open(os.path.join(rd, f"load_c{c}.csv"))):
            if r["warmup"] != "0":
                continue
            t = float(r["end_ts"])
            if not (lo <= t <= hi):
                continue
            sec = int(t - lo)
            site = SITE_OF_IP.get(hm.get(r["request_id"], ""), "?")
            per[sec][site] += 1
            bad = r["status"] != "200" or float(r["corrected_ms"]) > SLO[r["ep"]]
            v = viol_by_sec[sec]
            v[0] += 1
            v[1] += bad
    return per, viol_by_sec


def cv(xs):
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    if m == 0:
        return None
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return round(sd / m, 3)


def main():
    runs = {"SORTS": ["s5_sorts_L1400_1", "s5_sorts_L1400_2"],
            "bl_lr": ["s5_lr_L1400_1", "s5_lr_L1400_2"]}
    print("# STAGE5 §4.1 — L=1400 역전 귀속 분해 (런 0)\n")

    # ---------------- (iii) 불가능 하한
    print("## (iii) 총수요 vs 총용량 — 배정 무관 위반 하한\n")
    L = 1400.0
    dem = {k: L * s for k, s in (("search", 1 / 3), ("reserve", 2 / 9),
                                 ("recommend", 4 / 9))}
    d_acc = {k: RESP_B[k] * 8 / 1600.0 * 1.10 for k in RESP_B}   # overhead 1.10
    print(f"{'클래스':<10s}{'수요 rps':>9s}{'d_acc[ms]':>10s}   구조적 가능 사이트"
          f"(SLO−GB−d_net−d_acc > 0)")
    feasible_sites = {}
    for k in ("search", "reserve", "recommend"):
        ok = []
        for s in ("S1", "S2", "S3"):
            budget = SLO[k] - GB - D_NET[s] - d_acc[k]
            if budget > 0:
                ok.append(f"{s}(+{budget:.1f}ms)")
            else:
                ok.append(f"~~{s}({budget:.1f})~~")
        feasible_sites[k] = [x for x in ok if not x.startswith("~~")]
        print(f"{k:<10s}{dem[k]:>9.1f}{d_acc[k]:>10.1f}   {' '.join(ok)}")
    # search 는 S1(용량 K)·S2(용량 미측정) 만 가능. 하한 = S1 용량으로만 서비스
    # 가능한 몫을 뺀 나머지 중 S3 로 갈 수밖에 없는 부분.
    s_dem = dem["search"]
    floor_s1_only = max(0.0, s_dem - K_S1_1600) / L
    print(f"\n- search 수요 {s_dem:.0f} rps, S1 유효 용량 K={K_S1_1600} eq "
          f"→ S1 만으로는 {100*floor_s1_only:.1f} %p 가 넘친다(전체 대비).")
    print("- S3 는 search 에 대해 **구조적 불가**(예산 음수) — 밴드 창에서 "
          "S3 로 간 search 는 부하와 무관하게 100 % 위반(실측 정합).")
    print("- S2 의 밴드 유효 용량은 미측정이라 하한은 구간으로만 준다:")
    for name, s2cap in (("S2 = 0 (극단 보수)", 0.0),
                        ("S2 = K_S1 비율 적용(515×0.511=263)", 515 * 0.511),
                        ("S2 = c_eq 그대로(515, 낙관)", 515.0)):
        fl = max(0.0, s_dem - K_S1_1600 - s2cap) / L
        print(f"    {name:<34s} → 하한 {100*fl:5.1f} %p")

    # ---------------- (i) 반사실 결정 재계산
    print("\n## (i) C_eff 테이블 비대칭 — 반사실 결정 재계산 (실행 없음)\n")
    scenarios = [("기준(실측 배치: S1만 밴드값)", {"S1": {1600: 105.4, 2300: 161.7}}),
                 ("가정 A: S2/S3 도 S1 과 같은 하락률 ×0.511",
                  {"S1": {1600: 105.4, 2300: 161.7},
                   "S2": {1600: round(515 * 0.511, 1), 2300: round(515 * 0.784, 1)},
                   "S3": {1600: round(865 * 0.511, 1), 2300: round(865 * 0.784, 1)}}),
                 ("가정 B: 2300k 하락률 ×0.784 (완만)",
                  {"S1": {1600: 105.4, 2300: 161.7},
                   "S2": {1600: round(515 * 0.784, 1), 2300: round(515 * 0.9, 1)},
                   "S3": {1600: round(865 * 0.784, 1), 2300: round(865 * 0.9, 1)}}),
                 ("가정 C: 거리 보정 — S2 ×0.35 / S3 ×0.15 (SLO 예산 잠식 반영)",
                  {"S1": {1600: 105.4, 2300: 161.7},
                   "S2": {1600: round(515 * 0.35, 1), 2300: round(515 * 0.6, 1)},
                   "S3": {1600: round(865 * 0.15, 1), 2300: round(865 * 0.5, 1)}})]
    res_i = {}
    for rid in runs["SORTS"]:
        rd = os.path.join(OUT, rid)
        meta = json.load(open(os.path.join(rd, "meta.json")))
        lo, hi = t2.windows(meta)["both"]
        ticks = load_ticks(rd, meta, lo, hi)
        print(f"### {rid} (both 창 {len(ticks)} tick)\n")
        print(f"{'시나리오':<46s}{'S1':>8s}{'S2':>8s}{'S3':>8s}{'EXP틱':>7s}{'cap차단':>8s}")
        for name, band in scenarios:
            ctl = sorts_ctl.Controller(base_cfg(band), dry=True)
            tot, exp_n, blk = replay(ticks, ctl)
            ssum = sum(tot.values()) or 1
            sh = {s: 100 * tot.get(s, 0) / ssum for s in ("S1", "S2", "S3")}
            res_i.setdefault(rid, {})[name] = {"share_pct": sh, "exp_ticks": exp_n,
                                               "cap_blocked": blk}
            print(f"{name:<46s}{sh['S1']:>7.1f}%{sh['S2']:>7.1f}%{sh['S3']:>7.1f}%"
                  f"{exp_n:>7d}{blk:>8d}")
        print()

    # ---------------- (ii) 진동
    print("## (ii) 고부하 진동 — 배정 버스트성과 위반 몫\n")
    res_ii = {}
    for pol, rids in runs.items():
        for rid in rids:
            rd = os.path.join(OUT, rid)
            meta = json.load(open(os.path.join(rd, "meta.json")))
            lo, hi = t2.windows(meta)["both"]
            per, viol = per_second_sites(rd, meta, lo, hi)
            secs = sorted(per)
            series = {s: [per[x].get(s, 0) for x in secs] for s in ("S1", "S2", "S3")}
            res_ii[rid] = {"policy": pol,
                           "cv": {s: cv(v) for s, v in series.items()},
                           "mean": {s: round(sum(v) / len(v), 1)
                                    for s, v in series.items()},
                           "max": {s: max(v) for s, v in series.items()}}
            print(f"{rid:20s} {pol:8s} 초당 도착 평균/최대/CV — "
                  + "  ".join(f"{s} {res_ii[rid]['mean'][s]:.0f}/"
                              f"{res_ii[rid]['max'][s]}/{res_ii[rid]['cv'][s]}"
                              for s in ("S1", "S2", "S3")))
    json.dump({"i_counterfactual": res_i, "ii_burstiness": res_ii},
              open(os.path.join(OUT, "s5_attrib.json"), "w"),
              ensure_ascii=False, indent=1)
    print(f"\n-> {OUT}/s5_attrib.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

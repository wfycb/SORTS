#!/usr/bin/env python3
"""STAGE2 본 배치 분석 — 주기 ablation 주 지표 + P-S2 판정 입력.

런별 (radio config, phase4 A-런 계열과 동일):
  ① during(교란 창) 전체 위반율 [%] — s1_analyze.server_run 과 동일 산식
     (summary.sections.during.by_cohort 의 Σ n·rate / Σ n). c1-search during 도
     병기 (phase4 §6 표 지표와의 다리).
  ② 첫 전환 시각 2기준:
     - anchor_s  = c1:search 첫 changed tick − (radio_on.t_issue + d43)
                   [P-S2-0: T=1s 에서 1.08~1.14 재현]
     - react_s   = 동일 tick − radio_on.t43_done  [P-S2-1: 적용 완료 기준]
     - react_total_s = react_s + apply_latency  [첫 전환 '완료']
  ③ burst 위반: 교란 적용 완료(t43_done)~첫 전환 완료 창의 위반 건수
     (load_c1, .12 시계로 변환; 위반 = s1_analyze.is_valid 부정 ∨ SLO 초과)
  ④ 전환 횟수: changed==1 합 (교란 창 / 런 전체) — 진동 가드레일 입력
  ⑤ 오버런 %: tick 간격 > T×1.1 (드라이런과 동일 정의) + gap 분위수
  ⑥ 반응 지연 분해 (P-S2-1-3, 표4 재현 산식): inject_done → detect(첫 changed)
     → apply 완료; 반응 창 위반 / during 위반 몫 [%]
  ⑦ 진단(변경 금지, 보고만): 잔여 위반(반응 창 이후 during) 중 해당 시각
     governing tick 의 chosen(S,class) f_c 가 src != 'obs' 또는 stale 인 몫
     — f_c 스테일(WINDOW_S 2.0) 귀속 프록시 (정의 보고서 명기)
"""
import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, "/home/user/exp/analysis/stage1")
import s1_analyze as s1  # noqa: E402  (is_valid, SLO 재사용 — 정의 단일화)


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def effect_time(root, rid):
    """v3 primitive: 가시화 = 발효. 폴러에서 첫 netem leaf 가시화 시각(.43)."""
    p = os.path.join(root, f"tcpoll_{rid}.csv")
    if not os.path.exists(p):
        return None
    for r in csv.DictReader(open(p)):
        try:
            if int(r["n_netem_c1"]) >= 1:
                return float(r["ts"])
        except (ValueError, TypeError):
            continue
    return None


def analyze_run(rd, root=None):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    summ = json.load(open(os.path.join(rd, "summary.json")))
    d43 = meta["clock"]["d43_s"]
    d12 = meta["clock"]["d12_s"]
    period = float(meta.get("arm", {}).get("effective", {}).get("ctl_period_s", 1.0))
    mk_on = [m for m in meta["marks"] if m.get("phase") == "start"][0]
    ref_issue = mk_on["t_issue"] + d43
    ref_done = mk_on["t43_done"]
    mk_off = [m for m in meta["marks"] if m.get("phase") == "end"][0]
    t_off43 = mk_off.get("t43_done", mk_off["t_done"] + d43)

    # decisions: tick·전환·첫 전환·오버런 (+P-S2-3 진단: c1:search 플립·slack)
    ticks = set()
    n_changed_all = 0
    n_changed_dur = 0
    first = None            # (ts, apply_ms) c1:search, ref_issue 이후
    c1s_switch_ts = []      # c1:search changed tick (during)
    c1s_slack2 = []         # (ts, slack_s2) c1:search (during)
    for r in csv.DictReader(open(os.path.join(rd, "decisions.csv"))):
        ts = float(r["ts"])
        ticks.add(ts)
        is_c1s = r["cohort"] == "c1" and r["class"] == "search"
        if is_c1s and ref_done <= ts < t_off43 and r["slack_s2"]:
            c1s_slack2.append((ts, float(r["slack_s2"])))
        if r["changed"] == "1":
            n_changed_all += 1
            if ref_done <= ts < t_off43:
                n_changed_dur += 1
                if is_c1s:
                    c1s_switch_ts.append(ts)
            if first is None and is_c1s and ts >= ref_issue:
                first = (ts, float(r["apply_latency_ms"] or 0.0))
    ts_sorted = sorted(ticks)
    gaps = [(b - a) * 1000 for a, b in zip(ts_sorted, ts_sorted[1:])]
    over = [g for g in gaps if g > period * 1100]

    # v3: 반응 기준점은 **물리적 발효**(폴러 A1). ref_done(ssh 반환)·ref_issue
    # 기준값은 계열 연속성 확인용으로만 병기한다 (PREREG_S2 §6~§8).
    A1 = effect_time(root, meta["run_id"]) if root else None
    anchor_s = react_s = react_total_s = react_eff = react_eff_total = None
    if first:
        anchor_s = round(first[0] - ref_issue, 3)
        react_s = round(first[0] - ref_done, 3)
        react_total_s = round(react_s + first[1] / 1000.0, 3)
        if A1 is not None:
            react_eff = round(first[0] - A1, 3)
            react_eff_total = round(react_eff + first[1] / 1000.0, 3)

    # during 전체 위반율 (server_run 산식)
    def sec_viol(sec):
        by = summ["sections"].get(sec, {}).get("by_cohort") or {}
        n = sum(v["n"] for v in by.values())
        vi = sum(v["n"] * v["slo_violation_rate"] for v in by.values())
        return (round(100 * vi / n, 3) if n else None), n
    dur_pct, dur_n = sec_viol("during")
    pre_pct, _ = sec_viol("pre")
    post_pct, _ = sec_viol("post")
    c1s = (summ["sections"].get("during", {}).get("by_cohort", {})
           .get("1", {}).get("by_endpoint", {}).get("search", {}))
    c1s_pct = round(100 * c1s.get("slo_violation_rate", 0), 3) if c1s else None

    # burst 위반 + during c1 위반 분해 (load_c1, .12 시계).
    # 창 시작 = **물리적 발효**(A1). 폴러가 없으면 구 기준(ssh 반환)으로 폴백.
    lo12 = (A1 if A1 is not None else ref_done) - d43 + d12
    hi12 = (first[0] + first[1] / 1000.0 - d43 + d12) if first else None
    off12 = t_off43 - d43 + d12
    burst_n = burst_v = 0
    bs_n = bs_v = 0         # search 한정 (표4/P-S2-1-3 산식과 동일 모집단)
    dur_c1_v = dur_c1s_v = 0
    resid = []              # 잔여 위반 (반응 창 이후 ~ 교란 해제)
    p = os.path.join(rd, "load_c1.csv")
    for r in csv.DictReader(open(p)):
        if r["warmup"] != "0":
            continue
        e = float(r["end_ts"])
        if not (lo12 <= e < off12):
            continue
        bad = (not s1.is_valid(r)) or float(r["corrected_ms"]) > s1.SLO[r["ep"]]
        is_s = r["ep"] == "search"
        if bad:
            dur_c1_v += 1
            dur_c1s_v += is_s
        if hi12 is not None and e < hi12:
            burst_n += 1
            burst_v += bad
            if is_s:
                bs_n += 1
                bs_v += bad
        elif bad:
            resid.append(e)

    # ⑦ 진단: 잔여 위반의 f_c 스테일 귀속 프록시
    obs_rows = []
    op = os.path.join(rd, "obs_state.csv")
    if os.path.exists(op):
        for r in csv.DictReader(open(op)):
            if r["class"] == "search":
                obs_rows.append((float(r["ts"]), r["site"], r["src"],
                                 r["stale_ms"]))
        obs_rows.sort()
    dec_rows = []
    for r in csv.DictReader(open(os.path.join(rd, "decisions.csv"))):
        if r["cohort"] == "c1" and r["class"] == "search":
            dec_rows.append((float(r["ts"]), r["chosen_site"]))
    dec_rows.sort()
    import bisect
    stale_attr = 0
    for e in resid:
        t43 = e - d12 + d43
        i = bisect.bisect_right(dec_rows, (t43, "\xff")) - 1
        if i < 0:
            continue
        site = dec_rows[i][1].split("|")[0]
        j = bisect.bisect_right(obs_rows, (t43, "\xff", "", "")) - 1
        src = None
        while j >= 0:
            if obs_rows[j][1] == site:
                src = obs_rows[j][2]
                st = obs_rows[j][3]
                break
            j -= 1
        if src is not None and (src != "obs" or (st and float(st) > 0)):
            stale_attr += 1

    return {
        "run_id": meta["run_id"], "T_s": period,
        "viol_pct": {"pre": pre_pct, "during": dur_pct, "post": post_pct,
                     "during_c1_search": c1s_pct, "during_n": dur_n},
        "effect_A1_rel_s": None if A1 is None else round(A1 - ref_issue, 3),
        "first_transition": {"anchor_s": anchor_s, "react_s": react_s,
                             "react_total_s": react_total_s,
                             "react_from_effect_s": react_eff,
                             "react_from_effect_total_s": react_eff_total},
        "burst": {"n": burst_n, "viol": burst_v,
                  "search_n": bs_n, "search_viol": bs_v,
                  "search_gap_share_pct":
                      round(100 * bs_v / dur_c1s_v, 1) if dur_c1s_v else None},
        "residual_viol_c1": len(resid), "residual_stale_attr": stale_attr,
        "during_c1_viol": dur_c1_v, "during_c1_search_viol": dur_c1s_v,
        "switches": {"during": n_changed_dur, "run_total": n_changed_all},
        # P-S2-3 진단 (PREREG_S2 §4): c1:search 플립 왕복 주기 = 연속 전환
        # 간격의 2배가 아니라, 같은 방향 재진입 주기 — 연속 changed 간격
        # p50 로 반주기, ×2 없이 '전환 간격 p50' 로 보고 (해석은 보고서에서)
        "c1s_flip": {
            "n_switches_during": len(c1s_switch_ts),
            "switch_gap_p50_s": round(pct([b - a for a, b in
                                           zip(c1s_switch_ts, c1s_switch_ts[1:])],
                                          0.5), 2) if len(c1s_switch_ts) > 1 else None,
            "roundtrip_p50_s": round(pct([b - a for a, b in
                                          zip(c1s_switch_ts, c1s_switch_ts[2:])],
                                         0.5), 2) if len(c1s_switch_ts) > 2 else None,
            "slack2_zero_cross": sum(
                1 for (t0, a), (t1, b) in zip(c1s_slack2, c1s_slack2[1:])
                if (a < 0) != (b < 0)),
        },
        "loop": {"n_ticks": len(ts_sorted),
                 "gap_ms_p50": round(pct(gaps, 0.5), 2) if gaps else None,
                 "gap_ms_p99": round(pct(gaps, 0.99), 2) if gaps else None,
                 "overrun_pct": round(100 * len(over) / len(gaps), 3) if gaps else None},
        "t0": ts_sorted[0] if ts_sorted else None,
        "t1": ts_sorted[-1] if ts_sorted else None,
    }


def main():
    outroot = sys.argv[1]
    res = []
    for rid in sorted(os.listdir(outroot)):
        rd = os.path.join(outroot, rid)
        if os.path.isdir(rd) and os.path.exists(os.path.join(rd, "DONE")):
            try:
                res.append(analyze_run(rd, outroot))
            except Exception as e:
                res.append({"run_id": rid, "error": f"{type(e).__name__}: {e}"})
    cpulog = os.path.join(outroot, "s2_cpu.log")
    if os.path.exists(cpulog):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from s2_dryrun_analyze import cpu_join
        cpu_join(cpulog, [r for r in res if "error" not in r])
    json.dump(res, open(os.path.join(outroot, "s2_results.json"), "w"),
              ensure_ascii=False, indent=1)

    print(f"# STAGE2 자동 분석 ({outroot})\n")
    print("| run | T | during 위반% (c1s) | 발효A1 | 반응(발효기준)/완료 [s] | "
          "burst 위반/건 (search) | 잔여c1 (stale귀속) | 전환 dur/전체 | 오버런% | CPU% |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    by_arm = defaultdict(list)
    for r in res:
        if "error" in r:
            print(f"| {r['run_id']} | — | ★{r['error']} | | | | | |")
            continue
        ft = r["first_transition"]
        print(f"| {r['run_id']} | {r['T_s']} | {r['viol_pct']['during']} "
              f"({r['viol_pct']['during_c1_search']}) | "
              f"{r.get('effect_A1_rel_s')} | "
              f"{ft['react_from_effect_s']}/{ft['react_from_effect_total_s']} | "
              f"{r['burst']['viol']}/{r['burst']['n']} "
              f"(s {r['burst']['search_viol']}/{r['burst']['search_n']}) | "
              f"{r['residual_viol_c1']} ({r['residual_stale_attr']}) | "
              f"{r['switches']['during']}/{r['switches']['run_total']} | "
              f"{r['loop']['overrun_pct']} | {r.get('cpu_pct')} |")
        by_arm[r["T_s"]].append(r)

    print("\n## arm 요약 (평균±표본표준편차)\n")
    print("| T | during 위반%(전체) | during 위반%(c1 search) | 반응 p50(발효기준) [s] | burst 위반(search) | 전환(during) |")
    print("|---|---|---|---|---|---|")
    for T in sorted(by_arm, reverse=True):
        rs = by_arm[T]
        def ms(key):
            xs = [k for k in key(rs) if k is not None]
            if not xs:
                return "—"
            m = sum(xs) / len(xs)
            sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5 \
                if len(xs) > 1 else 0.0
            return f"{m:.3f}±{sd:.3f}"
        print(f"| {T} | {ms(lambda rs: [r['viol_pct']['during'] for r in rs])} | "
              f"{ms(lambda rs: [r['viol_pct']['during_c1_search'] for r in rs])} | "
              f"{pct([r['first_transition']['react_from_effect_s'] for r in rs if r['first_transition']['react_from_effect_s'] is not None], 0.5)} | "
              f"{ms(lambda rs: [float(r['burst']['search_viol']) for r in rs])} | "
              f"{ms(lambda rs: [float(r['switches']['during']) for r in rs])} |")
    print("\nP-S2 판정과 채택 권고는 STAGE2_REPORT.md 에서 (사람 게이트 G2).")


if __name__ == "__main__":
    main()

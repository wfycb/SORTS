#!/usr/bin/env python3
"""1단계 자동 분석 — 드라이버가 배치 종료 후 호출한다 (무인 경로).

A. 노드 장애 (배치 1·2): 복구 3지표 + 개선 귀속 분리
   - 재개 지연   = 해제 → S3 완료 트래픽 첫 관측 (s1_share_ts.csv, 1s 버킷)
   - 정상화 지연 = 해제 → 10 s 롤링 위반율이 pre 수준 복귀 후 10 s 유지
                   (pre 수준 = pre 평균 + 1×pre 표준편차 = mean·(1+CV).
                    사전 정의 — 사후 조정 금지)
   - 드레인 손실 = 해제 ~ 정상화 사이 위반 건수 (분자·분모 모두 기록)
   - 분리 (SORTS 런만): 차단 창의 (a) feasible_set 내 S3 포함률(decisions)
     vs (b) 실제 S3 도달 비중 vs (c) Envoy HC 이벤트(감지 시각) — "SORTS 는
     여전히 못 보고 Envoy 가 막는가"를 명시하기 위함.
B. 엣지 축 (배치 4): windows.both 위반율 (t2_policy_repeat 재사용 — 검증①
   6.50±0.85 와 같은 정의), S1 유입·f_c(S1) 엣지 과부하 확인.
C. 서버 축 (배치 3): t2 창 요약 + outlier ejection 스탯 스냅샷 병기.

출력: <outroot>/s1_results.json + stdout 마크다운 요약.
"""
import csv
import glob
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, "/home/user/exp/analysis/night-20260810")
import t2_policy_repeat as t2  # noqa: E402

SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
EXPECT = {"reserve": 36, "search": 4474, "recommend": 200}
B3_REF = (6.50, 0.85)          # taskB3 검증① (HC off)


def is_valid(r):
    if r["status"] != "200":
        return False
    e = EXPECT.get(r["ep"])
    if e is None:
        return False
    b = int(r["bytes_recv"])
    return abs(b - e) <= (e * 0.10 if e > 1000 else 0)


def iso_epoch(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def viol_series(rd, t_meas):
    """1 s 버킷 -> [n, viol] (전 코호트 합)."""
    buck = defaultdict(lambda: [0, 0])
    for c in (1, 2):
        p = os.path.join(rd, f"load_c{c}.csv")
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(open(p)):
            if r["warmup"] != "0":
                continue
            b = int(float(r["end_ts"]) - t_meas)
            bad = (not is_valid(r)) or float(r["corrected_ms"]) > SLO[r["ep"]]
            buck[b][0] += 1
            buck[b][1] += bad
    return dict(buck)


def s3_series(rd):
    out = {}
    p = os.path.join(rd, "s1_share_ts.csv")
    if not os.path.exists(p):
        return out
    for r in csv.DictReader(open(p)):
        out[int(r["t_rel_s"])] = int(r["n_S3"])
    return out


def recovery(rd, ev):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    t_meas = meta["t_meas"]
    d12 = meta["clock"]["d12_s"]
    blk = ev["block_ts"] + d12 - t_meas          # .40 -> .12 상대초
    unb = ev["unblock_ts"] + d12 - t_meas
    vs = viol_series(rd, t_meas)
    s3 = s3_series(rd)
    dur = meta["duration"]

    pre = [vs[b][1] / vs[b][0] for b in range(0, int(blk) - 2)
           if b in vs and vs[b][0]]
    pre_mean = sum(pre) / len(pre) if pre else 0.0
    pre_std = ((sum((x - pre_mean) ** 2 for x in pre) / len(pre)) ** 0.5
               if pre else 0.0)
    band = pre_mean + pre_std

    # 재개: 해제 시각을 포함하는 1 s 버킷부터 본다. 그 버킷은 해제 후 구간을
    # 일부 덮으므로 유효하며, 음수가 되지 않게 0 으로 자른다 (분해능 1 s).
    resume = next((b for b in sorted(s3) if b >= int(unb) and s3[b] > 0), None)
    resume_delay = (None if resume is None else max(0.0, round(resume - unb, 1)))

    def roll(b):
        xs = [(vs[t][0], vs[t][1]) for t in range(b - 9, b + 1) if t in vs]
        n = sum(x[0] for x in xs)
        return (sum(x[1] for x in xs) / n) if n else None

    norm = None
    b = int(unb) + 9
    while b <= max(vs) if vs else 0:
        r10 = roll(b)
        if r10 is not None and r10 <= band:
            if all((roll(t) or 0) <= band for t in range(b, min(b + 10, max(vs) + 1))):
                norm = b - 9            # 롤링 창의 시작이 아니라 판정 시각 보수화:
                break                   # 창 오른끝 b 기준 → 시작점 b-9 는 참고용
        b += 1
    hi = norm if norm is not None else (max(vs) + 1 if vs else int(unb))
    drain_n = sum(vs[t][0] for t in range(int(unb), hi) if t in vs)
    drain_v = sum(vs[t][1] for t in range(int(unb), hi) if t in vs)
    blk_n = sum(vs[t][0] for t in range(int(blk) + 1, int(unb)) if t in vs)
    blk_v = sum(vs[t][1] for t in range(int(blk) + 1, int(unb)) if t in vs)
    return {
        "run_id": meta["run_id"], "policy": meta["policy"],
        "block_rel_s": round(blk, 2), "unblock_rel_s": round(unb, 2),
        "pre_viol_mean": round(pre_mean, 5), "pre_viol_std": round(pre_std, 5),
        "band_def": "pre_mean + 1*pre_std, 10s 롤링, 10s 유지",
        "block_win": {"n": blk_n, "viol": blk_v,
                      "rate": round(blk_v / blk_n, 5) if blk_n else None},
        "resume_delay_s": resume_delay,
        "normalize_delay_s": (None if norm is None else round(norm - unb, 1)),
        "normalized": norm is not None,
        "drain": {"n": drain_n, "viol": drain_v,
                  "rate": round(drain_v / drain_n, 5) if drain_n else None},
    }


def separation(rd, ev, hc_lines):
    """차단 창의 feasible S3 포함률 vs 실 S3 도달 vs HC 이벤트 + 메커니즘 분해.

    decisions.csv 가 없는 비교군 런에서도 HC 이벤트·처리량·클러스터 분해는
    낸다 (feasible 관련 항목만 None)."""
    meta = json.load(open(os.path.join(rd, "meta.json")))
    t_meas, d12, d43 = (meta["t_meas"], meta["clock"]["d12_s"],
                        meta["clock"]["d43_s"])
    dec = os.path.join(rd, "decisions.csv")
    blk = ev["block_ts"] + d12 - t_meas
    unb = ev["unblock_ts"] + d12 - t_meas
    inc = tot = 0
    cap_state = defaultdict(int)
    leff = []
    if os.path.exists(dec):
        for r in csv.DictReader(open(dec)):
            t = float(r["ts"]) - d43 + d12 - t_meas
            if blk + 2 <= t < unb:
                tot += 1
                inc += "S3" in (r["feasible_set"] or "")
                cap_state[(r["feasible_set"], r["blocked_by"],
                           r["soft_applied"])] += 1
                try:
                    leff.append((float(r["l_eff_s2"]), float(r["l_eff_s3"])))
                except (KeyError, ValueError):
                    pass
    s3 = s3_series(rd)
    hm_tot = hm_s3 = 0
    for b in range(int(blk) + 1, int(unb)):
        if b in s3:
            hm_s3 += s3[b]
    # 전체 도달수는 s1_share_ts 의 합으로
    tot_arr = 0
    p = os.path.join(rd, "s1_share_ts.csv")
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            b = int(r["t_rel_s"])
            if int(blk) + 1 <= b < int(unb):
                tot_arr += (int(r["n_S1"]) + int(r["n_S2"]) + int(r["n_S3"]))
    # HC 이벤트: .40 호스트 관련, 차단 -5 s ~ 해제 +90 s
    ev_out = []
    for e in hc_lines:
        if e.get("host", {}).get("socket_address", {}).get("address") != "192.168.0.40":
            continue
        ts = iso_epoch(e["timestamp"])              # UTC epoch (.43 시계)
        t = ts - d43 + d12 - t_meas
        if blk - 5 <= t <= unb + 90:
            typ = next((k for k in e if k.endswith("_event")), "?")
            ev_out.append({"t_rel": round(t, 1), "cluster": e.get("cluster_name"),
                           "event": typ,
                           "rel_to_block_s": round(t - blk, 1)})
    ev_out.sort(key=lambda x: x["t_rel"])
    # ★"healthy" 부분문자열 검사는 쓰면 안 된다 — eject_unhealthy_event 에도
    # 들어 있다 (dry-run 에서 전 런 None 으로 드러남). 이벤트명을 명시 검사한다.
    first_fail = next((e["t_rel"] for e in ev_out
                       if e["event"] == "eject_unhealthy_event"), None)
    det = None if first_fail is None else first_fail - blk

    # 메커니즘 분해: 차단 창의 (클러스터, 코드) x HC 감지 전/후 + 처리량.
    # "HC 가 막았는가"와 "HC 가 막을 수 없는 경로가 남았는가"를 가른다 —
    # 단일 엔드포인트 클러스터(site_s*)는 전원 unhealthy 시 panic 이라
    # HC 로 구제되지 않는다 (Envoy 문서: panic 시 health 무시).
    pre_c, post_c = defaultdict(int), defaultdict(int)
    n_win = 0
    gz = os.path.join(rd, "envoy_access.log.gz")
    if os.path.exists(gz):
        with gzip.open(gz, "rt", errors="replace") as f:
            for line in f:
                p = line.split(",")
                if len(p) < 18:
                    continue
                try:
                    t = float(p[0]) - d43 + d12 - t_meas
                except ValueError:
                    continue
                if not (blk + 2 <= t < unb):
                    continue
                n_win += 1
                if p[4] == "200":
                    continue
                key = f"{p[9]}|{p[4]}|{p[5]}"
                if det is not None and (t - blk) >= det:
                    post_c[key] += 1
                else:
                    pre_c[key] += 1
    # 처리량·지연 (차단 창): 붕괴 여부의 직접 지표
    corr, n_all, n_bad = [], 0, 0
    for c in (1, 2):
        pth = os.path.join(rd, f"load_c{c}.csv")
        if not os.path.exists(pth):
            continue
        for r in csv.DictReader(open(pth)):
            if r["warmup"] != "0":
                continue
            t = float(r["end_ts"]) - t_meas
            if not (blk + 2 <= t < unb):
                continue
            n_all += 1
            if r["status"] != "200":
                n_bad += 1
            else:
                corr.append(float(r["corrected_ms"]))
    corr.sort()
    win_s = max(unb - (blk + 2), 1e-9)
    mech = {
        "block_win_requests": n_all,
        "block_win_throughput_rps": round(n_all / win_s, 1),
        "block_win_non200_pct": round(100 * n_bad / n_all, 2) if n_all else None,
        "block_win_corrected_p50_ms": (round(corr[len(corr) // 2], 1)
                                       if corr else None),
        "fail_by_cluster_before_hc_detect": dict(pre_c),
        "fail_by_cluster_after_hc_detect": dict(post_c),
        "decision_states": {f"{k[0]}|blocked={k[1] or '-'}|soft={k[2]}": v
                            for k, v in sorted(cap_state.items(),
                                               key=lambda kv: -kv[1])},
        "planned_l_eff_mean": ({"S2": round(sum(x[0] for x in leff) / len(leff), 1),
                                "S3": round(sum(x[1] for x in leff) / len(leff), 1)}
                               if leff else None),
    }
    return {
        "mechanism": mech,
        "s3_in_feasible_frac_blockwin": round(inc / tot, 4) if tot else None,
        "actual_s3_arrivals_blockwin": hm_s3,
        "total_arrivals_blockwin": tot_arr,
        "actual_s3_share_blockwin": round(hm_s3 / tot_arr, 5) if tot_arr else None,
        "hc_first_fail_rel_after_block_s":
            (None if first_fail is None else round(first_fail - blk, 1)),
        "hc_events": ev_out[:40],
    }


def load_hc_lines(path):
    out = []
    if path and os.path.exists(path):
        for ln in open(path, errors="replace"):
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
    return out


def edge_run(rd):
    """엣지 축: t2(검증① both 창과 동일 정의) + 러너 during 요약."""
    r = t2.one_run(rd)
    w = r["windows"]["both"]
    meta = json.load(open(os.path.join(rd, "meta.json")))
    summ = json.load(open(os.path.join(rd, "summary.json")))
    dsec = summ["sections"].get("during", {})
    return {"run_id": r["run_id"], "policy": meta["policy"],
            "suspect": r["suspect"],
            "both_viol_pct": round(w["viol_pct"], 3),
            "during_s1_share": dsec.get("s1_share"),
            "during_s1_share_rps": dsec.get("s1_share_rps"),
            "during_s1_knee_ratio": dsec.get("s1_knee_ratio"),
            "during_fc_s1": dsec.get("fc_ms", {}).get("S1")}


def server_run(rd):
    """서버 축: 러너 summary.json 의 pre/during/post (disturb=server 절단).

    t2.windows 는 seq_extreme 마크 전용이라 여기 쓰면 안 된다 (KeyError).
    총 위반율 = Σ n·rate / Σ n (코호트별 요약에서 재구성)."""
    meta = json.load(open(os.path.join(rd, "meta.json")))
    summ = json.load(open(os.path.join(rd, "summary.json")))
    res = {"run_id": meta["run_id"], "policy": meta["policy"],
           "suspect": os.path.exists(os.path.join(rd, "SUSPECT"))}
    for sec in ("pre", "during", "post"):
        d = summ["sections"].get(sec, {})
        by = d.get("by_cohort") or {}
        n = sum(v["n"] for v in by.values())
        vi = sum(v["n"] * v["slo_violation_rate"] for v in by.values())
        res[sec] = {"n": n, "viol_pct": round(100 * vi / n, 3) if n else None,
                    "site_share": d.get("site_share"),
                    "fc_s3": d.get("fc_ms", {}).get("S3")}
    return res


def main():
    outroot = sys.argv[1]
    res = {"outroot": outroot}

    # A. 노드 장애 배치들
    for batch, evp in (("nf-repro", os.path.join(outroot, "nf-repro", "nf_events.json")),
                       ("nf-hc", os.path.join(outroot, "nf-hc", "nf_events.json"))):
        bdir = os.path.join(outroot, batch)
        if not os.path.isdir(bdir):
            continue
        evs = [e for e in (json.load(open(evp)) if os.path.exists(evp) else [])
               if e.get("idx") != "final"]
        hc_lines = load_hc_lines(os.path.join(bdir, "hc_events_slice.log"))
        rds = sorted(d for d in glob.glob(os.path.join(bdir, "s1nf_*"))
                     if os.path.exists(os.path.join(d, "DONE")))
        out = []
        for rd in rds:
            # ★런↔사건 짝짓기는 t_meas 로 한다. 디렉터리 정렬순은 실행 순서와
            # 다르다 (교대 배열: hc_1, lr_1, hc_2, ... vs 정렬 hc_1..3, lr_1..3)
            # — 인덱스로 짝지으면 엉뚱한 차단 창을 보게 된다 (dry-run 에서 실측).
            tm = json.load(open(os.path.join(rd, "meta.json")))["t_meas"]
            ev = min(evs, key=lambda e: abs(e["t_meas_12"] - tm)) if evs else None
            if ev is None or abs(ev["t_meas_12"] - tm) > 5.0:
                out.append({"run_id": os.path.basename(rd),
                            "error": f"events.json 에 대응 사건 없음 "
                                     f"(t_meas={tm:.1f})"})
                continue
            rec = recovery(rd, ev)
            rec["event_t_meas_gap_s"] = round(ev["t_meas_12"] - tm, 3)
            rec["separation"] = separation(rd, ev, hc_lines)
            out.append(rec)
        res[batch] = out

    # B/C. 서버·엣지 배치
    for batch, pat, fn in (("server", "s1*_server*", server_run),
                           ("edge", "s1*edge*", edge_run)):
        bdir = os.path.join(outroot, batch)
        if not os.path.isdir(bdir):
            continue
        out = []
        for rd in sorted(glob.glob(os.path.join(bdir, pat))):
            if not os.path.exists(os.path.join(rd, "DONE")):
                continue
            try:
                out.append(fn(rd))
            except Exception as e:
                out.append({"run_id": os.path.basename(rd),
                            "error": f"{type(e).__name__}: {e}"})
        res[batch] = out

    # 브리지 판정
    br = next((r for r in res.get("edge", [])
               if r.get("run_id") == "s1edge_sorts_hc_1" and "both_viol_pct" in r),
              None)
    if br:
        lo, hi = B3_REF[0] - B3_REF[1], B3_REF[0] + B3_REF[1]
        res["bridge_check"] = {
            "value": br["both_viol_pct"], "ref": f"{B3_REF[0]}±{B3_REF[1]} (HC off)",
            "within": lo <= br["both_viol_pct"] <= hi}

    jp = os.path.join(outroot, "s1_results.json")
    json.dump(res, open(jp, "w"), ensure_ascii=False, indent=1)
    print(f"-> {jp}")
    # 요약 (마크다운)
    for batch in ("nf-repro", "nf-hc"):
        for r in res.get(batch, []):
            if "error" in r:
                print(f"- {batch}/{r['run_id']}: ★{r['error']}")
                continue
            s = r.get("separation") or {}
            print(f"- {batch}/{r['run_id']} ({r['policy']}): 차단창 위반 "
                  f"{(r['block_win']['rate'] or 0) * 100:.1f}% | 재개 "
                  f"{r['resume_delay_s']}s | 정상화 {r['normalize_delay_s']}s"
                  f"{'' if r['normalized'] else '(미달성)'} | 드레인 손실 "
                  f"{r['drain']['viol']}/{r['drain']['n']}"
                  + (f" | S3∈feasible {s.get('s3_in_feasible_frac_blockwin')}"
                     f" vs 실도달 {s.get('actual_s3_share_blockwin')}"
                     f" | HC감지 +{s.get('hc_first_fail_rel_after_block_s')}s"
                     if s else ""))
    for r in res.get("server", []):
        if "error" in r:
            print(f"- server/{r['run_id']}: ★{r['error']}")
        else:
            print(f"- server/{r['run_id']} ({r['policy']}): 위반% "
                  f"pre {r['pre']['viol_pct']} / during {r['during']['viol_pct']}"
                  f" / post {r['post']['viol_pct']} | during 분배 "
                  f"{r['during']['site_share']}")
    for r in res.get("edge", []):
        if "error" in r:
            print(f"- edge/{r['run_id']}: ★{r['error']}")
        else:
            print(f"- edge/{r['run_id']} ({r['policy']}): both 위반 "
                  f"{r['both_viol_pct']}% | S1 share {r['during_s1_share']}"
                  f" | knee비 {r['during_s1_knee_ratio']}")
    if "bridge_check" in res:
        b = res["bridge_check"]
        print(f"- 브리지: {b['value']}% vs {b['ref']} → "
              f"{'무영향 확인' if b['within'] else '★범위 밖 — HC 영향 의심'}")

    # 무인 경로: 보고서까지 자동 작성 (터미널·세션 생존과 무관)
    try:
        import s1_report
        print("보고서 ->", s1_report.write(res, outroot))
    except Exception as e:
        print(f"★보고서 작성 실패: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()

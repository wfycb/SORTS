#!/usr/bin/env python3
"""STAGE2 §4-1 판정: tc 실제 적용 시각 vs 컨트롤러 감지 시각.

전 시각을 **.43 현지 시계**로 통일한다 (폴러·컨트롤러가 .43 에서 돌기 때문).
  - 러너 마크: t_issue(.40) + d43 -> .43,  t43_done 은 이미 .43
  - 부하 기록(.12): end_ts - d12 + d43 -> .43

산출:
  T0 = 주입 지시(t_issue)               [기준 0]
  A1 = netem leaf 첫 가시화             (컨트롤러가 '밴드를 보는' 최초 시점)
  A64= netem leaf 64개 완비
  F1 = u32 필터 첫 부착                 (트래픽이 셰이핑 leaf 로 분류 시작)
  F64= u32 필터 64개 완비               (전 버킷 발효)
  R  = ssh 반환 (t43_done)
  D  = 컨트롤러 감지 tick (observed_rate 최초 비공란)
  X  = 트래픽 발효 실측 (c1 search service_ms 가 pre 대비 급등한 첫 100ms 버킷)

판정: D < F1 이면 **감지가 물리적 발효보다 앞선다** = 지시값을 읽는 것.
"""
import csv
import json
import os
import sys


def pctl(xs, q=0.5):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def main():
    root = sys.argv[1]
    rd = [os.path.join(root, d) for d in sorted(os.listdir(root))
          if os.path.isdir(os.path.join(root, d))][0]
    meta = json.load(open(os.path.join(rd, "meta.json")))
    d43 = meta["clock"]["d43_s"]
    d12 = meta["clock"]["d12_s"]
    mk = [m for m in meta["marks"] if m.get("phase") == "start"][0]
    T0 = mk["t_issue"] + d43            # .43 시계
    R = mk["t43_done"]

    # 폴러
    A1 = A64 = F1 = F64 = None
    rows = list(csv.DictReader(open(os.path.join(root, "tcpoll.csv"))))
    for r in rows:
        try:
            t = float(r["ts"]); n = int(r["n_netem_c1"]); nf = int(r["n_filter_c1"])
        except (ValueError, TypeError):
            continue
        if n >= 1 and A1 is None:
            A1 = t
        if n >= 64 and A64 is None:
            A64 = t
        if nf >= 1 and F1 is None:
            F1 = t
        if nf >= 64 and F64 is None:
            F64 = t
    poll_span = (float(rows[0]["ts"]), float(rows[-1]["ts"])) if rows else (0, 0)
    gaps = [float(b["ts"]) - float(a["ts"]) for a, b in zip(rows, rows[1:])]

    # 컨트롤러 감지
    D = None
    for r in csv.DictReader(open(os.path.join(rd, "decisions.csv"))):
        if (r["cohort"] == "c1" and r["class"] == "search"
                and r["observed_rate_kbit"] and float(r["ts"]) >= T0 - 2):
            D = float(r["ts"])
            break

    # 트래픽 발효: (a) 100 ms 버킷 p50 (거친 눈), (b) **요청 단위**(≈7.5 ms 간격)
    # — 첫 셰이핑 요청과 직전 비셰이핑 요청으로 발효 시각을 브래킷한다.
    buckets = {}
    pre = []
    reqs = []
    for r in csv.DictReader(open(os.path.join(rd, "load_c1.csv"))):
        if r["ep"] != "search" or r["status"] != "200":
            continue
        t43 = float(r["end_ts"]) - d12 + d43
        ms = float(r["service_ms"])
        if T0 - 10 <= t43 < T0 - 0.5:
            pre.append(ms)
        if T0 - 1 <= t43 < T0 + 6:
            buckets.setdefault(int((t43 - T0) * 10), []).append(ms)
            reqs.append((t43, ms))
    pre_p50 = pctl(pre) or 0.0
    thr = pre_p50 + 8.0
    X = None
    for b in sorted(buckets):
        if b < 0:
            continue
        p = pctl(buckets[b])
        if p is not None and p > thr:
            X = T0 + b / 10.0
            break
    # 요청 단위 브래킷: 처음으로 "이후 3건 연속 셰이핑"이 성립하는 요청
    reqs.sort()
    X_req = X_prev = None
    for i, (t, ms) in enumerate(reqs):
        if t < T0 or ms <= thr:
            continue
        nxt = [m for _, m in reqs[i:i + 3]]
        if len(nxt) == 3 and all(m > thr for m in nxt):
            X_req = t
            X_prev = max([tt for tt, mm in reqs if tt < t and mm <= thr], default=None)
            break

    def rel(x):
        return None if x is None else round(x - T0, 3)

    res = {"run": os.path.basename(rd), "T0_t43": round(T0, 3),
           "rel_s": {"A1_netem_first": rel(A1), "A64_netem_full": rel(A64),
                     "F1_filter_first": rel(F1), "F64_filter_full": rel(F64),
                     "R_ssh_return": rel(R), "D_detect": rel(D),
                     "X_traffic_effect_100ms": rel(X),
                     "X_req_first_shaped": rel(X_req),
                     "X_req_last_unshaped": rel(X_prev)},
           "gap_ms": {
               "A1_to_Xreq (가시화->발효)":
                   None if (A1 is None or X_req is None)
                   else round(1000 * (X_req - A1), 1),
               "A1_to_A64 (leaf 스윕 폭)":
                   None if (A1 is None or A64 is None)
                   else round(1000 * (A64 - A1), 1)},
           "poller": {"n": len(rows), "span_rel_s": [round(poll_span[0] - T0, 2),
                                                     round(poll_span[1] - T0, 2)],
                      "gap_ms_p50": round(1000 * pctl(gaps), 1) if gaps else None,
                      "gap_ms_max": round(1000 * max(gaps), 1) if gaps else None},
           "pre_service_p50_ms": round(pre_p50, 2),
           "bucket_p50_ms": {f"{b/10:+.1f}": round(pctl(v), 1)
                             for b, v in sorted(buckets.items())}}

    verdicts = []
    if D is not None and F1 is not None:
        verdicts.append(("D<F1 (감지가 필터 부착보다 앞섬)", D < F1))
    if D is not None and F64 is not None:
        verdicts.append(("D<F64 (감지가 전 버킷 발효보다 앞섬)", D < F64))
    if D is not None and X is not None:
        verdicts.append(("D<X (감지가 트래픽 발효 실측보다 앞섬)", D < X))
    if D is not None and A1 is not None:
        verdicts.append(("D>=A1 (감지가 leaf 가시화 이후 — 구조상 참이어야)", D >= A1))
    if D is not None and X_req is not None:
        verdicts.append(("D<X_req (감지가 요청단위 발효보다 앞섬)", D < X_req))
    if F1 is not None and A1 is not None:
        verdicts.append(("F1<=A1 (v3 목표: 필터가 netem 보다 먼저)", F1 <= A1))
    if A1 is not None and X_req is not None:
        verdicts.append(("|A1-X_req| < 25ms (§3-4 판정 기준)",
                         abs(X_req - A1) * 1000 < 25.0))
    res["verdicts"] = {k: v for k, v in verdicts}
    json.dump(res, open(os.path.join(root, "inject_probe_result.json"), "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

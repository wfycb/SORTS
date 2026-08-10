#!/usr/bin/env python3
"""작업 B2 단계 0b: 일중 드리프트 진단 (기존 데이터, read-only, 런 없음).

관측된 드리프트: far_tier+off both 창 위반율 야간 70.54±0.72(n=4) → 저녁
74.9(n=2), +4.4%p. 이것이 (a) 단조 요인(데이터 누적 등)인지 (b) 열/시간대
요인인지 (c) 랜덤(배치 간 재현 분산)인지 가른다.

증거 축 (사전 등록 §F):
 1. 런 순번/벽시계 vs 위반율 — 배치 **내** 추세와 배치 **간** 점프 분리.
 2. reserve_reset 로그의 drop 직전 문서 수 — 누적 요인 직접 계측
    (배치 로그 grep — 러너가 매 런 출력).
 3. 매치 조건 교차 비교: 동일 arm(far+off/strict+off/모든 창)의 야간 vs 저녁,
    pre 창(클린 800, 밴드 전)의 site_class fc_p95 — 밴드 창 요인과 기저 요인 분리.
 4. CPU 온도/주파수 — 과거 계측 없음(오늘 배치부터 thermal.json 수집 시작).
    가용 여부만 보고.
판정은 출력 하단에 사람이 읽는 요약으로 남기고, 수치는 JSON 으로 저장.
"""
import glob
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, "/home/user/exp/analysis/night-20260810")
import t2_policy_repeat as t2  # noqa: E402

OUT = "/home/user/exp/analysis/taskB2/drift_diag.json"


def arm_of(meta):
    if meta["policy"] != "sorts_reactive":
        return meta["policy"]
    eff = meta["arm"]["effective"]
    a = f"{eff['subset_policy']}+{'on' if eff.get('capacity_check') else 'off'}"
    if eff.get("soft_assign"):
        a += "+soft"
    return a


def collect_tb_runs():
    """seq_extreme(1600k×800) 런 전부: 야간 t2 + 작업B v1. done_ts + 창별 위반율."""
    rows = []
    for rd in (sorted(glob.glob("/home/user/exp/runs/night-20260810/t2/t2_*"))
               + sorted(glob.glob("/home/user/exp/runs/taskB-20260810/v1/v1_*"))):
        if not os.path.exists(os.path.join(rd, "DONE")):
            continue
        meta = json.load(open(os.path.join(rd, "meta.json")))
        if meta.get("disturb") != "seq_extreme":
            continue
        done_ts = float(open(os.path.join(rd, "DONE")).read().strip())
        try:
            r = t2.one_run(rd)
        except Exception as e:
            print(f"  {rd}: one_run 실패 {e}")
            continue
        w = r["windows"]
        rows.append({
            "run": os.path.basename(rd), "batch": rd.split("/")[-2],
            "arm": arm_of(meta), "done_ts": done_ts,
            "viol_both": w["both"]["viol_pct"],
            "viol_c1only": w["c1only"]["viol_pct"],
            "site_class_both": {k: {kk: v[kk] for kk in ("rps", "fc_p95",
                                                         "viol_pct")}
                                for k, v in w["both"]["site_class"].items()},
            "site_class_c1only": {k: {kk: v[kk] for kk in ("rps", "fc_p95",
                                                           "viol_pct")}
                                  for k, v in w["c1only"]["site_class"].items()},
        })
    rows.sort(key=lambda r: r["done_ts"])
    return rows


def doc_counts():
    """배치 로그의 reserve_reset 출력 — drop 직전 reservation 문서 수.

    러너 로그 줄 예: '  [reset] 192.168.0.3  문서수 = 12345  flush_all=OK'
    로그 파일 mtime 이 아니라 앞 [HH:MM:SS] 는 러너가 안 찍는 줄이라, 직전
    런 시작 로그와의 순서로만 쓴다 (해당 배치 내 런 순서 = 등장 순서)."""
    out = {}
    for lg in sorted(glob.glob("/home/user/exp/runs/*/*.log")):
        seq = []
        cur = None
        for line in open(lg, errors="replace"):
            m = re.search(r"\[(\d+)/(\d+)\] (\S+)", line)
            if m:
                cur = m.group(3)
            m = re.search(r"\[reset\] (\S+)\s+문서수 = (\d+)", line)
            if m and cur:
                seq.append({"run": cur, "ip": m.group(1),
                            "docs_before_drop": int(m.group(2))})
        if seq:
            out[os.path.relpath(lg, "/home/user/exp/runs")] = seq
    return out


def slope(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def main():
    rows = collect_tb_runs()
    print(f"seq_extreme 런 {len(rows)}개")
    print(f"{'run':>14s} {'arm':>13s} {'done(KST)':>9s} {'both%':>7s} "
          f"{'c1only%':>7s}  S1fc_p95(both search)")
    import time
    for r in rows:
        hh = time.strftime("%H:%M", time.localtime(r["done_ts"]))
        s1 = r["site_class_both"].get("S1_search", {})
        print(f"{r['run']:>14s} {r['arm']:>13s} {hh:>9s} {r['viol_both']:7.2f} "
              f"{r['viol_c1only']:7.2f}  fc={s1.get('fc_p95')} rps={s1.get('rps')}")

    # 1) 배치 내 순번 회귀 vs 배치 간 점프 (far+off / far+on)
    trends = {}
    for arm in sorted({r["arm"] for r in rows}):
        arm_rows = [r for r in rows if r["arm"] == arm]
        bybatch = defaultdict(list)
        for i, r in enumerate(arm_rows):
            bybatch[r["batch"]].append(r)
        ent = {}
        for b, rs in bybatch.items():
            xs = [r["done_ts"] for r in rs]
            ys = [r["viol_both"] for r in rs]
            ent[b] = {"n": len(rs), "viol": [round(y, 2) for y in ys],
                      "slope_pct_per_hr": (round(slope(xs, ys) * 3600, 3)
                                           if slope(xs, ys) is not None else None)}
        trends[arm] = ent

    # 2) pre 창 기저: c1only 창의 비관여 사이트(fc) — 시간별
    #    (pre 창 통계는 t2.windows 에 없으니 c1only 창의 c2 쪽 사이트 상태로 대용:
    #     c1only 창은 c2 무제한이라 S2/S3 는 정상 부하 — 기저 f_c 추이를 본다)
    baseline = [{"run": r["run"], "done_ts": r["done_ts"],
                 "fc_S2_search": r["site_class_c1only"].get("S2_search", {}).get("fc_p95"),
                 "fc_S3_search": r["site_class_c1only"].get("S3_search", {}).get("fc_p95"),
                 "fc_S1_search": r["site_class_c1only"].get("S1_search", {}).get("fc_p95")}
                for r in rows]

    # 3) 문서 수 누적
    docs = doc_counts()

    out = {"runs": rows, "trends": trends, "baseline_c1only": baseline,
           "doc_counts": docs}
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    print("\n== 배치 내 추세 (viol_both %/hr) ==")
    print(json.dumps(trends, ensure_ascii=False, indent=1))
    print("\n== c1only 창 기저 fc_p95 (S1/S2/S3 search) ==")
    for b in baseline:
        print(f"  {b['run']:>14s} S1={b['fc_S1_search']} S2={b['fc_S2_search']} "
              f"S3={b['fc_S3_search']}")
    print("\n== reservation 문서 수 (drop 직전, 배치 로그) ==")
    for lg, seq in docs.items():
        tail = seq[-6:]
        print(f"  {lg}: {len(seq)}건, 말미 {[(s['run'], s['ip'].split('.')[-1], s['docs_before_drop']) for s in tail]}")


if __name__ == "__main__":
    main()

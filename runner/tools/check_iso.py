#!/usr/bin/env python3
"""축소판 통과조건 6(교란 대상 격리)의 보조 판독 — 판정은 check_smoke.py 가 한다.

check_smoke.py 의 조건 6 은 "코호트1 search 만 오르고 나머지는 그대로"를 본다.
그런데 §0.3 밴드표는 세 클래스 **모두**에 d_acc 를 준다(Poor: reserve 0.92 /
search 17.06 / recommend 1.59 ms). 그래서 "변화 없음"은 문자 그대로는 성립할 수
없고, 각 클래스가 자기 기준값만큼 오르는 것이 정상이다. 여기서는 그 기준값
대비 배수를 같이 찍어 "의도한 대상에만, 의도한 크기로" 걸렸는지 보이게 한다.

같이 찍는 것: 구간별 사이트 분배. 밴드 적용 전후로 분배가 움직이면 Δp50 에
d_acc 가 아니라 d_net(2/10/25ms) 이동분이 섞인다 — 게이트(bl_lr)에서 소형
클래스 배수가 2.4~2.8배로 보인 원인이 정확히 이것이었다(diag_dacc.json:
정책 고정 시 0.97~1.31배).
"""
import json
import os
import sys

BATCH = sys.argv[1] if len(sys.argv) > 1 else "/home/user/exp/runs/smoke2-20260804"
REF_POOR = {"reserve": 0.92, "search": 17.06, "recommend": 1.59}
EPS = ("reserve", "search", "recommend")

man = json.load(open("/home/user/exp/manifest_smoke.json"))
for run in man["runs"]:
    if run["disturb"] != "radio":
        continue
    p = os.path.join(BATCH, run["run_id"], "summary.json")
    if not os.path.exists(p):
        print(f"{run['run_id']}: summary.json 없음")
        continue
    s = json.load(open(p))
    print(f"\n=== {s['run_id']}  정책={s['policy']} (교란: 코호트1 Poor 2300kbit) ===")
    for name in ("pre", "during", "post"):
        sec = s["sections"][name]
        sh = sec["site_share"]
        print(f"  [{name:>6s}] 분배 S1/S2/S3 = "
              f"{'/'.join(f'{100 * sh[k]:.1f}' for k in ('S1', 'S2', 'S3')) if sh else 'NA'}"
              f"   S1 {sec['s1_rps']}/s (무릎비 {sec['s1_knee_ratio']})")
    print(f"  {'코호트':>6s} {'ep':>10s} {'pre p50':>9s} {'during':>9s} "
          f"{'Δ':>8s} {'§0.3 ref':>9s} {'배수':>6s}")
    for c in ("1", "2"):
        for ep in EPS:
            pre = s["sections"]["pre"]["by_cohort"][c]["by_endpoint"][ep]["corrected_p50"]
            dur = s["sections"]["during"]["by_cohort"][c]["by_endpoint"][ep]["corrected_p50"]
            d = dur - pre
            r = REF_POOR[ep] if c == "1" else None   # 코호트2 는 교란 대상이 아님
            print(f"  {c:>6s} {ep:>10s} {pre:9.3f} {dur:9.3f} {d:8.3f} "
                  f"{r if r else '-  (무교란)':>9} "
                  f"{f'{d / r:6.3f}' if r else '     -'}")

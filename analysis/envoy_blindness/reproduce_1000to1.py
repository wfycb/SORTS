#!/usr/bin/env python3
"""Envoy "1000:1" 대표 수치의 정본 재산출 (야간배치2 과제 2).

주장: "무선이 22.541 ms 나빠지는 동안 front Envoy 가 관측한 지연 변화는
0.022 ms" (≈1025:1).

정의 (원 출처 presentation/factpack.md §1 = p4_recompute.py t5, 실측 재현):
- 데이터: N2 캘리브레이션 (.40:/home/user/n2/) — search 단독, 밴드 정적
  스윕, 라우팅 고정, status 200 만, n=1800/밴드.
- 접속측(무선 포함) = 부하생성기 e2e `service_ms` **p50** [ms].
- Envoy 측 = access log **필드16** (COMMON_DURATION DS_RX_BEG:DS_TX_END,
  µs → ms) **p50**. (%DURATION% 필드7은 정수 ms 라 전 밴드 4.0 — 변별 불가,
  µs 필드가 정밀값. I-계열 "Envoy DURATION 정수 ms" 함정.)
- 변화량 = 정상 20 Mbps → 극단 1.6 Mbps.
  e2e Δ = +22.563 / Envoy Δ = +0.022 / 무선 구간 Δ = 22.563−0.022 = +22.541.
- "1000:1" = 무선 구간 변화 22.541 vs Envoy 관측 변화 0.022 → 1024.6:1.

재현: python3 analysis/envoy_blindness/reproduce_1000to1.py
(요구 데이터: /home/user/n2/n2_{B_normal,B_extreme}_search{,_envoy}.csv)

보조: reproduce.py 는 phase4 본실험(radio 교란, 혼합 부하) 창 비교 —
혼합·반응 조건에선 평균 기준 46:1 수준으로 작아진다 (result.json).
정본 수치는 위 통제 캘리브 정의를 쓴다.
"""
import csv
import json
import statistics

N2 = "/home/user/n2"
OUT = {}
for tag, label in (("B_normal", "정상 20Mbps"), ("B_extreme", "극단 1.6Mbps")):
    e2e = sorted(float(r["service_ms"])
                 for r in csv.DictReader(open(f"{N2}/n2_{tag}_search.csv"))
                 if r["status"] == "200")
    dur_us = []
    for line in open(f"{N2}/n2_{tag}_search_envoy.csv"):
        f_ = line.rstrip("\n").split(",")
        if len(f_) >= 16 and f_[4] == "200" and f_[15].strip().isdigit():
            dur_us.append(int(f_[15]) / 1000.0)
    OUT[label] = {"n": len(e2e),
                  "e2e_p50": round(statistics.median(e2e), 3),
                  "envoy_us_p50": round(statistics.median(dur_us), 3)}
d_e2e = round(OUT["극단 1.6Mbps"]["e2e_p50"] - OUT["정상 20Mbps"]["e2e_p50"], 3)
d_env = round(OUT["극단 1.6Mbps"]["envoy_us_p50"]
              - OUT["정상 20Mbps"]["envoy_us_p50"], 3)
d_radio = round(d_e2e - d_env, 3)
OUT["delta"] = {"e2e": d_e2e, "envoy": d_env, "radio_segment": d_radio,
                "ratio": round(abs(d_radio / d_env), 1) if d_env else None,
                "claim": {"radio": 22.541, "envoy": 0.022},
                "reproduced": abs(d_radio - 22.541) < 0.001
                and abs(d_env - 0.022) < 0.001}
print(json.dumps(OUT, ensure_ascii=False, indent=1))
json.dump(OUT, open("/home/user/exp/analysis/envoy_blindness/"
                    "result_1000to1.json", "w"), ensure_ascii=False, indent=1)

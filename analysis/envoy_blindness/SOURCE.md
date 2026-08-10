# 출처 (analysis/envoy_blindness/)
"Envoy 1000:1" 대표 수치의 정본 재산출 (야간배치2 과제 2, 2026-08-11).
- 정의·재현: reproduce_1000to1.py (데이터 n2_data/ 동봉 — .40:/home/user/n2/ 원본)
- 원 출처: .40:~/exp/presentation/factpack.md §1 + tables/p4_t5_envoy_blind.csv
  + p4_recompute.py t5 (N2 캘리브, search 단독 밴드 스윕, p50 기준)
- 결과: 무선 구간 +22.541 ms vs Envoy(필드16 µs) +0.022 ms = 1024.6:1 —
  원 수치 정확 재현 (result_1000to1.json, reproduced=true)
- 보조: reproduce.py = phase4 본실험(혼합 부하·radio 교란) 창 비교 —
  평균 기준 ~46:1 (조건 다름, result.json)

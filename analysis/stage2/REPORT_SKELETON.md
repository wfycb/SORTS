# STAGE2_REPORT.md 구성 (G2 제출용 — 배치 종료 후 수치 채움)

1. 한 줄 결론 + 채택 권고(P-S2-2 규칙 적용 결과)
2. 사전 등록 대비 판정표: P-S2-0'(a/b/c) · P-S2-1a/b/c/d · P-S2-2 · P-S2-3
   — 각 항목 등록 시점(데이터 이전)과 근거 파일 명시
3. 주 지표 표 (arm × n=3): during 위반%(전체·**c1 search=판정 모집단**),
   첫 전환(발효 기준) p50/p95, burst 위반(search), 전환 횟수(during),
   오버런%, 컨트롤러 CPU%
4. 진동 진단(수정 없음, 보고만): c1:search 전환 수, 전환 간격 p50, 왕복 p50,
   S2 slack 0-교차 — P-S2-3(왕복 주기 T 비의존 1.8~2.2 s) 판정
5. 잔여 위반의 f_c 스테일 귀속 몫(진단; WINDOW_S 2.0 은 이번 범위 밖)
6. 주 지표 ① 정의 근거 명기(§2.2 승인): radio config 에 both 창 부재,
   전체 during 은 라우팅 무관 성분(recommend/reserve) 지배 → 판정은 c1 search,
   phase4 §6 표·P-S2-1 과 동일 모집단. **결과 보기 전 변경**.
7. flush 교락 서술: flush(52.6 ms)는 radio 축 트리거 경로에 **없다**
   (rate = tc 직접 읽기). f_c/바이트에만 관여 → 결정 내용에 영향.
   근거: 코드 경로 + 11런에서 "감지 tick == rate 최초 비공란 tick".
8. primitive 교체 이력과 그 영향(V3_PRIMITIVE_REPORT 요약 + 계열 비교 불가 범위)
9. 실패·SKIP·SUSPECT, 원복 상태, 산출물 경로
10. 3단계로 넘길 입력 (진동 완화 판단, STAGE5_INPUT 참조)

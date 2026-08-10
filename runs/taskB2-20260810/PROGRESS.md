# 작업 B 후속 (taskB2) 배치 2026-08-10 (시작 20:52 KST)

## 매트릭스와 예상
| 단계 | 내용 | 런수 | 예상 |
|---|---|---|---|
| 가드 | run_all §5 파일 잠금 가드 + 열/주파수 계측 | 0 | 20:55–21:10 |
| 0a | S3 조성 M0 2100/2500, M1 900/1450, M2 2400/3300 (48conn, t1c 4점 재사용) | 6 | ~21:55 |
| 0b | 일중 드리프트 진단 (기존 데이터 read-only, 0a 병행) | 0 | ~21:55 |
| 1 | soft assignment 구현 + selftest + 회귀 | 0 | ~22:40 |
| 2 | 검증① 재실행: far+on+soft_on×3 / far+on+soft_off×3 / strict+off×1 교대 | 7 | ~00:20 |
| 보고 | TASKB2_REPORT.md + ISSUES + 클린 복원 | – | ~01:00 |

## 사전 등록 (실행·구현 전 고정 — 결과 보고 바꾸지 않는다)

### A. soft assignment 목적함수 = (B) 위반 건수 최소화
근거: 프로젝트 주 지표가 SLO 위반율이고, 야간 §4 의 6~9% 추정("초과분만 이월")이
(B) 계열이다. (A) 초과량 최소화는 사후 분석으로만 덧붙인다. 주 판정은 (B).

### B. 배정 규칙 (등록)
적용 범위: `capacity_check=on` **그리고** `soft_assign=on` 이고, 해당 유닛이
EXPECTANT(feasible 공집합)일 때만. feasible 비공집합 경로는 바이트 단위 불변.
1. 후보 사이트 = 전 사이트(site_order). 용량 여유로 이월하려면 엣지만으로는 부족.
2. 여유 room_s = max(0, HEADROOM·C_s − planned_s) [search-등가 rps]. §3.3-2 그대로.
3. 유닛 등가 부하 r_u 를 **badness 오름차순** 그리디로 room 까지 충전.
   badness_s = max(0, −slack_s) [ms] (라이브 결정과 동일한 slack 입력).
   badness=0 동률은 **원거리 우선**(site_order 순) — far-first 철학 유지 + 소형
   클래스가 원거리 양슬랙으로 가서 S1 room 을 search 에 남긴다.
4. 전 room 소진 후 잔여(overflow)는 S1 — 기존 EXPECTANT 폴백과 동일 사이트.
5. 집합 표현 = 기존 weighted_clusters 런타임 가중치(site_s1/s2/s3 정수 %,
   합 100). 새 메커니즘 없음.
6. 클래스 우선순위: 명시적 순위 없음. tick 내 유닛 고정 순서(기존과 동일:
   c1→c2 × reserve→search→recommend) + 규칙 3 의 원거리 선호가 암묵 결정 —
   1600k 밴드에서 reserve/recommend 는 S3/S2 slack 양수라 S1 room 을 건드리지
   않고, search(원거리 slack 음수)만 S1 room 을 쓴다. d_acc 큰 클래스에 무위반
   room 을 주는 (B) 취지와 일치.
7. objective_value = Σ_s alloc_s·1[slack_s≤0] + overflow [등가 rps] — 이 tick
   배정의 예측 위반 등가 부하. decisions 에 기록.

### C. 검증① 재실행 판정 기준 3종 (§4.1 그대로)
1. soft on 위반율이 soft off 대비 유의하게 낮다 (차이 > 두 arm 반범위 합).
2. soft_assign_applied 가 기존 blocked_by=slack 창(108/108틱 상당)에서 실제 발동.
3. carry_over(배정 의도)와 실측 사이트별 도착 등가 부하가 일치.
셋 다 만족 = 성공. 6~9% 미달이어도 유의한 개선이면 성공으로 판정하고 차이를 분석.

### D. 해석 예측 (구현 전 기록 — 실측과 대조)
1. 개선은 유의할 것으로 기대하나 6~9% 도달은 불확실. 근거: S1 의 SLO-유효
   용량은 밴드 하 ~210-220 rps(capacity_knee.md — f_c p95 ≤ 16.4ms 요구)인데
   room 은 0.9·279=251 eq — room 까지 채우면 S1 search f_c 가 SLO 예산(13.4ms)을
   넘어 slack 이 음수化 → 다음 tick 배정이 S1 을 회피 → 채움↔회피 진동 가능.
   진동 시 위반은 search 창 일부에 국한되어 총위반 ~10-25% 수준 예상 범위.
   (room 정의는 §3.3-2 지시 그대로 두고, 이 예측이 맞으면 SLO-aware room 이
   후속 결정 사항이 된다.)
2. reserve/recommend 유닛은 v1 조건에서 EXPECTANT 가 아니므로(far_tier 정상
   경로) soft 발동은 search 유닛 108틱 창에 집중될 것.

### E. C(S3) 판정 기준 (단계 0 과 동일 절차)
S1 w(0.278/0.178) 고정 적합 잔차 상대 RMSE ≤ 1.0 (2×0.50), 브래킷 교집합
비공집합, C(S3) 점추정 + 부트스트랩 CI. t1c S3 4점(400/800/1200/1600,
48conn — 동일 동시성 프로파일) 재사용 + 신규 6점. 브래킷이 전혀 안 잡히면
정지·보고(§7). 붕괴점 미달 조성은 rps 상향 1회 재시도, 그래도 안 잡히면
"측정 범위 내 상한 없음" 기록.

### F. 드리프트 판정 기준
단조 판정 = (i) 런 순번(또는 벽시계) vs 위반율/f_c 회귀 기울기가 배치 내에서
일관 부호이고 (ii) 독립 계측(문서 수/온도/기저 f_c) 중 하나가 동반 추세.
아니면 랜덤 또는 미규명으로 기록. 규명 못 하면 못 했다고 쓴다.

### G. 드리프트 추가 사전 예측 (0b 분석 후, 검증① 재실행 전 등록)
0b 분해: far+off +4.4%p 는 전 사이트×클래스 셀에 분산됐는데 **svc_p95 는
야간·저녁 동일**(S2 rec 18.59↔18.60, S3 rec 28.23↔28.23) — 담체는
corrected−service 스케줄 밀림(커넥션 HOL)이고 원천은 포화 S1 search f_c p95
+3ms(61.8→64.8). 단조 요인 기각(07:10 런=04시 값, 배치 내 기울기 부호 혼재).
유력 후보 = S1 호스트(.3) 코테넌트 활동(야간 idle vs 저녁 사용 중; 현재 .3
"1 user"). **예측**: 오늘 밤(~23시) 재실행에서 far+on+soft_off 가 오후 74.2
보다 야간 70.5 쪽으로 내려가면 시간대(코테넌트) 요인 지지, 74~75 유지면 기각.

## 런 로그 (append)
### 20:52 백업 5종(run_all/sorts_ctl/tmpl/ISSUES) + PROGRESS 사전 등록 작성.
### 20:55 §5 가드(guard_snapshot/guard_check) + thermal.json 계측 + %SOFT_ASSIGN%
  렌더 경로 구현, 렌더-배포 스모크 통과. s0b(6런, S3 조성) 기동 — 가드 6파일 스냅샷 확인.
### 21:0x 0b 드리프트 진단(read-only, drift_diag.py→drift_diag.json): 위 §G 로 등록.
  reservation 문서수 이력은 러너가 reset 출력을 버려 부재 — 누적 가설은 설계상
  (매런 drop) 성립 어려움만 기록. 열/주파수 과거 계측 없음 — 오늘부터 수집.
### 21:25 단계0a 완료 6/6 DONE, SUSPECT 0. ★판정(사전 등록 §E 기준):
- 브래킷: M0 [759(t1c1600), 840.5(2100붕괴, 달성1774)] · M1 [726(900), 1013(1450붕괴)] ·
  M2 [853(2400 비붕괴!), 982(3300붕괴, 달성2764)] — **교집합 공집합 [852.8, 840.5]**.
- **w 공유 불성립 (S3)**: C-only 적합 불가, 자유 w 적합(west 격자)도 해 없음.
  M2(reserve-중) 2400 완주가 M0 붕괴 등가점을 넘음 = 소형 클래스가 S3 에서
  선형 w 예측보다 싸다 — capacity_knee.md 의 조성 의존 경고 실측 확인.
- 정지 조건(§2.1 "브래킷 전혀 안 잡힘") 아님 — 3/4 조성 붕괴 브래킷 확보. 진행.
- **채택값: C(S3|M0)=865 eq** (M0 계열 6점 조건부 적합, CI[795,865], rmse 0.069,
  analysis/taskB2/s3_c_m0.json). 검증①이 M0 조성이라 조건부 값이 실험에 유효 —
  **전 조성 단일 C(S3) 는 부재, 조성 바뀌면 그 방향 재측정 필요 (가정 명시)**.
- M0 붕괴 달성치 불일치(2100→1774 vs 2500→2093, 동일 조성) 기록 — 붕괴 하
  달성률은 하중 깊이에 민감, 브래킷은 min 사용.
### 21:2x tmpl c_eq[S3] 832→865 갱신(주석 포함) + 단계1 구현 착수.
### 21:3x 단계1 구현 완료 (사전 등록 §B 그대로):
- sorts_ctl: soft_alloc(badness 오름차순 그리디+room+원거리 우선+잔여 S1),
  apply_weights(기존 weighted_clusters 런타임 키 정수 %), run 루프 EXPECTANT
  분기(off 면 코드 경로 불변), DEC 7열 추가(soft_applied/carry_s1~s3/
  soft_overflow_eq/soft_objective_eq/soft_weights).
- selftest: 기존 전체 + soft 6케이스 **통과**.
- ★회귀(§3.5): obs_ctl_regress 구판=pre-taskB2(작업 B판) vs 신판 soft off —
  **96행 앞 12열 동일, 통과**. 정지점 아님 → 진행.
- Envoy 분수 가중치 라이브 확인: c1_search 70/30 설정·판독 OK, site_s3=100 원복.
- .43 배포: sorts_ctl scp + sorts.yaml 재렌더(c_eq S3 865), check_deploy 통과.
### 21:3x 검증① 재실행 기동 (v1s 7런: on,off,on,off,strict,on,off 교대).
### 21:41 런1 조기 점검: soft 발동 86행(search 유닛만 — 예측 D-2 적중), 가중치
  적용 확인(S2:100 / S1:9x|S2:x 혼재 = 예측 D-1 진동 징후), overflow 0, both 30.11%.
### 22:30 검증① 완료 7/7 DONE, SUSPECT 0. ★사전 등록 기준 (§C) 판정:
- ① **통과**: soft on 30.110/30.457/23.621 (28.06±3.42) vs off 74.452/76.089/73.250
  (74.60±1.42) — 차 46.5%p > 임계 4.8%p. off 는 작업 B 74.21 재현.
- ② **통과**: soft 79~81틱 발동 — off 런의 blocked_by=slack 108~110틱 창 정확히 그 자리
  (on 런 잔여 slack 54 + both 25~27).
- ③ **통과 (런1 편차 명기)**: 도착 기반 — on_2/on_3 의도 30/70/0.5 vs 실측 36/63/0.4
  (≤6%p), on_1 은 56/44/0.5 (25%p 편차, 진동 위상 잡음). 틱 단위 MAE 11.9%p(p90 19.4).
  S3≈0 전 런 일치. → **3기준 충족 = 성공** (6~9% 미달이나 유의 개선 규정 충족).
- 잔여 28% 구조: S2 이월 search viol 69%(slack −3.9 구조적) + S1 search 51%
  (fc95 45.3 — 채움 틱 과적재) + recommend 11/32%. S1 평균 적재 128 eq << room 251
  — S1-회피 duty ~2/3 (관측 slack 진동, 예측 D-1 적중).
- (A) 목적함수 사후 평가: on 932~2521 vs off 14720~18018 ms/s — 동일 방향 (주 판정은 B).
- ★드리프트 §G 예측 **기각**: soft off 밤에도 74.6 (74.2 유지, 70.5 회귀 없음),
  strict 98.1 (오후 95.0 대비 +3) — 시간대(코테넌트) 가설 불지지. 야간 t2 와의
  +4~4.5%p 격차는 **미규명 유지** (남은 후보: 야간 배치와 오늘 사이의 코드 경로
  차이(작업 B obs/sorts_ctl 개정), S1 호스트 상태 변화 — 판정 실험 = 야간 시간대
  동일 코드 재실행).
### 22:4x 클린 복원 검증: 기본 렌더(strict/off/off) 재배포 md5 일치, 컨트롤러·
  loadgen 잔재 0, ogstun 클린, 런타임 가중치 비정상 키 0, 컨테이너 72 재시작 0
  (Up 7~8d), stress stopped, 디스크 172GB. 배치 종료 — 총 13런 DONE, SUSPECT/FAILED 0.

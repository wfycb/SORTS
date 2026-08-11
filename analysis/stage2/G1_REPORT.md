# STAGE2 G1 보고 — S2-0 준비 완료 (2026-08-11)

게이트 G1: 정지·승인 대기. 아래 전 항목 완료, 본 배치(S2-1)는 승인 후에만 기동.

## 1. §0.2 정정 4건 (STAGE1_REPORT.md 반영 완료)

1. **드레인 손실 정의** → §4.1 신설. 89,670.3 = SORTS_HC arm **런별** 드레인
   위반 건수(94,341/87,345/87,325)의 **n=3 평균**(합계 아님). 카운트 대상
   4종(비-200 / `err:TimeoutError`(러너 타임아웃 5 s — 미회신도 행으로
   기록·포함) / 무효 바이트 / SLO 초과), 재계산 경로(load_c{1,2}.csv +
   nf_events.json + clock.d12_s, 구현 `s1_analyze.recovery()`), night2
   "67~75 % 미결"의 종결 경위(정상화 시각으로 창을 닫음), 의미론 주의
   (절대 스케줄 + 커넥션당 직렬 전송의 폐루프성 적체가 DROP(silent) 하에서
   백로그를 키움; REJECT(RST)는 다른 동역학 — 실측 검증: tb-load.py worker
   구조 확인) 전부 명기. 새 arm 추가 없음.
2. **배치 3(서버 축) 3-arm 완결표** → §5.1.1 신설. SORTS(const 1.680/1.389,
   both 0.940/0.811 — phase4 S1~S4, HC off·개정 전 코드), bl_rr 0.108,
   bl_lr 0.075→0.07(HC on 재현성 대조), bl_od 0.08/0.11/0.12. 전 수치 출처
   런 명시, 산식 단일화(Σ n·rate/Σ n, `server_run()` 동일). taskA 계열
   (far_tier+both 0.116 / bl_lr 0.158)은 **개정 후 코드·별도 계열**로 표에서
   분리 — bl_lr 조차 계열 간 0.075↔0.158 이동함을 적시.
3. **bl_loc_pri 런 조건** → §5.2 부기. taskB3 검증①(v1c_*)과 동일 부하
   (seq_extreme, 360 s, 16conn×25rps×양 코호트 800 rps)·동일 판정창
   (windows.both, t2_policy_repeat 재사용) — meta.json 대조로 확인.
4. **ISSUES.md** → I-6 갱신 블록 + I-13 상태 갱신에 **3차 발현** 추가
   (노드 장애 하 stale→낙관 프라이어 복귀로 feasible_set 1.000 유지,
   stage1 배치 2, n=3, HC-on 재확인).

## 2. §1-2 커밋 (완료)

- 커밋 **`d1ac509`**, 태그 **`stage1-complete`**, origin(main)·태그 push 완료,
  `git status` **클린**. 원격 HEAD == 로컬 HEAD 확인.
- 포함: docs/STAGE1_REPORT.md(정정 반영판)·docs/baselines.md·docs/FREEZE.md
  (해제 기록 1)·docs/ISSUES.md·controller/envoy/runner 1단계 수정분(비교군
  3종 + ctl_period_s)·stage1/night2 매니페스트 6종·analysis/stage1/·
  **presentation/**(factpack.md 단일 사본 위험 제거, SOURCE.md 부여)·
  envoy.yaml 배포본(.43 hc_off 원복본, md5 518cd1a5). runs/ 제외.
- 신규 폴더 비밀 스캔 0건. 배포본(.43) 4종 md5 == .40 원본 확인.
- 참고: iptables 태그 전환·stage2 신규 파일(매니페스트/드라이버/분석기)은
  이 커밋 이후 작업 — S2-4 재동결 커밋(stage2-freeze)에 포함 예정.

## 3. §1-1 iptables sorts-fault 태그 전환 (완료)

- FREEZE 6파일 대상 아님 확인(대상: sorts_ctl/obs/sorts.yaml.tmpl/run_all/
  gen_envoy_v10/envoy_keys — 수정 파일은 analysis 쪽 node_block.sh/
  node_unblock.sh/stage1_driver.py/s1_report.py).
- 차단 룰 삽입에 `-m comment --comment "sorts-fault"` 추가, 잔재 검사를
  `iptables-save | grep -c sorts-fault` 로 교체(구 `--dport 5000` grep 의
  Docker ACCEPT 과잉 매칭 영구 해소). unblock 은 신·구 룰 모두 멱등 제거.
  자동 해제 백스톱(독립 타이머)·curl 실증 규칙 유지.
- 기능 검증: 태그 룰 삽입→카운트 1→해제 스크립트→카운트 0 (실측).
- 함정 기록: 정지 경로의 `pkill -f` 가 ssh 원격 셸 자기 명령줄과 매칭돼
  자기를 죽이는 사고 2회 실측(경로 리터럴이 있는 한 `[.]` 트릭 무효) —
  CPU 샘플러 정지는 **pidfile 방식**으로 확정(드라이버 반영).

## 4. S2-0-3 주기 파라미터 실효 드라이런 (완료 — 전 arm 플래그 없음)

본실험 동일 부하(800 rps, 16conn×25rps×2코호트, b3 동결 arm), 교란 없음,
측정 30 s. 러너 정식 경로(렌더-배포·유효값 검증·회귀 가드) 사용:

| run | T | ticks | 루프 주기 p50/p95/p99/max [ms] | 오버런 % (>T×1.1) | apply 호출 | CPU % (1코어) |
|---|---|---|---|---|---|---|
| s2dry_t1000 | 1 s | 65 | 1000.0/1001.0/1003.0/1005.0 | **0.0** | 6 | 0.8* |
| s2dry_t50 | 50 ms | 1293 | 50.0/51.0/51.0/52.0 | **0.0** | 6 | 3.9 |
| s2dry_t25 | 25 ms | 2584 | 25.0/26.0/26.0/27.0 | **0.0** | 6 | 6.8 |

- 오버런(>T×1.1, 톨러런스 10 % 사전 고정) **전 arm 0 %**, 주기 히스토그램
  단봉·지터 ≤2 ms. CPU 포화 없음(단일 스레드, 25 ms 에서도 6.8 %).
  **제외/플래그 대상 arm 없음.**
- apply 호출은 arm 무관 6회(기동 정착 전환) — "apply 는 변경 시에만" 재확인
  (정상 상태 duty 0, T 를 40배 내려도 apply 비용 불변).
- \* t1000 CPU 는 샘플 7개(부분 커버 — 샘플러 자기-킬 사고 복구 직후).
  선형 외삽(6.8 %÷40≈0.17 %)과 상한이 일치해 포화 판정에는 영향 없음.
  본 배치는 전 구간 샘플링.
- 원자료: `runs/stage2-dryrun-20260811{,b}/s2_dryrun_results.json` (2회 실행
  — 1차는 CPU 샘플러 사고로 타이밍만, 2차 b 가 CPU 포함 정본. 타이밍 동일).

## 5. S2-0-4 교란 config 특정 (완료 — 재사용 확정)

- **산출 런 특정**: "첫 전환 1.08~1.14 s" = phase4-20260807 **A1/A3(+A2/A4)**
  radio 런. "84.6 % / 0.328 s" = demo-20260805 **D2_sorts_radio** (표4,
  `presentation/tables/p4_t4_residual.csv`). 두 계열 config **동일 확인**
  (meta.json 대조): disturb=radio(c1 Poor 2300 kbit), t+120~240 s,
  warmup 60 + duration 360, 16conn×25rps×2코호트 = 800 rps, 상시 무제한.
- **앵커 산식 원자료 재현**: 첫 전환 = c1:search 첫 `changed` tick(.43) −
  (`radio_on.t_issue` + d43) → A1 1.08 / A3 1.14 / A2 1.11 / A4 1.09 —
  공표치 정확 재현. **기준점은 교란 '지시' 시각**(ssh 램프 ~0.86 s 포함).
  적용 완료(t43_done) 기준으로는 0.22~0.28 s — P-S2-1 의 "0.2 s 미만"
  예측은 이 기준(하한 ≈ flush+T)과 정합하므로 **P-S2-0 은 t_issue 기준,
  P-S2-1 은 t43_done 기준**으로 사전 등록한다 (두 값 모두 보고).
- **재사용 경로**: `manifest_stage2.json` — 위 config 그대로 9런
  (t1000/t50/t25 × 3, arm 인터리브), 변경 변수는 ctl_period_s 뿐, arm 은
  b3 동결 기본(far_tier+both+capacity+soft+c_eff), 포트 고정 규칙 유지.
- **리스크 노트(신규 정보)**: 앵커 원출처는 개정 전 코드의 런이고, b3 동결
  코드로 radio 축을 도는 것은 이번이 처음이다(taskA 이후 전부 server/edge
  축). 첫 전환 트리거는 d_acc(연속 관측) 층이라 개정과 독립일 것으로
  예상하지만, 이탈 시 드라이버가 사전 등록대로 배치 SUSPECT·정지한다.
  §7 미니 런의 참고 관측치 참조.

## 6. S2-0-5 표준 precheck (전부 통과)

| 항목 | 결과 |
|---|---|
| `probe_buckets()` 실측 | **통과** — 미니 radio 런(`runs/stage2-probe-20260811/s2probe_radio_1`, DONE) 밴드 창 실측: 양 코호트 커넥션 16 = distinct_buckets 16, tc 코호트1 활성 버킷 16, 충돌 0 |
| `check_deploy()` md5 | **전부 일치** (.43 배포 4종 == .40 원본) |
| `envoy_keys.json` ↔ `/clusters` 대조 | **통과** (precheck 내장 검사; 클러스터 12, healthy EP 27/27) |
| 회귀 96행 | **통과** — off-arm 렌더에서 구판(20260811-stage1.bak) vs 신판 앞 12열 96행 동일 |
| 72 컨테이너 | 24/24/24 확인, Envoy ready LIVE |
| 잔재 검사(새 태그 방식) | `iptables-save \| grep -c sorts-fault` = **0** |
| 기타 | 경로 netem 3종 정상(0.3/14.8/24.8 ms), ogstun 클린, 부하/컨트롤러/stress 잔재 0, sudo -n 정상, 디스크 여유 170 GB |

precheck 후 sorts.yaml 은 휴지 기본 arm(strict/off/off/off, T=1.0)으로
재렌더·재배포했다 (md5 f29bb1b5 — .43 == .40).

## 7. ★미니 radio 런의 참고 관측 — 앵커·진동에 대한 사전 신호 (G1 판단 필요)

probe 런(§6)은 b3 동결 코드가 radio 축을 도는 **첫 사례**라 첫 전환도 참고
관측했다 (config 는 본실험과 부하·교란 스펙 동일, 단 warmup 15 s·교란
t+20~40 s 로 단축 — 앵커 판정용이 아니라 참고용):

1. **첫 전환 1.24 s (vs t_issue) / 0.38 s (vs t43_done)** — 앵커 대역
   1.08~1.14 밖(+0.10~0.16 s 지연). n=1 이고 워밍업이 짧아 확정은 아니나,
   본 배치 T=1 s 런에서 재현되면 사전 등록대로 **배치 SUSPECT·정지**된다.
2. **★c1:search S2↔S1 주기-2 왕복 13회/20 s** (본 창 120 s 환산 ~78회 —
   진동 가드레일 20회/런의 ~4배). 기전(decisions 실측): poor 하에서 S3 는
   slack −6 으로 제외(정상), **S2 slack 이 0 경계에 걸려**(+0.86 → −0.22 →
   −1.08 → +2.47 → −1.98 …) far_tier 집합이 {S2}↔{S1} 로 플립. capacity/
   soft 분기는 미발동(blocked 없음) — 관측 f_c(S2) 가 경계를 넘나드는 순수
   경계 진동이다. 왕복 주기 ~2 s = WINDOW_S 2.0 과 일치.
3. **대조**: phase4 A2/A4(개정 전 코드, est on, strict_far)는 같은 교란에서
   c1:search→S2 후 **고정**(전환 1회, shadow 4방향 완전 일치). 즉 이
   거동은 교란이 아니라 **코드 개정(far_tier 집합 + 관측 slack 산정)의
   radio 축 신규 발현**이다. I-9/I-13 계열(경계 진동)의 새 표면일 가능성 —
   본 배치가 돌면 T ablation 과 진동 주기의 상호작용(주기 단축 시 플립
   가속 여부)이 그대로 주 결과에 실린다.

**정지 판단**: 지시 §5-2(앵커 불일치)는 배치 중 정지 조건이지만, 사전
신호가 이미 있으므로 G1 에서 정면 보고하고 본 배치는 기동하지 않은 채
승인을 기다린다. 선택지는 §9.

## 8. 본 배치 준비 상태와 예상 소요

- 드라이버: `analysis/stage2/stage2_driver.py` (nohup 무인). 구현된 무인
  중단 조건: **P-S2-0 앵커**(t1000 런 DONE 즉시 검사, 2자리 반올림 비교,
  이탈 → 러너 정지 + BATCH_SUSPECT + 원복) / 오버런 폭주(런 전 반복 주기
  초과) / 연속 3런 실패 / 디스크 < 20 GB. 개별 런 실패는 러너가
  SKIPPED/SUSPECT 후 진행. 종료 시 cleanup_all + precheck + 자동 분석
  (`s2_analyze.py` → AUTO_RESULTS.md). 컨트롤러 CPU 는 전 구간 1 s 샘플링
  (pidfile 정지).
- 배치 중 편집 금지: guard_md5 잠금 유지(러너 기본 경로).
- **예상 소요**: 런당 ≈ 8.5 분(오버헤드 31 s + 부하 420 s + 휴지 60 s,
  드라이런 실측 기반) × 9런 ≈ **77 분** + 드라이버 전후처리 ≈ **약 1시간
  25분**.

## 9. G1 결정 요청 3건

### 9.0 앵커·진동 사전 신호 (§7) 처리

- (a) **그대로 기동** — 앵커 이탈이 본 config 에서 재현되면 드라이버가
  규정대로 1런 후 SUSPECT·정지 (손실 ~10분, 원인 자료는 남음). §7 신호가
  본 config(긴 워밍업·t+120 주입)에서 소멸할 가능성도 배제 못 함.
- (b) **앵커 판정 전용 T=1 s 단독 1런을 먼저** 돌려 앵커·진동을 확정하고
  그 결과로 재보고 (손실 동일 ~10분, 배치 전체를 걸지 않음). — 제안.
- (c) 앵커 대역 자체를 재정의 — **비추천** (사전 등록 사후 변경 금지).

### 9.1 주 지표 ① 정의 확정 (사전 등록 사후 변경 금지 대비)

P-S2-2 주 지표 ①은 "런 전체 both 창 위반율"인데, 특정된 교란 config(radio
축)에는 edge 축의 both 창이 없다. 또한 radio 축의 during 전체 위반율은
**라우팅 무관 성분이 지배**함을 원자료로 확인했다: A1 기준 c1 recommend
6.43 %·reserve 6.27 %(SLO 여유 부족 — phase4 기지 사실 "밴드로 못 지키는
클래스") vs **c1 search 0.31 %**(SORTS 가 실제로 움직이는 유닛). T ablation
의 효과(burst 감소 ~수십 건)는 전체 위반율에서는 소수점 둘째 자리에
묻힌다.

제안(승인 요청): 주 지표 ① = **during 창(120~240 s) 위반율을 (a) 전체와
(b) c1 search 로 병기**하고, **채택 규칙(겹치면 느린 주기)의 판정은 (b)**
로 한다 — (b)가 phase4 §6 표·표4·P-S2-1 예측(60 % burst 감소)과 같은
모집단이기 때문. (a)는 전 계열 보고 관례대로 함께 실린다. 승인 없이는
본 배치를 돌려도 판정을 내리지 않는다.

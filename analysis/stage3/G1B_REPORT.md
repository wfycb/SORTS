# STAGE3 G1b 중간 보고 — 인프라 확장·검증 완료, 본 배치 승인 대기 (2026-08-12 03:45)

G1 회신 §2.3 순서 이행: 해제 → 확장 → 재현런 → 드라이런·baseline. 전 게이트
통과. 본 배치(9런, 무인 ~80분)는 승인 후 기동한다.

## 1. 동결 해제 작업 (3파일, 코호트 열거 일반화 한정)

| 파일 | 수정 내용 | 검증 |
|---|---|---|
| `sorts.yaml.tmpl` | `cohorts:` 블록을 `%COHORTS%` 토큰으로 — 러너가 n_cohorts 렌더 (cN = N×0x1000 규약) | 렌더 재파싱 검증 추가 (`render_deploy_ctl`) |
| `gen_envoy_v10.py` | `UNITS` → `make_units(n_cohorts)`, `route_prefixes(n)`, `--n-cohorts`(기본 2 = 종전) | 6코호트 렌더: prefix 19·클러스터 12·**런타임 키 228** |
| `run_all.py` | `N_COHORTS`(manifest max)·`run_cohorts(run)` 도입, `for c in (1,2)` 5곳 일반화, `radio()` → `applyn` 규약, ogstun 파서 1..N | 회귀 **96행 동일** + 재현런(§3) |

결정 로직·`obs.py` **무접촉** (md5: sorts_ctl 97d63b83 / obs b9fac68d 불변).
백업 `*.20260812-stage3.bak` 3종.

- 작업 중 함정 1건 재확인: python str.replace 패치가 이스케이프 불일치로
  **조용히 미적용**되는 사례 2회(radio()·apply 블록) — 파일 미변경이라
  무해했고, 이후 patch 는 assert-매치 + 전 지점 grep 검증으로 회수.
  (method_cases C 계열 아님 — 결과 무영향, 작업 로그에만 기록.)

## 2. 비동결 확장

- **tb-radio2 v4** 설치(.43, v3 백업 보존): `applyn <ip1..ipN> <spec1..N>`
  **argv 방식** — sudo 가 env 를 스트립해 COHORT_IPS env 안이 조용히 실패함을
  실측하고 argv 로 확정. `apply`(C1_IP/C2_IP)는 v3 하위 호환 유지.
  classes→filters→netem 순서(가시화≡발효)·tc -batch 유지. 실측: c1-only
  0.02 s / 6코호트 전밴드 384 leaf **0.05 s**.
- **UE 6**: open5gs 가입자 4명 추가(imsi …13~16, .43 mongo — 총 6), .12 에
  `ue3~6.yaml`(기존 관례 그대로 수동 기동, systemd 신설 없음 — 기지 함정
  준수), uesimtun2~5 = 10.46.0.8~11. `tb-cohort-map` 6행 확장·재생성.
  **E2E 6/6: 각 UE 인터페이스에서 200 + 4474 B** (응답 바이트 판정).
  sunny(.3) 무접촉.
- **Envoy 6코호트 배포**: validate OK → 교체(md5 대조) → 재기동 → LIVE,
  클러스터 12·healthy EP **27/27**, runtime routing.* 키 **228**(19×12).
  백업 `/var/tmp/envoy.yaml.20260812-pre-stage3.bak`. 전 arm 이 **같은
  config**를 쓴다(2코호트 arm 에서 c3~c6 라우트는 XFF 불일치로 무트래픽 —
  비교가능성 확보).

## 3. 재현런 (해제 검증, 사전 등록 대역) — **통과**

`runs/stage3-repro-20260812/s3repro_t1000_1` (2코호트, stage2 T=1 s config):

| 항목 | 실측 | 등록 대역 | 판정 |
|---|---|---|---|
| c1 search during 위반율 | **0.643 %** | [0.618, 0.798] | 통과 |
| 반응(발효 기준) | **0.973 s** | [0.947, 0.979] | 통과 |
| 앵커 게이트 (A1 0.247·d_acc 17.118·changed 1) | OK | P-S2-0'' | 통과 |
| 플립(참고) | 72회·간격 p50 2.0 s | stage2 계열 68.7±2.9 | 일치 |

## 4. 드라이런 + baseline 등가성 ({2,4,6}, 무교란 800 rps) — **통과**

`runs/stage3-baseline-20260812` (3/3 DONE):

| arm | 위반%(전체) | 코호트별 최대 | 분배 S2/S3 | 달성 rps | **stale starved** |
|---|---|---|---|---|---|
| c2 | 0.019 | 0.025 | 0.585/0.415 | 799.9 | **0** |
| c4 | 0.019 | 0.025 | 0.580/0.420 | 800.05 | **0** |
| c6 | 0.006 | 0.013 | 0.575/0.425 | 800.1 | **0** |

- **P-S3-2 예비 통과**: arm 간 최대차 0.013 %p ≤ 0.1 %p.
- **P-S3-3 예비 통과 + §2.1 계산 검증**: "트래픽 있는데 n_min 미달" stale
  = **전 arm 0** — 표 B 예측(코호트 수 무관) 그대로. 비-obs tick 은 칸당
  1(기동 순간)뿐.
- f_c 등가: 전 칸 동등, 단 S2/search p50 이 7.3 → 7.7 → 8.6 ms 로 소폭
  상승(다른 칸은 ±0.7 ms 내). 관측 f_c 의 런간 변동 범위이며 위반율에
  반영되지 않음 — 본 배치 pre 창에서 재확인 예정.
- **`read_rates` 동작 범위 실측 (G1 회신 §2.1 요구)**: 6코호트 혼합 밴드
  (c1 2300/c3 1600/c5 4500/c6 1600, c2·c4 없음) 인가 상태에서
  `{0x1000: 2300, 0x3000: 1600, 0x5000: 4500, 0x6000: 1600}` — **코드
  무수정으로 정확 관측**. 무밴드 코호트는 종전대로 항목 없음(무제한 처리).

## 5. 본 배치 준비 상태

- `manifest_stage3.json`: 코호트 {2,4,6} × n=3 = 9런 인터리브, c1 고정
  열화(radio poor 2300 kbit, t+120~240, v4 primitive), 총 800 rps 고정
  (rps/conn 25 / 12.5 / 8.333 — tb-load float 지원 확인).
- 드라이버: stage2_driver 재사용(폴러 + 앵커 게이트 P-S2-0'' + CPU 샘플러 +
  표준 중단). **자동 분석만 s3_analyze 로 교체** 예정(승인 후 1줄 수정 —
  분석기는 작성·사전 검증 완료: P-S3-1~7 전 지표 + starved 분류).
- `PREREG_S3.md` 확정(P-S3-1~4 + **P-S3-5~7 결합 경로** + 재현 대역 + 앵커).
- 예상 소요: 본 배치 ~80분 + 분석·보고 ~30분.

## 6. 현재 테스트베드 상태

precheck 통과, ogstun 클린(fq_codel), iptables sorts-fault 잔재 0, UE 6
가동(테스트베드 유휴 시에도 유지 — nr-ue 상시 프로세스, 기존 2 UE 관례와
동일), 디스크 168 GB. **G1b 정지 — 본 배치 기동 승인 대기.**

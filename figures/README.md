# figures/ — 발표·논문용 그림

STAGE6-FIG. **F1(시계열) 1차본**. 각 그림은 ① 생성 스크립트 ② 입력 CSV
③ 산출물(PDF 벡터 + PNG 300 dpi)을 함께 둔다. 원자료 → CSV 단계(`f1_extract.py`)와
CSV → 그림 단계(`f1_plot.py`)가 분리돼 있어 **스타일 수정 시 원자료를 다시 훑지
않는다.**

## 재생성

```bash
cd ~/exp/figures
python3 f1_extract.py --load 450          # 원자료 → data/f1_L450_*.csv
python3 f1_extract.py --load 800
python3 f1_plot.py --load 450 --profile talk    # → talk/*.pdf|png (기본)
python3 f1_plot.py --load 450 --profile paper   # → paper/*.pdf|png (단일 컬럼)
python3 f1_plot.py --load 800 --profile talk    # F1b(포화 분리 패널) 포함
```

- `style.py` — 색·선 스타일·프로파일 **공통 모듈**(F2~F5도 이걸 쓴다).
  색맹 안전(Okabe–Ito) + 선 스타일 병용 → 흑백 출력에서도 구분된다.
  **정책 색 고정**: SORTS 파랑 실선 / bl_lr 주황 파선 / bl_loc_pri 초록 점선.
  **사이트 색 고정**: S1 주황빨강 / S2 하늘 / S3 자주.
- 프로파일: `talk`(16:9·큰 폰트·굵은 선, 기본) / `paper`(단일 컬럼 3.4 in).

## 산출물과 캡션 초안

공통 조건 — 원자료 `runs/stage5-20260812/`, 시나리오 `seq_extreme`
**1600 kbit**(c1 @120 s → 양 코호트 @180 s → 해제 @240 s), 2 코호트,
16 conn/코호트, **hc_off**, T = 1 s, 관측 직결(shim off). 시간 원점은 각 런의
본측정 시작(warmup 60 s 이후). 정책 3종은 **같은 배치·같은 매니페스트**에서
인터리브로 실행됐고 교란 시각 편차는 ≤ 0.31 s.

### F1 `F1_timeseries_L450.{pdf,png}` — 주 그림

> **Radio degradation → routing response (L = 450 rps).** 위에서부터 (1) 컨트롤러가
> 관측한 무선 밴드, (2) search 응답 지연(정책 3종, 1 s 버킷 p50 선 + p50…p95 음영,
> `service_ms` 기준), (3) SORTS 의 사이트 분배, (4) SORTS 의 slack.
> 밴드가 1600 kbit 로 떨어지는 순간 SORTS 의 엣지(S1) 몫이 **0 % → 22 %** 로
> 올라가고, 해제와 동시에 0 % 로 돌아간다. 4단이 그 이유다 — 밴드 하에서 S2·S3 의
> slack 이 **0 아래로** 내려가(각각 −4 / −13 ms) 원거리 사이트로는 서버가 아무리
> 빨라도 SLO 를 맞출 수 없다. slack = SLO − GB − d_net − f_c − d_acc.
> **4단은 SORTS 에만 존재한다** — 비교군은 이런 상태를 갖지 않는다(결손이 아니라
> 주장 그 자체).

### F1(요청 순번 축) `F1_timeseries_reqidx_L450.*`

> 같은 그림, x축을 **요청 순번**(완료 순서 누적)으로 바꾼 판. 부하가 일정해
> 시간축과 1:1 이지만, "몇 번째 요청부터 바뀌었나"로 읽고 싶을 때 쓴다.

### F1(요청 단위 산점도) `F1_timeseries_scatter_L450.*`

> 2단을 롤링 통계 대신 **요청 단위 산점도**(정책당 최대 6000점 균등 추출)로 그린
> 판. 분포의 꼬리를 그대로 보여주지만 밀도가 높아 발표에는 p50/p95 판을 권한다.

### F1c `F1c_sites_by_policy_L450.*` — 누가 실제로 움직이는가

> **Who actually moves?** 같은 교란에서 정책별 사이트 분배. SORTS 는 엣지 몫이
> **0 % → 22 %** 로 움직이고, `bl_lr` 은 **50 % → 51 %** 로 사실상 불변,
> `bl_loc_pri` 는 **100 % → 100 %** 로 애초에 엣지 고정이다. 무선 상태를 보지 않는
> 정책은 무선이 나빠져도 배정이 바뀌지 않는다는 것이 이 그림의 요지다.

### F1b `F1b_L800_saturated.*` — 부하 800 rps 보조

> **At L = 800 rps the locality-first baseline is broken before the radio degrades.**
> 위: SORTS 와 `bl_lr` 의 service 지연(선형 축). 아래: `bl_loc_pri` 를 **별도 패널**
> 로 분리(로그 축) — 교란 **이전부터** service p50 87 ms 로 포화이고, `corrected`
> (스케줄 기준) 는 65 초까지 벌어진다. 그 차이는 서버 지연이 아니라 **부하 생성기의
> 스케줄 백로그**(커넥션당 1 outstanding, I-19)다. 800 rps 는 4단계 누적표
> (6.50 ± 0.85 %)를 측정한 지점이기도 하다.

## 수치 일관성

그림에 적힌 수치는 `docs/NUMBERS.md` §5.5(stage5) 와 같은 원자료·같은 산식이다.
사이트 몫(L = 450, both 창): SORTS **0.216**, bl_lr **0.506**, bl_loc_pri **1.000**
— 그림의 22 % / 51 % / 100 % 와 일치. L = 800 `bl_loc_pri` 의 pre service p50
**86.8 ms**, corrected p50 **65,399 ms** 도 대장·`STAGE5_REPORT` §1 과 일치한다.

## 데이터 파일

| 파일 | 내용 |
|---|---|
| `data/f1_L{450,800}_latency.csv` | 1 s 버킷 × 정책 — service/corrected p50·p95·n·요청 순번 |
| `data/f1_L{450,800}_points.csv` | 요청 단위 산점도용 추출(정책당 ≤ 6000점) |
| `data/f1_L{450,800}_sites.csv` | 1 s 버킷 × 정책 — S1/S2/S3 완료 건수·비율 |
| `data/f1_L{450,800}_slack.csv` | SORTS tick × slack(S1/S2/S3)·관측 밴드·d_acc·결정 |
| `data/f1_L{450,800}_marks.csv` | 교란 시각(정책별 상대초) |

### F1 개정(G2 회신 반영, 2026-08-13)

- 2단 y축 **0~60 ms**(SLO 45 선과의 관계가 보이게). 잘린 꼬리는 패널 안에
  **화살표 + "bl_loc_pri p95 spikes to ~102 ms (clipped)"** 로 표시.
  **0~115 백업판** = `F1_timeseries_L450_full.*`.
- 1단 밴드를 **코호트별 2선**(c1 실선 @120 s, c2 파선 @180 s)으로 — 순차 열화
  구조가 그림에서 바로 읽힌다.
- **c2 slack 예비판** = `F1_timeseries_L450_slackc2.*`("다른 UE 는?" 질문 대비.
  발표 슬라이드에는 넣지 않음).
- F1c 각 패널에 **both 창 위반율**(SORTS 0.42 / bl_lr 7.41 / bl_loc_pri 4.16 %)
  병기 → F1c 단독으로도 "움직여서 뭐가 좋아졌나"가 선다.
- 발표 순서 권장: **F1c → F1 → (4단 확대)**. F1c 와 F1 은 항상 붙여서 낸다.
- **확대율**: `--scale 1.2 / 1.5` 로 재방출(`*_x1.2.*`, `*_x1.5.*`). 투사 환경에
  맞춰 미팅 직전에 다시 그릴 필요가 없게 미리 뽑아 뒀다.

## F2 `F2_budget_heatmap.*` — 밴드는 클래스를 가려서 때린다

> **The band hits classes selectively.** 칸 값은 `f_c` 예산 =
> SLO − GB − d_net − d_acc [ms]. **음수(테두리) = 서버가 아무리 빨라도 SLO 불가.**
> search(4474 B)만 밴드가 좁아질수록 예산이 무너져 **2.3 Mbit/s 에서 S3 −2.1**,
> **1.6 Mbit/s 에서 S3 −9.6 · S2 +0.4** 가 되고, reserve(36 B)·recommend(200 B)는
> 전 밴드에서 양수로 남는다. 야간 실측과 정합한다 — 4.5 Mbit/s 에서는 S3 search
> 예산이 +6.3 이라 **정책 변별 자체가 없다**(D2 조건 0.06 % 위반).
> 계산 전용(런 0): `analysis/stage6/budget_table.py`.

## F3 `F3_cumulative.*` — 각 층이 무엇을 벌어주는가

> **Cumulative decomposition (1600 kbit on both cohorts, 800 rps, hc_off).**
> strict_far **95.03 %**(n=1) → +far_tier/용량 **74.60 ± 1.42 %** → +soft
> assignment **28.06 ± 3.42 %** → +C_eff(밴드 인지) **6.50 ± 0.85 %**.
> 오차막대는 반범위, 첫 막대만 n=1. 값은 `t2_policy_repeat.one_run`(both 창 총
> 위반율)로 **재계산**해 뽑았다(`f3_f5_extract.py`) — 대장 §4 와 동일 산식.

## F4 `F4_gap_curve.*` — 우위가 있는 구간과 뒤집히는 구간

> **Where the advantage exists — and where it flips.** 위: 정책별 위반율(로그).
> 아래: 최선 비교군 − SORTS 격차. **+0.06 → +3.74 → +4.27 → −15.92 %p**.
> 무릎 아래에서는 차이가 없고, 무릎의 1.5~2 배에서 최대이며, **K비 2.33
> (L=1400)에서 부호가 반전**된다. 6.50 % 지점(=F3 를 측정한 점)을 곡선 위에
> 표시했다. L=1400 만 32 conn/코호트(생성기 성립 조건, I-19)이며 커넥션 대조
> 보정 후에도 부호는 불변(≈ −14.8 %p). K비 축 판 = `F4_gap_curve_kratio.*`.

## F5 `F5_envoy_blindness.*` — LB 는 무선 열화를 못 본다

> **A(헤드라인, 부하 중)**: 같은 밴드 열화에서 접속측 지연은 **+16.9 ms** 오르는데
> front Envoy 가 관측한 업스트림 지연 변화는 **0.366 ms** — **46 : 1**
> (phase4 `R1_rr_radio`, c1 search, 2300 k 단일 코호트, RR 고정, 800 rps, 평균차).
> **B(부기, 정적 스윕)**: N2 캘리브레이션의 search 단독 p50 비교에서는
> **+22.563 ms vs +0.022 ms = 1024.6 : 1**. **두 수는 측정이 달라 같은 문장에
> 병렬로 쓰지 않는다**(대장 §1 규칙) — 그래서 패널을 나누고 조건을 각 패널
> 아래에 적었다.

## F6 아키텍처 — `ARCH_SPEC.md` (그리기 명세, 파이썬 생성 아님)

노드·컨테이너 수, **관측 경로 2개**(`rate` ← ogstun tc / `f_c`·bytes ← front
Envoy access log)와 **제어 경로 1개**(허용 집합 → Envoy runtime key), 주기(관측
1 s·윈도 2 s, apply 는 변경 시에만), 그리고 **"SORTS 는 LB 를 대체하지 않고
제약한다"** 를 어떻게 그려야 하는지(두 단계 화살표)를 코드 근거와 함께 적었다.
랩미팅의 글로벌/로컬 Envoy 질문에 대한 사실 확인도 포함 — **사이트별 로컬
Envoy 는 없고**, 컨트롤러는 front Envoy 로그 하나만 읽는다.

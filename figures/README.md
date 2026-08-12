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

## 남은 그림 (F1 확정 후)

F2 예산 히트맵 · F3 4단계 누적 분해 · F4 부하 격차 곡선 · F5 46:1 ·
F6 아키텍처(`ARCH_SPEC.md` 명세만, 그리기는 도구로).

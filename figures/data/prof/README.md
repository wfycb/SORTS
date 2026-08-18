# `figures/data/prof/` — 지연 요소 분해 CSV (지도교수 회신 대응, `graph-data-v1`)

2026-08-13. **새 런 없음** — 기존 원자료(`runs/**/load_c*.csv` +
`envoy_access.log.gz`)의 재추출본이다. 전 파일 **UTF-8 BOM · 헤더 1행**
(엑셀에서 더블클릭하면 그대로 열린다).

재생성: `python3 analysis/stage6/prof_extract.py`
검증:   `python3 analysis/stage6/prof_verify.py`

---

## 1. 용어 — 분해 정의 표

회신에서 지적된 "네트워크 지연"이라는 뭉뚱그린 말을 쓰지 않는다. 아래 네
가지로 통일한다.

| 용어 | 포함하는 것 | 산식 | 비고 |
|---|---|---|---|
| **무선 구간 지연** (UE ↔ 코어) | UE 스택, 무선 셰이핑(netem), GTP 터널, `.12`↔`.43` 구간, **5G 코어 사용자평면(UPF) 처리** | 부하생성기 `service_ms` − Envoy 필드16 | **왕복만** 측정된다(§3) |
| **코어 처리 지연** (front Envoy) | 라우팅 판단·헤더 처리·업스트림 선택 | Envoy 필드16 − 필드18 | p50 **0.050 ms** |
| **백홀 지연** (코어 ↔ 서버) | `.43` ↔ 사이트(S1 `.3` / S2 `.2` / S3 `.40`) 왕복 | ① 설정 `d_net` 2/15/25 ms ② 필드17(커넥션 수립) 사이트 중앙값 0.349/14.850/24.847 | 두 열 병기 — §4 |
| **서버 처리 지연** | DSB 애플리케이션 처리(=시스템의 `f_c`) | Envoy 필드18 − 백홀 | 두 기준 각각 |

**분해는 요청 단위 항등식이다.** 조인 키 `x-request-id`(부하생성기가
`uuid4` 로 만들어 헤더로 실음 → front Envoy `preserve_external_request_id:
true` 로 보존)로 **조인율 100.000 %**(405,013 / 405,013). 네 구간이 전부
*같은 호스트에서 잰 구간 길이의 차분*이라 호스트 간 시계 오차가 산식에
들어가지 않는다(시간축 라벨링에만 쓴다).

백분위는 가산적이지 않으므로 칸별 `residual_ms = Σ4구간 p50 − 실측 전체 p50`
을 남겼다. **36칸 최대 0.076 ms, 중앙 0.007 ms** (p95 판 최대 0.171).

### 코어 처리 지연이 유의미한가 — 측정했고, **아니다**

- front Envoy 처리 = **p50 0.050 ms / p95 0.059~0.061 ms**, 전체 지연의
  **0.16 ~ 0.31 %**. 클래스·사이트·밴드에 무관하게 상수다.
- **5G 코어 사용자평면(UPF/GTP)은 별도 분리 측정이 불가능하다.** front Envoy
  와 UPF(`ogstun`)가 **같은 호스트 `.43` 에 동거**하므로 "코어 ↔ 라우터"라는
  구간 자체가 물리적으로 존재하지 않는다. UPF 처리는 무선 구간 지연 안에
  들어 있고, 정상 밴드의 무선 구간 왕복이 **0.33 ~ 0.38 ms** 이므로 UPF
  처리는 그 상한 안에 있다.

즉 두 종류의 "코어 지연" 모두 **유의미하지 않다**(각각 0.05 ms, ≤0.38 ms).
그래프에서는 막대 두께로 보이지 않는다 — 열은 남겨 두되 "측정했고 무시할
수준"이라는 답으로 읽으면 된다.

---

## 2. 업링크 / 다운링크 분리 — **원리적으로 불가, 다만 열화는 100 % 하향**

측정되는 것은 왕복뿐이므로 상·하향 **분리 측정은 불가능하다.** 대신 다음이
실측으로 확정된다.

1. **셰이핑은 하향 전용이다.** `testbed/pc5-43/tb-radio2.sh`(v4)는 `ogstun`
   의 **egress** 에 걸고, 필터가 `match ip dst <UE IP>/32` 이다. 상향
   (UE → 코어)에는 어떤 qdisc 도 붙지 않는다.
2. **"응답이 크니 상향은 무시해도 된다"는 논법은 성립하지 않는다.** 요청
   바이트(HTTP 헤더 포함 실계산)는 reserve 247 B / search 191 B /
   recommend 175 B 이고 응답 본문은 36 / 4474 / 200 B 다 — **reserve 는
   요청이 응답보다 크다.**
3. **대신 성립하는 논증**: 1600 kbit 인가로 늘어난 무선 구간 증분이 세
   클래스 전부에서 **하향-단독 예측과 일치**하고, 양방향 셰이핑 가설은
   요청이 가장 큰 reserve 에서 2 배 어긋나 기각된다.

   | 클래스 | 실측 증분 | 하향만 셰이핑(예측) | 양방향이라면 |
   |---|---|---|---|
   | search | **24.82 ms** | ~24 ms | ~26 ms |
   | recommend | **2.25 ms** | ~2.2 ms | ~3.3 ms |
   | reserve | **1.43 ms** | ~1.4 ms | ~2.9 ms |

4. **상향의 절대 크기 상한**: 정상 밴드 무선 구간 왕복 p50 =
   **0.33 ~ 0.38 ms**. 상향은 이 안에 포함되므로 ≤0.38 ms 이고, 밴드를
   걸어도 변하지 않는다(하향만 셰이핑되므로).

---

## 3. 파일별 출처·조건·산식

### `G1_delay_breakdown.csv` / `G1_delay_breakdown_p95.csv` (36행)

- 출처 런: `runs/stage5-20260812/{s5_sorts,s5_lr,s5_loc}_L450_1`
  (F1 과 같은 계열 — L=450, 교란 `seq_extreme` 1600 kbit, 16 conn/코호트).
- 칸 = `policy` × `class` × `band` × `site`. **대표값 p50**, `_p95` 는 같은
  칸의 p95.
- `band`: 요청의 코호트와 구간으로 정한다 — c1 은 `c1only`·`both` 구간에서,
  c2 는 `both` 구간에서 `degraded`. 마크 전후 **±2 s(GUARD)** 는 전이
  구간으로 보고 **버린다**(`t2_policy_repeat.windows` 와 같은 규약).
- 요청 필터: `status=200` + 응답 바이트 유효(대장 `is_valid` 와 동일).
  L450 3런에서 제외 **0건**.
- `radio_ms_model` = 결정식이 쓴 `d_acc = B×8/rate×1.10`(정상 밴드는 0).
  예산 표 `analysis/stage6/budget_table.csv` 와 값이 일치한다.
- `n_small` = 1 이면 `n < 100` (엑셀에서 뺄지 판단용). **현재 전 칸 0.**

**빈 칸은 결측이 아니라 정책의 결과다.** 3 정책 × 3 클래스 × 2 밴드 ×
3 사이트 = 54 칸 중 36 칸만 있다. 없는 칸은 *그 정책이 그 사이트로 그
요청을 보내지 않았다*는 뜻이다 — 예: `bl_loc_pri` 는 S1 행만 있고(엣지
고정), SORTS 는 search 를 정상 밴드에서 S1 로 거의 보내지 않는다. §4 의
"S3 를 한 건도 안 썼다"와 같은 종류의 이야기이므로 **결측 처리하지 말 것.**

### `G1b_model_vs_measured.csv` (3행)

`d_acc` 추정 모델 vs 실측 증분. 증분 = `p50(degraded) − p50(normal)`,
3 정책·전 사이트 풀링(무선 구간은 사이트 무관 — G1 에서 확인).

- `d_acc_model_ms` = 현행 모델 `본문 바이트 × 8 / rate × 1.10`.
- `d_acc_model_hdr_ms` = 헤더 보정판
  `(본문 + H_http + 40 × 패킷수) × 8 / rate`, `MSS = 1360`(ogstun MTU 1400 − 40).
- `h_http_fit_b = 210.1 B` 는 **단일 패킷 클래스(reserve·recommend)의 함의
  바이트로 적합한 값**이다. 따라서 그 두 행의 `err_pct_hdr`(+0.1 / −0.1 %)
  는 적합점이라 검산이 아니고, **독립 검증은 search 뿐**이다 — 외삽 결과
  **−2.4 %**. 원인 진단(HTTP/TCP 헤더 미계상)이 맞다는 뜻이다.
- **결정 로직은 고치지 않는다**(동결 유지). `docs/ISSUES.md` I-20 참조.

### `G2_radio_timeseries.csv` (300행) — 무선 축

- 출처 런: `runs/stage5-20260812/s5_sorts_L450_1` (F1 의 SORTS 패널과 동일).
- 1 초 버킷. `lat_*_ms` 는 **`corrected_ms` 의 p50** — 논문의 SLO 판정이
  `corrected_ms` 기준이라 `slo_*` 선과 같은 축에서 읽히도록 맞췄다.
  (G1 의 분해는 `service_ms` 를 쓴다 — `corrected_ms` 의 조정분은 어느
  물리 구간에도 귀속되지 않기 때문이다. 두 값의 p50 차는 정상 구간에서
  ~0.1 ms.)
- `band_kbit_c*` : 실제 인가된 netem rate. **빈 칸 = 셰이핑 없음(무제한).**
- `share_*_pct` : **search 클래스 기준** 사이트 몫.
- `phase` : `pre / c1only / both / post`, 마크 ±2 s 는 **`transition`**.

### `G3_server_timeseries.csv` / `G3b_server_timeseries_strictfar.csv` (각 360행) — 서버 축

**두 파일은 짝이다. 하나만 보면 안 된다.** 같은 교란(S3 서버 열화)을 정책만
바꿔 건 두 런이고, **갈린 것은 예산 잔여**다.

| 파일 | 출처 런 | 정책(arm) | f_c(S3) pre → stress | 예산 잔여 | 예산 소진 초 | S3 몫 |
|---|---|---|---|---|---|---|
| `G3_…csv` | `runs/taskA-20260809/T3_fartier_both_server` | **`far_tier` + 추정 both** (F1 의 SORTS arm과 동일) | 3.77 → **6.55 ms** | +4.67 → **+1.89 ms** | 5 / 116 (4 %) | 41.2 → 41.7 % (**안 움직임**) |
| `G3b_…csv` | `runs/taskA-20260809/T2_strictfar_both_server` | **`strict_far`** (가용한 가장 먼 사이트 하나) | 4.10 → **11.50 ms** | +4.34 → **−3.06 ms** | 80 / 116 (69 %) | 100 → **71.4 %** (**옮김**) |

(값은 초당 p95 의 구간 중앙값. `G3b` 의 both-창 집계 기준 S3 몫은 66.4 % —
초당 중앙값 71.4 % 와 통계량이 다르다.)

**읽는 법**: 서버가 나빠지면 무조건 옮기는 것이 아니라, **예산이 남으면 그대로
두고 소진되면 옮긴다.** T3 는 f_c 가 1.7 배로 올라도 예산이 +1.9 ms 남아 옮길
이유가 없었고 실제로 안 옮겼다. T2 는 f_c 가 2.8 배로 올라 예산이 −3.1 ms 로
소진되자 S3 를 100 % → 71 % 로 뺐다. 무선 축에서 "S3 예산이 −9.6 ms 라 search
를 한 건도 안 보냈다"(대장 §4)와 **같은 논리의 뒷면**이다.

공통 조건·열 정의:

- L=800, 360 s. 교란 = `.40`(**S3**)에 `tb-stress.sh` (t≈120.7 s on →
  240.1 s off). c1 에 **상시 밴드 6000 kbit** 가 측정 시작 전부터 걸려 있다
  (전 구간 상수, 두 런 동일).
- `phase` 가 **`pre / stress / post` 3 단**이다 — G2 의 4 단과 다르다.
- `fc_*_ms` = **search 클래스의 초당 p95** (시스템의 `f_c` 정의가
  "service p95"이고 `docs/TASKA_REPORT.md` 도 "S3 search f_c p95"로 인용한다).
  `fc_*_ms` 는 백홀 실측 기준, `fc_*_ms_dnet` 는 `d_net` 상수 기준.
- **`fc_budget_s3_ms` = 8.44 ms (상수)** — `SLO(search) 45 − GB 5 −
  d_net(S3) 25 − d_acc(search@6000k) 6.562`. **구속하는 코호트(c1, 상시 밴드)
  기준**이다. 밴드가 없는 c2 의 예산은 15.00 ms 로 더 넓으므로, 예산이 먼저
  터지는 쪽은 항상 c1 이다. 엑셀에서 **수평 참조선**으로 쓰면 된다.
- **`budget_s3_ms` = `fc_budget_s3_ms` − `fc_s3_ms_dnet`** = 남은 예산.
  결정식과 같은 기준으로 빼려고 `_dnet` 쪽을 쓴다. **음수 = 예산 소진.**
- **`fc_s1_*` 는 두 런 모두 전 구간 비어 있다** — `far_tier` 는 원거리 전멸
  시에만, `strict_far` 는 가장 먼 사이트 하나만 쓰므로 S1 유입이 0 이다.
  결측이 아니라 정책의 결과다(§3 G1 의 같은 이야기).
- T2 는 stress 구간에서 `lat_search_ms` 가 오히려 **33.1 → 29.7 ms 로 내려간다**
  — S3(d_net 25)에서 S2(d_net 15)로 옮겨서 생긴 결과다. 서버가 나빠졌는데
  체감 지연이 좋아지는 구간이라 캡션에 설명을 붙이는 편이 낫다.

### `G4_policy_share.csv` (903행) / `G4b_policy_violation.csv` (6행)

- `figures/data/f1_L450_sites.csv`(F1c 입력)를 그대로 재사용해 한 파일로
  합치고 `phase` 를 붙였다. 몫은 **전 클래스** 기준(G2 의 search 기준과 다름).
- `G4b` 는 `c1only`·`both` 창의 위반율 — 산식은 대장과 같은
  `analysis/night-20260810/t2_policy_repeat.one_run`
  (위반 = `corrected_ms > SLO` 또는 바이트/상태 무효).

### `G5_layer_cumulative.csv` (4행)

`figures/data/f3_cumulative.csv`(= 대장 §4) 재사용. `stdev` 는 런별 값의
표본표준편차(n−1). **첫 행은 n=1 이라 표준편차가 없다** — `note` 에 명시.

---

## 4. 백홀·서버 두 기준을 병기하는 이유 (I-1)

`d_net` 은 **10 KB ping 왕복 실측 + netem 주입값**으로 캘리브레이션됐다
(`docs/ISSUES.md` I-1). 그런데 실제 응답은 36 B ~ 4474 B 라, 100 Mb/s 링크인
S1 에서는 10 KB 직렬화(~1.6 ms)가 통째로 과다 차감된다. 결과:

| 사이트 | 필드17 실측 백홀 | 설정 `d_net` | 차 |
|---|---|---|---|
| S1 | 0.349 ms | 2.0 ms | **−1.65** |
| S2 | 14.850 ms | 15.0 ms | −0.15 |
| S3 | 24.847 ms | 25.0 ms | −0.15 |

그래서 `bl_lr` 의 S1 recommend/reserve 는 `server_ms`(d_net 기준)가 **음수**로
나온다(−0.81 / −0.42 ms; 해당 셀 요청의 84 ~ 92 %). 처리 방침:

- **그래프에는 `backhaul_ms_meas` · `server_ms_meas` 를 쓴다** — 음수 막대가
  없어야 누적 세로 막대가 읽힌다.
- **결정식·대장 설명에는 `backhaul_ms`(= `d_net`) 를 쓴다** — 시스템이 실제로
  더하고 빼는 값이 그것이다.
- 두 기준의 **합(백홀+서버)은 같으므로** `total_ms` 와 `residual_ms` 는 하나뿐이다.
- 결정 로직은 **고치지 않는다**(I-1 은 라우팅에 영향 없음이 이미 규명됨 —
  결정식이 더하는 `d_net` 과 관측기가 빼는 `d_net` 이 같은 값이라 상쇄된다).

---

## 5. 엑셀 차트 안내

| 파일 | 차트 | 축 구성 |
|---|---|---|
| `G1_delay_breakdown.csv` | **누적 세로 막대** | 가로 = `class`+`band`+`site` 조합, 값 = `radio_ms` / `core_ms` / `backhaul_ms_meas` / `server_ms_meas` 4계열 누적. `slo_ms` 를 꺾은선으로 겹친다. `policy` 는 슬라이서(피벗)로. |
| `G1b_model_vs_measured.csv` | 묶은 세로 막대 | `d_acc_model_ms` vs `radio_delta_meas_ms` vs `d_acc_model_hdr_ms`, 보조축에 `err_pct`. |
| `G2_radio_timeseries.csv` | **3단 꺾은선 + 영역** | 1단 `band_kbit_c1/c2`(계단), 2단 `lat_*_ms` 3선 + `slo_*` 점선, 3단 `share_*_pct` 100 % 누적 영역. 가로축 `t_sec` 공유. |
| `G3_server_timeseries.csv`<br>`G3b_server_timeseries_strictfar.csv` | **3단 꺾은선 + 영역, 2장 나란히** | 1단 `fc_s3_ms_dnet` + `budget_s3_ms`(0 기준선 강조) + `fc_budget_s3_ms`(수평 참조선), 2·3단은 G2 와 동일. **두 장을 같은 세로축으로 좌우 배치**해야 "예산 남음→안 움직임 / 소진→옮김"이 한눈에 보인다. G2 와 나란히 놓으면 무선 축 ↔ 서버 축 대조가 된다. |
| `G4_policy_share.csv` | **100 % 누적 영역 3개** | `policy` 로 필터해 3장(SORTS / bl_lr / bl_loc_pri), 같은 세로축. `G4b` 는 옆에 표로. |
| `G5_layer_cumulative.csv` | 세로 막대 | `violation_pct`, 오차막대 `stdev`(첫 행은 없음). |

`phase` 열로 교란 구간에 음영을 넣으면 읽기 쉽다. `transition` 행은 마크
적용 전후 ±2 s 라 해석에서 빼는 것이 안전하다.

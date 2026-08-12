# 그림 ↔ 값 파일 대응 (figures-v2)

2026-08-13. 저장소 `wfycb/SORTS` 태그 **`figures-v2`** 시점 산출물.
**그림·값 파일·생성 스크립트는 이 `figures/` 디렉터리 안에 함께 있다.**
캡션·발표 순서는 `README.md`, 아키텍처 명세는 `ARCH_SPEC.md`.

## 구성

```
talk/  paper/  backup/   그림(PDF 벡터 + PNG 300 dpi)
data/                    각 그림에 들어간 값 파일(CSV)
*.py                     생성 스크립트(추출 3 + 그리기 4 + 스타일 1)
README.md  MANIFEST.md  ARCH_SPEC.md
```

## 발표용 최종 5장 (이 순서)

| # | 그림 (talk/) | 값 파일 (data/) |
|---|---|---|
| 1 | `F1c_sites_by_policy_L450` | `f1_L450_sites.csv` |
| 2 | `F1_timeseries_L450` | `f1_L450_latency.csv`(밴드·지연·위반율) · `f1_L450_sites_by_class.csv`(search 몫·참조선) · `f1_L450_slack.csv`(slack·관측 밴드) · `f1_L450_marks.csv`(교란 시각) |
| 3 | `F3_cumulative` | `f3_cumulative.csv` |
| 4 | `F4_gap_curve` | `F4_s5_gap_curve.csv` |
| 5 | `F5_envoy_blindness` | `f5_blindness.csv` |

## 예비 (질문 대비)

| 그림 | 값 파일 | 쓰임 |
|---|---|---|
| `F1b_L800_saturated` | `f1_L800_latency.csv` | L=800 에서 locality-first 가 교란 전부터 포화 |
| `F2_budget_heatmap` | `F2_budget_table.csv`(= `analysis/stage6/budget_table.csv`) | 밴드가 클래스를 가려서 때린다(예산 표) |
| `F1d_byclass_L450/800` | `f1_L{450,800}_sites_by_class.csv` | 클래스 × 사이트 9칸 완전판 |
| `F1_timeseries_L450_full` | 위와 동일 | 지연 축 0~115 ms 백업판(잘림 없음) |
| `F1_timeseries_L450_slackc2` | `f1_L450_slack.csv` | "다른 UE 는?" — 코호트 2 의 slack |
| `F4_gap_curve_kratio` | `F4_s5_gap_curve.csv` | 가로축을 K비로 정규화한 판 |
| `backup/F1_timeseries_scatter_L450` | `f1_L450_points.csv` | 요청 단위 산점도 |

## 값 파일 열 설명

| 파일 | 열 |
|---|---|
| `f1_L*_latency.csv` | `policy, t_rel_s, req_idx_start, n, viol_pct, service_p50/p95, corrected_p50/p95` (1 s 버킷, search) |
| `f1_L*_sites.csv` | `policy, t_rel_s, S1/S2/S3(건수), S1_pct/S2_pct/S3_pct` (전 클래스) |
| `f1_L*_sites_by_class.csv` | `policy, t_rel_s, class, n, S1_pct/S2_pct/S3_pct` |
| `f1_L*_slack.csv` | `t_rel_s, cohort, class, slack_S1/S2/S3, observed_rate_kbit, d_acc_ms, subset_cluster` (SORTS 런에만 존재) |
| `f1_L*_points.csv` | `policy, t_rel_s, req_idx, service_ms, corrected_ms, ok` (요청 단위 추출) |
| `f1_L*_marks.csv` | `policy, mark, label, t_rel_s` (c1@120 / both@180 / clear@240) |
| `f3_cumulative.csv` | `label, layers, n, viol_pct, half_range, runs, values` (런별 값 포함) |
| `F4_s5_gap_curve.csv` | `L_total_rps, k_ratio_bl_lr/bl_loc_pri/sorts/best_comparator, viol_*, best_comparator, gap_pp` |
| `f5_blindness.csv` | `case, condition, access_side_ms, envoy_observed_ms, ratio` |
| `F2_budget_table.csv`(= `analysis/stage6/budget_table.csv`) | `band, band_kbit, class, site, slo_ms, gb_ms, d_net_ms, d_acc_ms, fc_budget_ms, feasible, observed` |

## 공통 조건 (전 그림)

원자료 `runs/stage5-20260812/`(F2 는 계산 전용, F3 는 taskB~B3, F5 는 phase4 +
N2 캘리브). 시나리오 `seq_extreme` **1600 kbit**(c1 @120 s → 양 코호트 @180 s →
해제 @240 s), 2 코호트, 16 conn/코호트(L=1400 만 32), **hc_off**, T = 1 s,
관측 직결. 조성 M0 = reserve : search : recommend = 2 : 3 : 4.

모든 수치의 출처는 저장소 `docs/NUMBERS.md`(수치 대장), 분포는
`docs/DISTRIBUTIONS.md`.

## 재생성

값 파일을 다시 뽑으려면 원자료(`runs/…`)가 필요하고, **그림만 다시 그리는 것은
`data/` 만으로 된다**:

```bash
cd ~/exp/figures
python3 f1_extract.py --load 450 && python3 f1_extract.py --load 800
python3 f1_plot.py --load 450 --profile talk      # --scale 1.2 / 1.5 로 확대판
python3 f2_budget.py --profile talk
python3 f3_f5_extract.py && python3 f3_cumulative.py --profile talk
python3 f4_gap.py --profile talk && python3 f5_blindness.py --profile talk
```

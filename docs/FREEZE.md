# 코드 동결 (b3-freeze, 2026-08-11 02:2x)

`b3-freeze-candidate` 지점을 동결 기준으로 **확정**. git 태그 `b3-freeze`
(candidate 태그는 이력으로 유지). 이 시점 = 작업 B3 완료 직후, 검증 ①
6.50 ± 0.85 % / 검증 ② 0/3 락인 상태.

## 규칙

1. **동결 대상(아래 md5)의 변경은 명시적 동결 해제 결정을 거친다** —
   해제 없이 고치지 않는다. 분석 스크립트(`analysis/**`)만 자유.
2. 동결 중 실험은 이 md5 상태에서 돈다 — 러너 `check_deploy()` +
   파일 잠금 가드가 런 단위로 대조한다.
3. 동결 해제 시 이 파일에 해제 사유·일시·후속 태그를 append 한다.

## 동결 대상 md5 (.40 원본)

| 파일 | md5 |
|---|---|
| sorts_ctl.py | 8ff7b20648e316ecd31e1c142d989ac4 |
| obs.py | b9fac68d079b017acf99d451cd9ddbae |
| sorts.yaml.tmpl | e7be4f2ba2c21176f73db67684549b41 |
| run_all.py | 824fcaadce238de2fec4764ee5edb97c |
| gen_envoy_v10.py | cc18cf55b57b7e6805a1d6bededd253d |
| envoy_keys.json | a082dafa370da750efafa12f9e3c427b |

## `.43` 배포본 md5 (동결 시점 실측)

| 파일 | md5 | 비고 |
|---|---|---|
| ~/sorts_ctl.py | 8ff7b20648e316ecd31e1c142d989ac4 | 원본 일치 |
| ~/obs.py | b9fac68d079b017acf99d451cd9ddbae | 원본 일치 |
| ~/envoy_keys.json | a082dafa370da750efafa12f9e3c427b | 원본 일치 |
| ~/sorts.yaml | 132a8641105ea7a9873dd6366754e768 | 렌더 산출물 (기본 arm: strict/off/off/off) — 러너가 런마다 재렌더하므로 md5 는 arm 에 따라 바뀌는 것이 정상. 동결 대상은 tmpl |

## 동결 해제 기록 1 — 1단계 (2026-08-11 11:34)

- **해제 일시**: 2026-08-11 11:34 (작업 전 b3-freeze md5 전수 재확인 — 6/6 일치)
- **해제 범위** (이것만):
  - `gen_envoy_v10.py`, `envoy.yaml`(렌더 산출물), `envoy_keys.json`(렌더 산출물)
    — 비교군 3종 추가 (outlier detection `bl_od`, locality+priority `bl_loc_pri`,
    active health check 변형)
  - `sorts.yaml.tmpl` — 제어 주기 키 `ctl_period_s` 추가 (기본 1.0, 주기 변경 없음)
  - `sorts_ctl.py` — **최소**: 주기를 설정에서 읽는 부분만
  - `run_all.py` — **최소**: 새 비교군·주기 arm 렌더 경로만
- **불변**: `obs.py`, 결정 로직(`decide`/`decide_live`/용량/손실 배분), 관측
  파라미터(WINDOW_S 2.0, n_min 100/20, stale_ttl 2.0, FILL_RATIO 0.8).
- **사유**: (1) Envoy 비교군이 기본형뿐이라 불공정(특히 노드 장애 비교가
  health check off 상태) — NIGHT2_REPORT §10-1 (a) 채택. (2) 2단계 주기
  ablation 을 위한 파라미터화 (이번 단계는 기본 1.0 유지 — 귀속 분리).
- **재동결 예정**: 2단계(주기 ablation) 종료 후. 후속 태그는 그때 부여.
- **작업 전 백업**: `.40:~/exp/*.20260811-stage1.bak` 6종.
- **회귀 요구**: `ctl_period_s: 1.0` + 기존 arm = 동결 코드와 결정 단위 동일
  (obs_ctl_regress). 불일치 시 중단.

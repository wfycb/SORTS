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

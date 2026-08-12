# SORTS GitHub 백업 보고 (2026-08-11)

대상: `wfycb/SORTS` (private, 백업 전 empty). 작업 로그:
`.40:~/exp/runs/backup-20260811/PROGRESS.md`.

## 1. 저장소 구조·크기

총 **81 M / 1,396 tracked 파일 / 커밋 10개(논리 단위) + 태그
`b3-freeze-candidate`**. 예산(전체 500 M, 단일 50 M) 내 — 최대 단일 파일은
런당 obs_state.csv 수 MB 급.

| 디렉터리 | 크기 | 파일 수 |
|---|---|---|
| controller | 100 K | 5 |
| runner | 424 K | 58 |
| envoy | 48 K | 4 |
| testbed | 64 K | 6 |
| analysis | 2.3 M | 99 |
| docs | 216 K | 12 |
| results | 284 K | 27 |
| runs | 61 M | 1,186 |

## 2. 호스트별 수집과 무결성

- `.43` (읽기만): `/usr/local/sbin/tb-{radio2,radio,netem}.sh`,
  `/etc/envoy/envoy.yaml` — **scp 후 원본 md5 대조 전부 일치**.
- `.12` (읽기만): `~/tb-load.py` — md5 일치.
- `.40` (로컬): exp 의 코드·설정·매니페스트·분석·보고서·선별 런 산출물.
- `.3` (sunny): **무접촉** — S1 사이트엔 백업할 SORTS 자산 없음 (컨테이너는
  DSB 공식 이미지, 설정은 러너가 원격 주입).
- ★tb-stress.sh 는 지시 표의 `.43` 이 아니라 **`.40:/usr/local/sbin/`** 에
  있었다 (실측 확인, SOURCE.md 반영).

## 3. 배포본 vs 원본 md5

`.43` 배포본 4종(sorts_ctl.py, obs.py, sorts.yaml, envoy_keys.json) 전부
`.40` 원본과 **일치** — 배포 누락 없음 (B3 종료 시 check_deploy 통과 상태
그대로).

## 4. 비밀정보 스캔

- 패턴(password/passwd/sudo -S/echo|sudo/token/secret/PRIVATE KEY/
  Authorization:/gho_/ghp_) × 대상(커밋 후보 전체 + `*.bak` + runs 의
  md/json): **실제 자격증명 0건.**
- `password=` 히트 다수는 전부 **DSB HotelReservation 공개 벤치마크의 합성
  계정**(`Cornell_30`/10자리 0) — 벤치마크 워크로드 정의의 일부로 비밀이
  아니라고 판단, 유지.
- 호스트 비밀번호 2종(노드맵에 있는 것)은 커밋 후보 어디에도 없음을 패턴
  검색으로 확인 (값 미출력).
- 원격 수집분(testbed/, envoy/)도 수집 후 재스캔 — 무검출. tb-radio2 는
  sudoers NOPASSWD 전제라 스크립트에 자격증명 없음.
- git 히스토리: 백업 전 원격이 empty 라 기존 히스토리 오염 논점 없음.

## 5. 제외한 것과 이유

| 제외 | 이유 |
|---|---|
| `runs/**/envoy_access.log.gz`, `load_c*.csv`, `sorts_ctl.log`, 배치 `*.log` | 원시 대용량 (runs 원본 8.9 G → 선별 61 M). 원본은 `.40` 에 잔존 |
| `/var/log/envoy/front_access.log` (2.9 G) | 지시 §0-3 |
| `analysis/cache/` (213 M), `analysis/obs_replay/` (36 M) | 재생성 가능한 파생 캐시 |
| `calib/` 원자료 CSV·log.gz (199 M 중 json 제외 전부) | 요약 json 만 `results/calibration/` 에 |
| `*.bak` (41개) | 편집 백업 — 이력 가치가 낮고(각 작업 보고서가 변경 내역 기록) 잡음. 비밀정보는 없음(스캔 완료), 순수 용량·잡음 사유 |
| `presentation/` (1.8 M) | 발표 자료 — 시스템 재구성에 불필요 판단 |
| `t1_stair/`, `taskC_work/`, `__pycache__` | 중간 작업물·캐시 |
| UERANSIM/Open5GS/DSB 설정 | 이 백업 범위 밖 (별도 시스템 — README 재구성 절에 전제로 명시) |

## 6. README 수치의 출처

- 엣지 경합 4단계 (95.0~98.1 → 70.5~74.6 → 28.1 → 6.50±0.85):
  TASKB_REPORT §4 / TASKB2_REPORT §5 / TASKB3_REPORT §4.
- 자가 진동 (락인 3/3 63.9~70.6 % → 0/3 0.42~0.46 %, 전환 2520→27):
  TASKB_REPORT §5.
- 채움 진동 (교대율 0.96~1.00→0.00, 편차 25→0.4~0.7 %p): TASKB3_REPORT §4·§6.
- 커버리지 (프라이어 0.25~0.49 → 관측 0.941~0.947): PHASE1_REPORT §3.2.
- C/C_eff/무릎/w: TASKB_PREP·TASKB·TASKB2·TASKB3 REPORT + capacity_knee.md.
- SLO/d_net/응답 바이트/파라미터: controller/sorts.yaml (단일 출처).
- ★"코어 결정의 근거 = Envoy 1000:1 실측" 은 보고서에서 **확인 불가** —
  README 에 수치 미기재, "각 보고서 참조"로 대체 (기억으로 쓰지 않음 원칙).

## 7. 판단해서 결정한 것

1. **`~/sorts-backup` 신규 저장소** (권고안) — `~/exp` 직행 시 9.4 G 런
   산출물 동거 문제.
2. **runs 는 전 배치 포함** — 선별 규칙(decisions/obs_state/meta/summary/
   marks/thermal/마커/PROGRESS)으로 61 M 이 예산 내라 우선순위 선별이
   불필요했다. 지시의 "핵심 배치만" 분기는 미발동.
3. DSB 합성 계정은 비밀 아님으로 판단·유지 (§4).
4. `*.bak`·presentation 제외 (§5).
5. HTTPS(gh credential helper) 사용 — SSH 키 미등록 실측.
6. envoy.yaml 배포본을 그대로 싣되 "재생성 필요" 경고를 SOURCE/README 에
   명시 (코호트 IP 가 렌더 시점 값).

## 8. 검증하지 못한 채 남긴 것

- **복원의 실배포 검증** — clone 시뮬레이션으로 파일 배치 가능성만 점검
  (지시대로 실제 배포는 안 함). check_deploy 통과까지는 미실행.
- 5G 코어(UERANSIM/Open5GS)·DSB 컨테이너 스택은 이 저장소 범위 밖 —
  README 는 "가동 중" 전제로 재구성 절차를 시작한다.
- `.12` 의 tb-cohort.map 생성 경로(UE 기동 스크립트)는 미수집 — nr-ue
  systemd 유닛은 호스트 설정이라 범위 밖으로 판단 (README 함정 절에
  ueransim-gnb 유닛 부재만 명시).
- 커밋 히스토리에는 백업 시점 스냅샷만 — 과거 작업별 diff 이력은 없음
  (docs/ 보고서가 변경 서사를 대체).

---

## 9. 최종 백업 점검 (STAGE6 §7, 2026-08-13 — `paper-ready`)

### 9.1 미백업 항목

**논문·재현에 필요한 항목 미백업 0 건.** 기계 대조 결과:

| 대상 | 결과 |
|---|---|
| `~/exp/*.md`(보고서·대장·분포표·인수인계) | 전부 `docs/`(또는 `docs/handoff/`)에 존재 |
| `analysis/*` 하위 디렉터리 | `cache`·`obs_replay` 2개만 제외(§9.2), 나머지 전부 백업 |
| `manifest_*.json` 전 파일 | `runner/manifests/` 에 전부 존재 |
| 동결 6파일 md5 | exp ↔ repo **6/6 일치** |

### 9.2 의도적 제외 (파생물, 재생성 가능)

| 경로 | 크기 | 사유 |
|---|---|---|
| `analysis/cache/` | 213 M | 런 원자료에서 뽑은 중간 CSV 캐시 — 분석 스크립트로 재생성 |
| `analysis/obs_replay/` | 36 M | 관측 재생 산출물 — 동상 |
| `runs/**/load_c*.csv`·`*.log(.gz)` | (대) | `.gitignore` 규칙(§5) — 요청 단위 원자료는 `.40` 로컬 보관 |

**주의**: 위 세 항목은 **논문 수치의 출처가 아니다** — 대장(`docs/NUMBERS.md`)이
인용하는 경로는 전부 저장소 안에 있고, 인용 49건 전수 존재 확인
(`analysis/stage6/verify_numbers.py`)을 통과했다.

### 9.3 최종 상태

- 저장소 119 M(.git 72 M), 태그: `b3-freeze` → `stage1-complete` →
  `stage2-freeze` → `stage3-freeze` → `stage4-freeze` → `stage5-complete` →
  `figures-v1` → `figures-v2` → **`paper-ready`**.
- 결정 로직 6파일은 **b3 이후 md5 불변**(해제 창은 전부 닫힘 — `docs/FREEZE.md`).

# SORTS

5G 엣지 3-사이트 테스트베드에서 **무선 상태·서버 상태·용량을 함께 아는
코어측 클래스별 라우팅 컨트롤러** — 관측(obs) → 결정(sorts_ctl) →
실행(front Envoy 런타임 가중치)의 3층 반응형 시스템.

> **⚠ 이 저장소를 쓰기 전에 반드시 [함정](#함정-반드시-읽을-것) 절을 읽을 것.
> 특히 `.3`(S1 호스트)은 팀원(sunny) 자산이 함께 있는 머신이다 — 실험
> 트래픽 외 어떤 파일도 만지지 않는다.**

## 개요

무엇을 푸는가: 무선 접근망 열화·서버 열화·용량 포화가 섞이는 조건에서
클래스별(SLO별) 트래픽을 사이트 집합에 배정해 SLO 위반을 최소화한다.
결정은 UE 가 아니라 코어(front Envoy 옆)에서 내린다. LB 혼자서는 못 하는
이유(관측 사각): **무선 구간이 +22.541 ms 나빠지는 동안 Envoy 가 재는
값(µs 필드) 변화는 +0.022 ms — 1024.6:1** (N2 캘리브 p50 기준, 재현:
`analysis/envoy_blindness/reproduce_1000to1.py`, 데이터 동봉).

결정식 한 줄 요약 (컨트롤러 docstring·보고서 기준):

```text
slack(site) = SLO − GB − d_net(site) − f_c(site,class) − d_acc(band)
feasible    = { site ∈ tier | slack > 0 ∧ L_eff ≤ 0.9·C_eff(site, band) }
              (L_eff = Σ_class w·rps,  w = 1 / 0.278 / 0.178)
feasible 공집합(EXPECTANT) → 손실 배분(soft assignment):
  badness = max(0, −slack) 오름차순 그리디, room = 0.9·C_eff − planned,
  잔여는 S1. 표현은 weighted_clusters 런타임 가중치.
```

- **허용 집합** (작업 A): 단일 사이트가 아니라 집합을 주고 집합 내 분배는
  Envoy LEAST_REQUEST 에 위임 — SORTS 는 LB 를 대체하지 않고 제약한다.
- **용량 제약** (작업 B): 결정이 자기 부하 효과를 알게 한다 — 예측 기반
  (관측 되먹임 없음), 준안정 진동 락인(I-13) 차단이 1차 검증.
- **손실 배분** (작업 B2, I-14): SLO 를 "지키는" 장치와 못 지킬 때 "덜
  잃는" 장치는 별개다. EXPECTANT 경로에서만 발동.
- **밴드 의존 유효 용량** (작업 B3): 좁은 밴드는 지연(d_acc)뿐 아니라
  유효 용량도 깎는다(1600 k 에서 −48.9 %). 실측 테이블 C_eff.

## 시스템 구조

| 층 | 코드 | 위치 | 역할 |
|---|---|---|---|
| 관측 | `controller/obs.py` | `.43` | Envoy access log tail → f_c·응답 바이트·유닛 도착률 (윈도 2 s, stale TTL 2 s — **파라미터 튜닝 금지**) |
| 결정 | `controller/sorts_ctl.py` | `.43` | 1 s 주기, 코호트×클래스 6유닛 각각 허용 집합/가중치 결정 |
| 실행 | front Envoy | `.43` | `runtime_modify` 로 weighted_clusters 가중치 적용 (7 prefix × 클러스터 키 전부 — `envoy/envoy_keys.json` 이 단일 출처) |
| 러너 | `runner/run_all.py` | `.40` | 매니페스트 배치 실행, 렌더-배포(`sorts.yaml`), precheck/postcheck, 파일 잠금 가드 |

## 테스트베드

| IP | 노드 | 역할 | 주의 |
|---|---|---|---|
| 192.168.0.40 | PC2 | 러너(.40) + **S3** 사이트(DSB 24컨테이너) + 분석 | 이 저장소의 원본 대부분 |
| 192.168.0.2 | PC1 | **S2** 사이트 (24컨테이너) | |
| 192.168.0.3 | PC4 | **S1** 사이트 (24컨테이너) | **★sunny 팀원 자산 동거 — 실험 외 무접촉** |
| 192.168.0.43 | PC5 | front Envoy + SORTS 컨트롤러 + ogstun(무선 셰이핑) | tb-radio2 sudoers NOPASSWD 필요 |
| 192.168.0.12 | PC3 | 부하 생성기 (tb-load.py, UE 코호트 2개) | |

고정 수치 (`controller/sorts.yaml` = 단일 출처, v10 §0):
- SLO [ms]: reserve 35 / search 45 / recommend 35. 가드밴드 5.
- d_net [ms]: S1 2.0 / S2 15.0 / S3 25.0 (netem 주입 + 10 KB ping 실측 관례).
- 응답 [B]: reserve 36 / search 4474 / recommend 200 (search 는 예약 상태
  의존 4474/4632 — I-5).
- 등가 가중치 w: search 1 / reserve 0.278 / recommend 0.178 (유효 범위 =
  측정 조성 볼록포 × 사이트 — S2 전이 성립, **S3 불성립** 실측).
- 용량 [search-eq rps]: C(S1)=279, C(S2)=515, C(S3|M0)=865 (조건부),
  C_eff(S1|M0): 무제한 206.1 / 2300 k 161.7 / 1600 k 105.4.
- 무릎 (SLO 기준, 야간 실측): S1 search 단독 260·혼합 500 / S2 1000 /
  S3 >1600 (조성 의존).
- 링크: S1 은 100 Mb/s (타 사이트 1/10, I-2). 무선 밴드: poor 2300 kbit,
  극단 1600 kbit (코호트·커넥션 단위 셰이핑, tb-radio2 v2 64버킷).
- cpuset 비 S1:S2:S3 = 1:2:4 (용량비 실측 1:2:3.2+ 와 방향 일치).

## 저장소 구조

각 디렉터리의 `SOURCE.md` 에 **원본 호스트·경로**가 있다 (복원 시 그 위치로).

```text
controller/  sorts_ctl.py, obs.py, sorts.yaml(.tmpl)   → .43 배포
runner/      run_all.py, reserve_reset.sh, 회귀·매니페스트·캘리브 도구 → .40
envoy/       gen_envoy_v10.py, envoy_keys.json, envoy.yaml(.43 배포본)
testbed/     pc5-43: tb-radio2/tb-radio/tb-netem.sh · pc2-40: tb-stress.sh · pc3-12: tb-load.py
analysis/    분석 스크립트+판정 JSON 전부 (cache·obs_replay 제외)
docs/        보고서 9종 + ISSUES.md + handoff
results/     calibration/ + summary/ (핵심 판정 산출물 요약)
runs/        전 배치 선별 산출물 (decisions/obs_state/meta/summary/thermal + PROGRESS)
```

## 재구성 방법

1. **사이트**: 3사이트에 DSB HotelReservation 24컨테이너씩 (S1 `.3` /
   S2 `.2` / S3 `.40`). 예약 DB hotelId 1..1000 확장 상태.
   **컨테이너 재시작 금지** 규칙 하에 운영.
2. **경로 지연**: `.43` 에서 `testbed/pc5-43/tb-netem.sh apply`
   (재부팅마다 소실 — `precheck` 가 자동 검출). 사이트 IP 하드코딩이므로
   IP 변경 시 스크립트 수정 (I-3).
3. **무선 셰이핑**: `.43` `/usr/local/sbin/tb-radio2.sh` + sudoers NOPASSWD
   등록 (`run_all.py` 상단 주석 참조).
4. **Envoy**: `.12` 의 `/run/tb-cohort.map` 에서 코호트 IP 확인 후
   `envoy/gen_envoy_v10.py` 로 envoy.yaml + envoy_keys.json **재생성**
   (저장본 envoy.yaml 은 렌더 시점 코호트 IP 가 박혀 있음) → `.43`
   `/etc/envoy/envoy.yaml` 배치.
5. **컨트롤러 배포**: `controller/` 4파일을 `.43:~/` 로 scp.
   `runner/run_all.py` 의 `check_deploy()` 가 .40 원본과 md5 대조로 검증
   — sorts.yaml 은 러너가 매 런 렌더-배포하므로 손편집 금지.
6. **부하 생성기**: `testbed/pc3-12/tb-load.py` → `.12:~/`.
7. **실행**: `.40` 에서
   `python3 run_all.py --manifest runner/manifests/manifest_taskB3_v1.json --outdir runs/<이름>`
   — precheck(컨테이너 수·netem 결합·잔재·sudo -n·클러스터 일치)가 전제
   조건을 전부 검사하고 실패 런은 SKIPPED 로 남긴다.

## 주요 결과

| 축 | 조건 | before → after | 출처 |
|---|---|---|---|
| 엣지 경합 (검증 ①) | 1600 k × 800, 양 코호트 극단 | strict 95.0~98.1 % → far_tier 70.5~74.6 % → +손실 배분 **28.1 %** → +C_eff **6.50 ± 0.85 %** | TASKB/B2/B3_REPORT |
| 자가 진동 (검증 ②) | 클린 × 1400 + 결정적 트리거 | strict 락인 3/3 (63.9~70.6 %) → 용량 제약 **0/3 (0.42~0.46 %)**, 전환 2520→27 | TASKB_REPORT §5 |
| 채움 진동 (B3) | 검증 ① 조건 | 교대율 0.96~1.00 → **0.00**, 이월 편차 25 %p → 0.4~0.7 %p | TASKB3_REPORT §4·§6 |
| 관측 커버리지 | Phase 1 | 프라이어 0.25~0.49 (포화 시) → 관측 **0.941~0.947** (목표 0.95) | PHASE1_REPORT §3.2 |
| 용량-밴드 결합 | S1, M0 | 유효 용량 무제한 206 → 1600 k 105 (**−48.9 %**) | TASKB3_REPORT §2 |

## 알려진 이슈 (docs/ISSUES.md — 전체 원장)

| # | 제목 (요약) |
|---|---|
| I-1 | d_net(S1) 캘리브 페이로드(10 KB)와 실제 페이로드 불일치 |
| I-2 | S1(.3) 링크 100 Mb/s — 타 사이트의 1/10 |
| I-3 | DHCP 리스 변경으로 사이트 IP 변경 (무에러 실패 — netem 결합 검사로 방어) |
| I-4 | d_net(S2) 10/15 두 값 공존 — era 상수 (era-pin 10.0 변경 금지) |
| I-5 | 응답 바이트는 예약 상태의 함수 (search 4474/4632) |
| I-6 | 관측은 선택한 사이트만 본다 — counterfactual 부재 (용량 층으로 완화) |
| I-7 | 클래스 분리 라우팅 → search 바이트 지속 편향 |
| I-8 | (해소) 커넥션 복권 = dst 포트 64버킷 해시 충돌 — 소스 포트 고정으로 제거 |
| I-9 | 스트레스 캘리브는 전량-S3 고정 부하 — 부분 부하 f_c 하락 미포함 |
| I-10 | far_tier 한계 사이클은 S2/S1 경계로 이동 |
| I-11 | (폐기) c1 단독 극단 밴드 창은 교란으로서 무효 |
| I-12 | 양 코호트 창 붕괴 = S1 포화 + 커넥션 HOL |
| I-13 | strict_far 준안정 진동 락인 (용량 제약으로 차단 실증) |
| I-14 | EXPECTANT 무행동 → 손실 배분 설계 (B2/B3 로 6.5 % 달성) |

## 함정 (반드시 읽을 것)

- **`.3`(S1) 은 팀원(sunny) 자산 동거 머신 — 실험 트래픽 외 무접촉.**
- **컨테이너 재시작 금지, `docker system prune` 계열 금지** (reservation
  memcached 오류 → `log.Panic` 연쇄; 초기화는 `reserve_reset.sh` 로만).
- **성공 판정은 응답 바이트로** (200 이어도 본문이 다를 수 있다 — I-5 의
  ±10 % 대역 관례). 알림/로그를 믿지 말고 산출물 파일로 판정.
- **Envoy DURATION 은 정수 ms** — sub-ms 는 access log 필드 18(µs)로.
- **7-prefix 키**: 라우트가 코호트×클래스 6 + 폴백으로 분리 — 가중치는
  `envoy_keys.json` 의 전 클러스터 키에 써야 한다 (구 `site_weights` 류
  키는 어떤 라우트도 읽지 않음 — 조용한 무시).
- **배치 실행 중 템플릿·설정·배포 파일 편집 금지** — 러너의 파일 잠금
  가드(md5)가 그 런을 실패시킨다 (작업 B 에서 SKIPPED 6건 사고).
- `pkill -f` 는 자기 ssh 명령줄에 매칭될 수 있다 — `[.]` 패턴 관례 준수
  (`run_all.py` 주석).
- 관측 파라미터(WINDOW_S 2.0, n_min 100/20, stale_ttl 2.0, FILL_RATIO 0.8)
  **튜닝 금지**.
- `sorts.yaml` 은 렌더 산출물 — 고치려면 `sorts.yaml.tmpl` 을 고치고
  러너가 렌더하게 한다.
- ueransim-gnb systemd 유닛은 어디에도 없음 — 새로 만들지 말 것 (수동
  기동과 socket 충돌 이력).

## 문서 (docs/)

| 문서 | 범위 |
|---|---|
| ISSUES.md | 이슈 원장 I-1~I-14 (등록·갱신 이력 포함) |
| PHASE1_REPORT.md | 관측 계층 (f_c/바이트 추정, 커버리지) |
| PHASE4_REPORT.md | 관측판 본실험 (const vs both, radio 비용) |
| NIGHT_REPORT.md | 용량 무릎 실측 (S1/S2/S3), far_tier 승격, 진동 발견 |
| TASKA_REPORT.md | 허용 집합 (subset policy) 설계·검증 |
| TASKC_REPORT.md | 커넥션 복권 규명 (I-8)·I-12 정정 |
| TASKB_PREP_REPORT.md | w/C 식별, 준안정 락인 반복 (D1) |
| TASKB_REPORT.md | 용량 제약 — 검증 ②(진동 차단) 성공, 검증 ① 관할 규명 |
| TASKB2_REPORT.md | 손실 배분(I-14) — 74.6→28.1 %, C(S3) w 불성립, 드리프트 진단 |
| TASKB3_REPORT.md | 밴드 의존 유효 용량 — 28.1→6.5 %, C_eff 3점 |

백업 자체의 수집·검증 기록은 `BACKUP_REPORT.md`.

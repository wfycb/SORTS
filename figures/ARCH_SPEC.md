# F6 아키텍처 그림 — 구성 요소 명세 (그리기용, 코드 기준)

STAGE6-FIG §7. **이 문서는 그림이 아니라 명세다** — 데이터가 없는 개념도라
그리기 도구로 직접 그리는 편이 낫다. 아래는 **실제 코드·설정에서 확인한 사실**만
적었다(추측 없음). 확인 근거를 각 항목에 달았다.

## 1. 노드와 역할 (실제 호스트)

| 블록 | 호스트 | 실제 구성 | 근거 |
|---|---|---|---|
| **UE / RAN** | `192.168.0.12` (PC3) | UERANSIM UE 6개 → 터널 `uesimtun0..5` (10.46.0.6~.11). 부하 생성기 `tb-load.py` 가 이 터널에 바인딩해 요청을 낸다 | `/run/tb-cohort.map` 실측, `run_all.py:28` `LOADGEN` |
| **5G 코어 + LB + 컨트롤러** | `192.168.0.43` (PC5) | Open5GS(UPF) — 사용자 평면 종단 `ogstun`(10.46.0.1/16) · **front Envoy v1.39** (admin 127.0.0.1:9901, access log `/var/log/envoy/front_access.log`, `--file-flush-interval-msec 100`, `--concurrency 2`) · **컨트롤러 `sorts_ctl.py`** · 무선 셰이핑 스크립트 `tb-radio2.sh` | `sorts_ctl.py:5-6` 도크스트링, `run_all.py:29,31`, `ip -br addr` 실측 |
| **S1 (edge)** | `192.168.0.3` (PC4) | DSB Hotel-Reservation **컨테이너 24개**, d_net 2 ms | `run_all.py:30` `SITES`, precheck 24/사이트 |
| **S2 (regional)** | `192.168.0.2` (PC1) | 동일 스택 24개, d_net 15 ms | 〃 |
| **S3 (central)** | `192.168.0.40` (PC2) | 동일 스택 24개, d_net 25 ms. **배치 러너도 이 호스트** | 〃 |

경로 지연 `d_net` 은 **.43 의 `eno1`** 에 netem 으로 주입한다(사이트별 클래스 —
`delay 181us / 14.7ms / 24.7ms`, 목표 2/15/25 ms). 무선 열화는 **`ogstun`** 에
커넥션 단위로 건다. 두 지점이 다르다는 것을 그림에서 구분할 것.
근거: `run_all.py:57 NETEM_EXPECT`, `run_all.py:290,295`.

## 2. 관측 경로 (컨트롤러로 들어오는 화살표) — 2개뿐

| 신호 | 출처 | 읽는 방법 | 주기 |
|---|---|---|---|
| **`rate`** (무선) | `.43` 의 **`ogstun` tc qdisc** | `tc qdisc show dev ogstun` 의 netem **rate 속성**을 파싱. 트래픽 측정이 아니라 **커널에 설정된 값**을 읽는다 — 그래서 물리적 발효보다 먼저 보인다(**I-16**, 문서화된 성질) | tick마다 (기본 **1 s**) |
| **`f_c`, `resp_bytes`** (서버·응답) | `.43` 의 **front Envoy access log** | 로그 증분 tail → 필드18(업스트림 왕복 µs)/1000 − d_net[site] = `f_c`, 필드10/11로 사이트 판별, 응답 바이트 관측 | tick마다, 윈도 **2 s**(`WINDOW_S`) |

**중요(랩미팅 지적 대응)**: 이 테스트베드에 **사이트별 로컬 Envoy 는 없다.**
LB 는 `.43` 의 front Envoy **하나**이고, 컨트롤러는 사이트에 접속하지 않는다 —
**같은 호스트의 access log 한 개**만 읽는다(`obs.py:122 DEFAULT_LOG_PATH`,
`Observer` 가 tail 하는 파일은 그 하나). 즉 현 구현은 "글로벌 LB 1개 + 그 LB 의
로그를 읽는 컨트롤러" 구조이며, 사이트별 Envoy 정보를 취합하는 형태가 **아니다.**
계층형(글로벌/로컬) 구조는 현 구현 범위 밖이고, 그림에도 그렇게 그려야 한다.

## 3. 제어 경로 (컨트롤러에서 나가는 화살표) — 1개

- 컨트롤러는 (코호트 × 클래스) 유닛마다 **허용 집합(candidate set)** 을 정하고,
  그것을 front Envoy 의 **runtime key** 로 쓴다:
  `POST 127.0.0.1:9901/runtime_modify?routing.<prefix>.<cluster_key>=<0|100>`
  (`sorts_ctl.py:418-426`). 부분 배정(soft assignment)일 때는 같은 키에 정수
  비중(예: `S1:72|S2:28`)을 쓴다(`apply_weights`, `sorts_ctl.py:277-285`).
- 키 공간: **prefix 19개**(코호트 6 × 클래스 3 + fallback) × **cluster key 12개**
  (`envoy_keys.json`).
- **변경이 있을 때만 호출한다** — 결정 상태 문자열이 이전 tick 과 같으면 apply 를
  건너뛴다(`run()` 의 `changed` 분기). 정상 상태에서 apply 비용은 0.

## 4. 그림에서 반드시 읽혀야 하는 것

> **SORTS 는 LB 를 대체하지 않고 제약한다.**

- 컨트롤러는 **최종 목적지를 지정하지 않는다.** 유닛마다 *어느 사이트들이 후보로
  허용되는가*(집합, 또는 비중)만 넘기고, **집합 안에서 어느 인스턴스로 보낼지는
  Envoy 의 least-request 가 그대로 결정**한다. 그림에서는
  `controller → (allowed set) → Envoy → (least-request) → site` 처럼
  **두 단계 화살표**로 그려야 하며, 컨트롤러에서 사이트로 직접 가는 화살표를
  그리면 틀린 그림이다.
- 비교군(`bl_lr`, `bl_loc_pri`, `bl_rr`, `bl_od`)은 **같은 Envoy 의 다른 클러스터
  키**로 구현된다 — 즉 데이터 평면은 동일하고 **선택 로직만 바뀐다**(공정 ablation).
- 컨트롤러가 없으면(비교군) 관측 화살표 2개와 제어 화살표 1개가 통째로 사라진다.
  **`slack` 같은 상태는 비교군에 존재하지 않는다**(F1 4단의 "SORTS only" 문구와
  같은 사실).

## 5. 그릴 때 권장 레이아웃

```
[UE 6 (uesimtun0..5)]  --5G user plane-->  [ogstun @ .43]
        .12                                     |
   tb-load.py (open-loop,                       |  (radio shaping: tb-radio2.sh,
   16 conn/cohort)                              |   per-connection netem)
                                                v
                                     [ front Envoy v1.39 @ .43 ]
                                       |    ^          |
        (allowed set / weights)        |    |          | least-request within set
   [ sorts_ctl.py @ .43 ] -------------+    |          v
        ^          ^                        |    [S1 .3] [S2 .2] [S3 .40]
        |          |                        |     24 containers each
        |          +--- access log (f_c, bytes; window 2 s)
        +--- tc qdisc show dev ogstun (rate; tick 1 s)
```

- 화살표 라벨에 **주기**를 적을 것: 관측 1 s(윈도 2 s) / apply **변경 시에만**.
- `d_net`(2/15/25 ms)은 Envoy → 사이트 구간에 표시(주입 지점은 `.43 eno1`).
- 색은 `figures/style.py` 의 사이트 색(S1 주황 / S2 하늘 / S3 자주)을 따를 것.

## 6. 그림에 넣지 말 것 (사실과 다름)

- 사이트별 로컬 Envoy / 사이드카 — **없다**.
- 컨트롤러 → 사이트 직결 화살표 — 제어는 Envoy runtime key 로만 나간다.
- 컨트롤러의 능동 프로브 — **없다**. 관측은 access log tail 과 tc 읽기뿐이다.
- RAN 텔레메트리 인터페이스 — 없다. `ogstun` netem 이 무선 상태의 stand-in 이며
  그 사실은 보고서·논문 §3 에 명시돼 있다(I-16).

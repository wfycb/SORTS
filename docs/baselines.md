# Envoy 비교군 설정 기록 (1단계, 2026-08-11)

원칙: **문서 기본값 우선** (Envoy v1.39.0 문서에서 확인 — 기억 사용 금지).
기본값에서 벗어난 항목은 이유를 명기한다. 렌더 원본 = `gen_envoy_v10.py`
(b3-freeze 해제 창, docs/FREEZE.md 해제 기록 1).

## 1. bl_od — least-request + outlier detection (작업 1-1)

```yaml
    - name: bl_od
      type: STATIC
      connect_timeout: 1s
      # choice_count 는 기본값 2 (P2C) 를 쓴다. 지정하지 않는다.
      lb_policy: LEAST_REQUEST
      outlier_detection: {}
      load_assignment:
        cluster_name: bl_od
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address: { address: 192.168.0.3, port_value: 5000 }
              - endpoint:
                  address:
                    socket_address: { address: 192.168.0.2, port_value: 5000 }
              - endpoint:
                  address:
                    socket_address: { address: 192.168.0.40, port_value: 5000 }
```

- `outlier_detection: {}` = **전부 문서 기본값** (v1.39 api-v3
  outlier_detection.proto 확인): consecutive_5xx 5, interval 10s,
  base_ejection_time 30s, max_ejection_percent 10 %,
  enforcing_consecutive_5xx 100 %, enforcing_success_rate 100 %
  (success_rate_minimum_hosts 5 — 3노드라 성공률 축은 사실상 비활성),
  consecutive_gateway_failure 5 (enforcing 0 % — 기본 비활성),
  always_eject_one_host false, max_ejection_time 300s.
- 기본값에서 벗어난 항목: **없음**. `connect_timeout`/`type`/엔드포인트는
  기존 비교군(bl_lr)과 동일한 테스트베드 상수.
- ★기록할 한계 (기본값의 귀결, 튜닝하지 않음):
  - max_ejection_percent 10 %: 3노드에서 두 번째 호스트부터는 축출 불가.
    첫 축출 허용 여부(0 % < 10 % 시점 판정)는 문서가 명시하지 않아 **런
    stats(`outlier_detection.ejections_enforced_total`)로 실측 보고**한다.
  - 축출 트리거는 오류 기반(5xx/게이트웨이 실패). 지연만 오르는 grey
    (서버 축 stress 교란, 응답은 200)는 정의상 못 본다 — 이것이 결과로
    나오면 그대로 보고 (우리 주장은 "잃지 않는다").

## 2. bl_loc_pri — locality + priority failover (작업 1-2)

```yaml
    - name: bl_loc_pri
      type: STATIC
      connect_timeout: 1s
      lb_policy: ROUND_ROBIN
      load_assignment:
        cluster_name: bl_loc_pri
        endpoints:
          - locality: { zone: s1 }
            priority: 0
            lb_endpoints:
              - endpoint:
                  address:
                    socket_address: { address: 192.168.0.3, port_value: 5000 }
          - locality: { zone: s2 }
            priority: 1
            lb_endpoints:
              - endpoint:
                  address:
                    socket_address: { address: 192.168.0.2, port_value: 5000 }
          - locality: { zone: s3 }
            priority: 2
            lb_endpoints:
              - endpoint:
                  address:
                    socket_address: { address: 192.168.0.40, port_value: 5000 }
```

- 우선순위 = 가까운 순 S1(P0) → S2(P1) → S3(P2) — 상용 기본 배치
  (지시 §3). 기존 `bl_loc`(locality 가중 4/2/1 분산, failover 없음)과는
  다른 것이라 재사용 불가 — 별도 클러스터.
- **overprovisioning factor 기본값 1.4** (설정하지 않음). health(P0) =
  min(100, 1.4·100·healthy/total) — healthy 71.4 % 미만부터 P1 로 흘러넘침
  (v1.39 arch/priority 문서).
- healthy_panic_threshold 기본 50 % (설정하지 않음).
- 기본값에서 벗어난 항목: **없음**.
- ★판단 근거 (overprovisioning 1.4 미설정): 이 값은 "얼마나 무너져야
  다음 우선순위로 넘기나"의 민감도라 **어느 방향으로 조정해도 튜닝 시비가
  성립한다** — 낮추면 "failover 를 쉽게 해 Envoy 를 유리하게", 높이면
  "failover 를 막아 불리하게". 문서 기본값 그대로 두는 것이 유일하게
  방어 가능한 위치고, 결과 해석 시 1.4 라는 값을 명시 인용한다.
- ★문서상 기대 거동 (실측으로 확인, 2026-08-11 스모크): failover 는
  health 기반이라 전원 healthy 상태에서는 **P0(S1)에 100 %** — 스모크
  12/12 요청 전부 S1. 부하로 S1 이 포화해도 HC 가 통과하는 한 넘어가지
  않는다. 이것이 엣지 축 주장("locality 우선은 엣지가 먼저 터진다")의
  측정 대상이다.

## 3. active health check (작업 1-3, `--hc` 변형)

```yaml
      health_checks:
        - timeout: 1s
          interval: 10s
          unhealthy_threshold: 3
          healthy_threshold: 1
          http_health_check:
            path: "/"
          event_logger:
            - name: envoy.health_check.event_sinks.file
              typed_config:
                "@type": type.googleapis.com/envoy.extensions.health_check.event_sinks.file.v3.HealthCheckEventFileSink
                event_log_path: /var/log/envoy/hc_events.log
```

- 적용 범위: `--hc` 렌더 변형에서 **전 클러스터 12개 동일 블록** (삽입 수
  == 클러스터 수를 생성기가 검사). `envoy_keys.json` 의 `active_hc` 필드가
  배포 변형을 기록한다.
- **Envoy 에는 이 4필드의 기본값이 없다** — timeout/interval/
  unhealthy_threshold/healthy_threshold 는 필수 필드 (v1.39 api-v3
  health_check.proto 확인). 따라서 상용 통념값으로 **Kubernetes probe
  기본값**을 채택: periodSeconds 10 → interval 10s, timeoutSeconds 1 →
  timeout 1s, failureThreshold 3 → unhealthy_threshold 3,
  successThreshold 1 → healthy_threshold 1. 단일 출처(k8s 문서 기본값)에
  전부 앵커 — 항목별 짜깁기 없음.
- 감지 시간 기대치(문서 산술): 장애 감지 ≤ 3×10 s + 진행 중 체크 ≈ 30 s,
  복귀 감지 ≤ ~10 s (healthy_threshold 1).
- path "/" 선택 근거: DSB frontend 정적 인덱스 — 200/1507 B, DB 무접촉
  (실측 2026-08-11; /health, /healthz 는 404). 차단 시나리오(포트 DROP)를
  HTTP 체크가 timeout 으로 감지.
- event_logger: 감지·복귀 **시각 실측용 계측** (JSON 이벤트, add/eject
  타임스탬프). 라우팅 거동에는 영향 없음 — 튜닝 아님.
- 문서 확인한 관련 기본값 (설정 안 함): `no_traffic_interval` 60 s (무트래픽
  클러스터는 10s 가 아니라 60s 간격 — 트래픽이 흐르면 즉시 10s 로),
  `reuse_connection` true, 기대 status 200 만 healthy.
- 기본값에서 벗어난 항목: **없음** (4필수값은 k8s 기본값 채택 명기).
- ★판단 근거 (k8s probe 기본값 채택): 필수 4필드에 Envoy 기본값이 없어
  어떤 값을 써도 "선택"이 된다. 선택의 방어선은 (a) **단일 출처** —
  항목별로 유리한 값을 짜깁기하지 않고 한 규약의 기본값 세트를 통째로
  가져온다, (b) **가장 널리 배포된 규약** — k8s probe 기본값(period 10 /
  timeout 1 / failure 3 / success 1)은 상용에서 가장 흔한 헬스체크
  파라미터다. 대안(AWS ALB 30 s/5·2, GCP 5 s/2·2)은 간격·임계가 서로
  달라 어느 쪽이든 "왜 그쪽인가"가 남는데, k8s 를 고른 이유는 배포 수와
  문서 접근성이다. 감지 ~30 s 는 이 세트의 산술적 귀결이지 목표값이
  아니다 — 감지를 빠르게 튜닝하려면 interval 을 내렸을 것이다 (안 했다).
- ★오염 검증 (2026-08-11): HC 65 s 단독 구간에서 front_access.log 증가
  **0 바이트** — HC 는 리스너(HCM)를 지나지 않아 access log 에 안 찍힌다.
  obs.py 표본 오염 원천 차단 (이중 방어: path "/" 는 class_of=None,
  HC 대상 클러스터도 필드10 명시 집합 검사 밖 경로).
- HC 자체 부하: 12 클러스터 × 27 엔드포인트 지형에서 사이트당 ≤ ~1 req/s
  ("/" 정적 응답) — 800 rps 대비 무시 가능. 무트래픽 클러스터는 60 s 간격.

## 4. 배포 변형 운영 규칙

- 변형 2종: `envoy_hc_off.yaml`(기본, b3 거동 + 신규 클러스터 2, HC 없음) /
  `envoy_hc_on.yaml`(전 클러스터 HC). 양쪽 다 `envoy --mode validate` 통과
  (2026-08-11, v1.39.0 컨테이너).
- 교체 절차: 백업 → `/etc/envoy/envoy.yaml` 교체 → `docker restart
  front-envoy` → `/ready`=LIVE → `/clusters` healthy 27/27 → 런타임 routing
  키 84개 확인 → `set_policy(site_s3)` → 사이트 컨테이너 24×3 무재시작 확인.
- config 와 `envoy_keys.json` 은 **같은 렌더 실행 산출물**을 함께 배포한다
  (`active_hc` 필드로 대조).

#!/usr/bin/env python3
"""front Envoy config 생성기 (지시서 v10 STEP T2 + 작업 A 부분집합 클러스터).

단일 weighted_clusters route 를 코호트(2) x 클래스(3) = 6개 route + 폴백으로
확장한다. 매칭은 경로 prefix(클래스) + x-forwarded-for exact(코호트 UE 주소).

UE 주소는 SMF 가 재기동마다 새로 할당하므로 하드코딩하지 않는다 —
/run/tb-cohort.map (.12) 에서 읽어 렌더링한다. 맵이 바뀌면 이 스크립트를
다시 돌려 config 를 재생성해야 한다.

유지하는 것(지시서 §3.2): preserve_external_request_id, use_remote_address,
access log 18필드 µs 포맷, admin 127.0.0.1:9901, 기존 클러스터 6개 정의 전부.

[작업 A] SORTS 허용 집합용 sub_* 클러스터 4개 추가 (LEAST_REQUEST,
choice_count 기본 2 = P2C). 단일 원소 집합은 기존 site_s* 를 그대로 쓴다.
bl_lr 은 기능이 같아 보여도 재사용하지 않는다 — 로그 필드10 에서 SORTS 와
비교군을 구분해야 한다.

★ 키 목록 단일 출처: config 를 렌더할 때 같은 실행에서 envoy_keys.json 을
같은 디렉터리에 뱉는다. run_all.py(set_policy/precheck)와 sorts_ctl.py(apply),
obs.py(관측 필터)는 전부 이 산출물을 읽는다 — 목록을 각자 하드코딩하면
7-prefix 무시 사고(조용한 무시)가 재발한다. 손으로 맞추지 마라.

사용:
  python3 gen_envoy_v10.py            # /run/tb-cohort.map 읽어 stdout 으로
  python3 gen_envoy_v10.py --c1 10.46.0.6 --c2 10.46.0.7   # 주소 직접 지정
"""
import argparse
import json
import os
import subprocess
import sys

LOADGEN = "192.168.0.12"
SITE_IP = {"S1": "192.168.0.3", "S2": "192.168.0.2", "S3": "192.168.0.40"}
# 부분집합 클러스터 (작업 A §2.1). 이름 규약: sub_s<번호 오름차순>.
SUBSETS = {
    "sub_s23": ("S2", "S3"),
    "sub_s13": ("S1", "S3"),
    "sub_s12": ("S1", "S2"),
    "sub_s123": ("S1", "S2", "S3"),
}
CLUSTERS = (["site_s1", "site_s2", "site_s3"] + list(SUBSETS)
            + ["bl_rr", "bl_lr", "bl_loc"])
# 클러스터 -> 엔드포인트 사이트 (키 산출물·precheck 엔드포인트 수 계산용)
CLUSTER_SITES = {
    "site_s1": ("S1",), "site_s2": ("S2",), "site_s3": ("S3",),
    **SUBSETS,
    "bl_rr": ("S1", "S2", "S3"), "bl_lr": ("S1", "S2", "S3"),
    "bl_loc": ("S1", "S2", "S3"),
}
# SORTS 가 라우팅에 쓰는 클러스터 (관측 필터의 허용 집합. bl_* 는 계속 배제)
SORTS_CLUSTERS = ["site_s1", "site_s2", "site_s3"] + list(SUBSETS)
ROUTE_PREFIXES = ["c1_search", "c1_reserve", "c1_recommend",
                  "c2_search", "c2_reserve", "c2_recommend", "fallback"]
# 허용 집합("S2|S3" 정렬·파이프 구분) -> 클러스터 이름
SUBSET_CLUSTER_OF = {
    "S1": "site_s1", "S2": "site_s2", "S3": "site_s3",
    **{"|".join(sorted(v)): k for k, v in SUBSETS.items()},
}

KEYS_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "envoy_keys.json")


def write_keys():
    """config 와 같은 실행에서 키 목록 산출물을 뱉는다 (단일 출처)."""
    obj = {
        "generated_by": "gen_envoy_v10.py",
        "route_prefixes": ROUTE_PREFIXES,
        "cluster_keys": CLUSTERS,
        "cluster_sites": {k: list(v) for k, v in CLUSTER_SITES.items()},
        "sorts_clusters": SORTS_CLUSTERS,
        "subset_cluster_of": SUBSET_CLUSTER_OF,
        "site_ip": SITE_IP,
    }
    with open(KEYS_OUT, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return KEYS_OUT
# (runtime prefix 이름, 코호트, 경로 prefix)
UNITS = [
    ("c1_search", 1, "/hotels"),
    ("c1_reserve", 1, "/reservation"),
    ("c1_recommend", 1, "/recommendations"),
    ("c2_search", 2, "/hotels"),
    ("c2_reserve", 2, "/reservation"),
    ("c2_recommend", 2, "/recommendations"),
]


def cohort_ips():
    txt = subprocess.run(["ssh", LOADGEN, f"cat /run/tb-cohort.map"],
                         capture_output=True, text=True, timeout=30).stdout
    m = {}
    for line in txt.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            p = line.split("\t")
            if len(p) == 3:
                m[int(p[0])] = p[2]
    return m


def weights_yaml(indent):
    pad = " " * indent
    out = []
    for c in CLUSTERS:
        w = 100 if c == "site_s3" else 0
        out.append(f"{pad}- name: {c}\n{pad}  weight: {w}")
    return "\n".join(out)


def subset_clusters_yaml():
    out = []
    for name, sites in SUBSETS.items():
        eps = "\n".join(
            f"""              - endpoint:
                  address:
                    socket_address: {{ address: {SITE_IP[s]}, port_value: 5000 }}"""
            for s in sites)
        out.append(f"""    - name: {name}
      type: STATIC
      connect_timeout: 1s
      # choice_count 는 기본값 2 (P2C) 를 쓴다. 지정하지 않는다.
      lb_policy: LEAST_REQUEST
      load_assignment:
        cluster_name: {name}
        endpoints:
          - lb_endpoints:
{eps}""")
    return "\n".join(out)


def route_yaml(prefix_key, path_prefix, xff):
    return f"""                        - match:
                            prefix: "{path_prefix}"
                            headers:
                              - name: x-forwarded-for
                                string_match: {{ exact: "{xff}" }}
                          route:
                            timeout: 5s
                            weighted_clusters:
                              runtime_key_prefix: routing.{prefix_key}
                              clusters:
{weights_yaml(32)}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--c1")
    ap.add_argument("--c2")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    if a.c1 and a.c2:
        ips = {1: a.c1, 2: a.c2}
    else:
        ips = cohort_ips()
    if 1 not in ips or 2 not in ips:
        sys.exit(f"FATAL: 코호트 맵에 1/2 없음: {ips}")

    routes = []
    for key, cohort, path in UNITS:
        routes.append(route_yaml(key, path, ips[cohort]))
    # 폴백은 반드시 마지막. UE 경로가 아닌 트래픽(관리용 LAN curl 등)이 온다.
    routes.append(f"""                        - match: {{ prefix: "/" }}
                          route:
                            timeout: 5s
                            weighted_clusters:
                              runtime_key_prefix: routing.fallback
                              clusters:
{weights_yaml(32)}""")

    static_runtime = []
    for key, _, _ in UNITS + [("fallback", 0, "/")]:
        static_runtime.append(f"          {key}:")
        for c in CLUSTERS:
            static_runtime.append(f"            {c}: {100 if c == 'site_s3' else 0}")

    cfg = f"""# 생성: gen_envoy_v10.py (지시서 v10 T2). 손으로 고치지 말 것 — 재생성하라.
# 코호트 UE 주소: c1={ips[1]} c2={ips[2]} (/run/tb-cohort.map)
admin:
  address:
    socket_address: {{ address: 127.0.0.1, port_value: 9901 }}

layered_runtime:
  layers:
    - name: static_base
      static_layer:
        routing:
{chr(10).join(static_runtime)}
    - name: admin_layer
      admin_layer: {{}}

static_resources:
  listeners:
    - name: front_listener
      address:
        socket_address: {{ address: 0.0.0.0, port_value: 8080 }}
      filter_chains:
        - filters:
            - name: envoy.filters.network.http_connection_manager
              typed_config:
                "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
                stat_prefix: front
                use_remote_address: true
                generate_request_id: true
                preserve_external_request_id: true
                access_log:
                  - name: envoy.access_loggers.file
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.access_loggers.file.v3.FileAccessLog
                      path: /var/log/envoy/front_access.log
                      log_format:
                        text_format_source:
                          # 1-15 는 고정. 파서가 인덱스로 읽으므로 새 필드는 반드시 뒤에만 붙인다.
                          # 16-18 은 마이크로초 정밀도. %DURATION% 는 정수 ms 라
                          # LAN 무부하 구간에서 전부 0 이 되어 측정이 불가능하다 (2026-08-02 실측).
                          inline_string: "%START_TIME(%s.%6f)%,%REQ(X-REQUEST-ID)%,%REQ(:METHOD)%,%REQ(X-ENVOY-ORIGINAL-PATH?:PATH)%,%RESPONSE_CODE%,%RESPONSE_FLAGS%,%DURATION%,%REQUEST_DURATION%,%RESPONSE_DURATION%,%UPSTREAM_CLUSTER%,%UPSTREAM_HOST%,%DOWNSTREAM_REMOTE_ADDRESS_WITHOUT_PORT%,%REQ(X-FORWARDED-FOR)%,%BYTES_RECEIVED%,%BYTES_SENT%,%COMMON_DURATION(DS_RX_BEG:DS_TX_END:us)%,%COMMON_DURATION(US_CX_BEG:US_CX_END:us)%,%COMMON_DURATION(US_TX_BEG:US_RX_END:us)%\\n"
                route_config:
                  name: front_route
                  virtual_hosts:
                    - name: all_sites
                      domains: ["*"]
                      routes:
{chr(10).join(routes)}
                http_filters:
                  - name: envoy.filters.http.router
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router

  clusters:
    - name: site_s1
      type: STATIC
      connect_timeout: 1s
      lb_policy: ROUND_ROBIN
      load_assignment:
        cluster_name: site_s1
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address: {{ address: 192.168.0.3, port_value: 5000 }}
    - name: site_s2
      type: STATIC
      connect_timeout: 1s
      lb_policy: ROUND_ROBIN
      load_assignment:
        cluster_name: site_s2
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address: {{ address: 192.168.0.2, port_value: 5000 }}
    - name: site_s3
      type: STATIC
      connect_timeout: 1s
      lb_policy: ROUND_ROBIN
      load_assignment:
        cluster_name: site_s3
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address: {{ address: 192.168.0.40, port_value: 5000 }}

    # ---------------- 비교군 클러스터 ----------------
    # 기존 site_s1/s2/s3 는 엔드포인트가 1개뿐이라 lb_policy 가 무의미하다.
    # RR/LEAST_REQUEST 는 한 클러스터 "안의" 여러 엔드포인트 사이에서 동작한다.
    - name: bl_rr
      type: STATIC
      connect_timeout: 1s
      lb_policy: ROUND_ROBIN
      load_assignment:
        cluster_name: bl_rr
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address: {{ address: 192.168.0.3, port_value: 5000 }}
              - endpoint:
                  address:
                    socket_address: {{ address: 192.168.0.2, port_value: 5000 }}
              - endpoint:
                  address:
                    socket_address: {{ address: 192.168.0.40, port_value: 5000 }}
    - name: bl_lr
      type: STATIC
      connect_timeout: 1s
      # choice_count 는 기본값 2 (P2C) 를 쓴다. 지정하지 않는다.
      lb_policy: LEAST_REQUEST
      load_assignment:
        cluster_name: bl_lr
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address: {{ address: 192.168.0.3, port_value: 5000 }}
              - endpoint:
                  address:
                    socket_address: {{ address: 192.168.0.2, port_value: 5000 }}
              - endpoint:
                  address:
                    socket_address: {{ address: 192.168.0.40, port_value: 5000 }}
    # ---------------- 부분집합 클러스터 (작업 A) ----------------
    # SORTS 허용 집합용. 집합 내 분배는 LEAST_REQUEST (choice_count 기본 2
    # = P2C — 보고에 명시할 것). bl_lr 과 기능이 겹쳐 보여도 별도 이름을
    # 유지한다: 필드10 으로 SORTS/비교군을 구분해야 한다.
{subset_clusters_yaml()}
    # 엣지 우선. locality 가중치 S1 4 / S2 2 / S3 1 -> 기대 57 / 29 / 14 %
    - name: bl_loc
      type: STATIC
      connect_timeout: 1s
      lb_policy: ROUND_ROBIN
      common_lb_config:
        locality_weighted_lb_config: {{}}
      load_assignment:
        cluster_name: bl_loc
        endpoints:
          - locality: {{ zone: s1 }}
            load_balancing_weight: 4
            lb_endpoints:
              - endpoint:
                  address:
                    socket_address: {{ address: 192.168.0.3, port_value: 5000 }}
          - locality: {{ zone: s2 }}
            load_balancing_weight: 2
            lb_endpoints:
              - endpoint:
                  address:
                    socket_address: {{ address: 192.168.0.2, port_value: 5000 }}
          - locality: {{ zone: s3 }}
            load_balancing_weight: 1
            lb_endpoints:
              - endpoint:
                  address:
                    socket_address: {{ address: 192.168.0.40, port_value: 5000 }}
"""
    kp = write_keys()
    print(f"-> {kp} (클러스터 {len(CLUSTERS)}개, prefix {len(ROUTE_PREFIXES)}개, "
          f"런타임 키 {len(CLUSTERS) * len(ROUTE_PREFIXES)}개)", file=sys.stderr)
    if a.out:
        open(a.out, "w").write(cfg)
        print(f"-> {a.out} (c1={ips[1]} c2={ips[2]})", file=sys.stderr)
    else:
        sys.stdout.write(cfg)


if __name__ == "__main__":
    main()

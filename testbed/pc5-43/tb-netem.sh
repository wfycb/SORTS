#!/usr/bin/env bash
# front Envoy -> 사이트 경로 지연 주입. apply | clear | show
#
# 정의 (지시서 6.1): d_net = E2E 응답시간에 더해지는 경로 지연 총량.
# netem 을 opti-9010 의 송신(egress) 방향에만 걸면 요청 방향에만 지연이 붙고
# 응답 방향은 안 붙으므로, 왕복 총 증가분 = 주입값이 된다.
#
#   주입값(i) = 목표 d_net(i) - 실측 기본 RTT(i, 10KB 페이로드)
#
# 2026-08-02 실측 RTT (.43 에서 root, -i 0.01 -c 100, -s 10000):
#   S1 192.168.0.3  avg 1.818 ms   -> 2  - 1.818 = 0.182 ms
#   S3 192.168.0.40 avg 0.292 ms   -> 25 - 0.292 = 24.708 ms
# 2026-08-05 지시서 v10: S2 목표 10 -> 15 ms (계단 극단 계층 복원, v10 §0.1).
#   재실측 RTT (root, -i 0.01 -c 100, -s 10000 — 구 관례와 동일): S2 avg 0.280 ms
#   -> 15 - 0.280 = 14.720 ms
#   (1차 시도 14.571ms 는 user -i 0.2 실측 avg 0.429 가 노이즈로 부풀려진 값이라 폐기)
# S1 의 큰 기본 RTT 는 100 Mb/s 링크의 10KB 직렬화(왕복 약 1.6 ms)다.
set -euo pipefail

IFACE="${IFACE:-eno1}"
S1_IP=192.168.0.3
S2_IP=192.168.0.2
S3_IP=192.168.0.40
S1_DELAY="${S1_DELAY:-0.182ms}"
S2_DELAY="${S2_DELAY:-14.720ms}"
S3_DELAY="${S3_DELAY:-24.708ms}"

apply() {
  tc qdisc del dev "$IFACE" root 2>/dev/null || true
  # priomap 전부 0 -> 미매칭 트래픽(N3 GTP, ssh 등)은 밴드 1:1(무지연)
  tc qdisc add dev "$IFACE" root handle 1: prio bands 4 \
     priomap 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
  tc qdisc add dev "$IFACE" parent 1:2 handle 20: netem delay "$S1_DELAY"
  tc qdisc add dev "$IFACE" parent 1:3 handle 30: netem delay "$S2_DELAY"
  tc qdisc add dev "$IFACE" parent 1:4 handle 40: netem delay "$S3_DELAY"
  tc filter add dev "$IFACE" protocol ip parent 1: prio 1 u32 \
     match ip dst "$S1_IP"/32 flowid 1:2
  tc filter add dev "$IFACE" protocol ip parent 1: prio 1 u32 \
     match ip dst "$S2_IP"/32 flowid 1:3
  tc filter add dev "$IFACE" protocol ip parent 1: prio 1 u32 \
     match ip dst "$S3_IP"/32 flowid 1:4
}

clear_all() { tc qdisc del dev "$IFACE" root 2>/dev/null || true; }
show() { tc qdisc show dev "$IFACE"; tc filter show dev "$IFACE"; }

case "${1:-show}" in
  apply) apply; show ;;
  clear) clear_all; show ;;
  show)  show ;;
  *) echo "usage: $0 {apply|clear|show}" >&2; exit 2 ;;
esac

#!/usr/bin/env bash
# UE 하향 무선 열화 주입 (.43 ogstun egress).
#   apply <cohort1_spec> <cohort2_spec> | clear | show
# spec 예: "rate 2300kbit"  "loss 2.5%"  "rate 2300kbit loss 2.5%"  "none"
#
# 왜 ogstun egress 인가:
#   응답 경로가 front Envoy -> (커널 라우팅) -> ogstun -> UPF -> GTP -> gNB -> UE 다.
#   ogstun egress 가 UE 하향의 셰이핑 지점이다. uesimtunN egress 는 상향만
#   제한하므로 응답 크기를 흔들지 못한다 (2026-08-02 실측: search 4474B 와
#   recommend 200B 의 d_acc 가 거의 같게 나옴).
#   ogstun 에는 UE 트래픽만 흐르므로 N3 GTP/ssh 누수 위험이 eno1 보다 낮다.
set -euo pipefail

IFACE=ogstun
C1_IP="${C1_IP:?코호트1 UE 주소 필요}"
C2_IP="${C2_IP:?코호트2 UE 주소 필요}"

apply() {
  local spec1="$1" spec2="$2"
  tc qdisc del dev "$IFACE" root 2>/dev/null || true
  # priomap 전부 0 -> 미매칭 트래픽은 밴드 1:1(무처리)
  tc qdisc add dev "$IFACE" root handle 1: prio bands 3 \
     priomap 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
  if [ "$spec1" != "none" ]; then
    tc qdisc add dev "$IFACE" parent 1:2 handle 20: netem $spec1
    tc filter add dev "$IFACE" protocol ip parent 1: prio 1 u32 \
       match ip dst "$C1_IP"/32 flowid 1:2
  fi
  if [ "$spec2" != "none" ]; then
    tc qdisc add dev "$IFACE" parent 1:3 handle 30: netem $spec2
    tc filter add dev "$IFACE" protocol ip parent 1: prio 1 u32 \
       match ip dst "$C2_IP"/32 flowid 1:3
  fi
}

clear_all() { tc qdisc del dev "$IFACE" root 2>/dev/null || true; }
show() { tc qdisc show dev "$IFACE"; tc filter show dev "$IFACE"; }

case "${1:-show}" in
  apply) apply "${2:?}" "${3:?}"; show ;;
  clear) clear_all; show ;;
  show)  show ;;
  *) echo "usage: $0 {apply <spec1> <spec2>|clear|show}" >&2; exit 2 ;;
esac

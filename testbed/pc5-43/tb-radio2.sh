#!/usr/bin/env bash
# UE 하향 무선 열화 주입 v2 — **커넥션(=가상 사용자) 단위** 셰이핑.
#   apply <cohort1_spec> <cohort2_spec> | clear | show | classes
# spec 예: "rate 2300kbit"  "none"
#
# ── v1 이 왜 틀렸나 ────────────────────────────────────────────────────────
# v1 은 코호트 전체를 netem 하나에 태웠다. §0.3 밴드의 d_acc 는 "요청 1건을
# 고립해서 잰 직렬화 시간"인데, 코호트당 700 rps 의 하향 요구량은 8.89 Mbit/s
# 라 Poor(2.3M)/Fair(4.5M)/극단(1.6M) 에서 링크가 포화한다. 실측: 코호트1
# 처리량 8.895 -> 2.074 Mbit/s, 완료율 700 -> 163/s, corrected p50 이 전
# 엔드포인트에서 22초. 클래스별 triage 가 아니라 코호트 전체 붕괴였다.
#
# ── v2 의 모형 ────────────────────────────────────────────────────────────
# 밴드는 원래 "사용자 1명의 무선 링크"를 모형화한 것이다. 부하 생성기는
# 코호트당 28개 keepalive 커넥션을 유지하고 **워커가 커넥션당 요청 1건만
# in-flight** 로 돌리므로, 커넥션 1개 = 가상 사용자 1명으로 보는 것이 옳다.
#   커넥션당 요구량 = 8.89 / 28 = 0.317 Mbit/s
#     극단 1.6 -> rho 0.20 / Poor 2.3 -> 0.14 / Fair 4.5 -> 0.07
# rho 가 낮으므로 대기시간이 무시되고 d_acc 가 §0.3 값(직렬화)으로 남는다.
#
# ── 왜 HTB rate 가 아니라 leaf netem rate 인가 ────────────────────────────
# HTB 의 rate 는 토큰버킷이라 burst 만큼은 즉시 통과시킨다. 커넥션이 요청
# 사이에 놀아서(rho 0.14) 토큰이 항상 가득 차므로 4474B search 응답이
# 통째로 즉시 나가버려 d_acc 가 0 이 된다. netem 의 rate 는 패킷마다
# len*8/rate 를 더하는 순수 직렬화 모형이라 §0.3 표를 만든 그 primitive 다.
# 그래서 HTB 는 **분류 컨테이너로만** 쓰고(leaf 는 무제한), 실제 셰이핑은
# 각 leaf 에 매단 netem 이 한다.
#
# ── 분류 키 ───────────────────────────────────────────────────────────────
# 셰이핑 지점이 ogstun **egress**(하향)이므로 커넥션 식별자는 소스 포트가
# 아니라 **dst 포트**(UE 의 ephemeral 포트)다. 실측 확인:
#   192.168.0.43.8080 > 10.46.0.6.37589  (커넥션마다 고유·수명 내내 고정)
# ogstun 은 tun(pi off) 이라 IP 헤더가 오프셋 0 이고, u32 의 at 오프셋은
# 네트워크 헤더 기준이다. at 20 = [sport(2) | dport(2)] 워드.
set -euo pipefail

IFACE=ogstun
DIVISOR="${TB_RADIO_DIVISOR:-64}"      # 코호트당 버킷(=가상 사용자 슬롯) 수
C1_IP="${C1_IP:?코호트1 UE 주소 필요}"
C2_IP="${C2_IP:?코호트2 UE 주소 필요}"
MASK=$(printf '0x%08x' $((DIVISOR - 1)))

# spec 에서 rate 만 뽑는다. §0.3/§8.3 에 따라 loss 는 쓰지 않는다.
parse_rate() {
  local s="$1"
  [ "$s" = "none" ] && { echo ""; return; }
  if [[ "$s" =~ ^rate[[:space:]]+([0-9]+[a-zA-Z]*)$ ]]; then
    echo "${BASH_REMATCH[1]}"
  else
    echo "지원하지 않는 spec: '$s' (rate <X>kbit 또는 none)" >&2; exit 2
  fi
}

setup_cohort() {
  local idx="$1" ip="$2" rate="$3"
  [ -z "$rate" ] && return 0
  local base=$((idx * 0x1000))          # 1:1000.. / 1:2000..
  local ht=$((idx * 16))                # 해시테이블 핸들 16: / 32:
  # 분류용 leaf class (무제한) + 실제 셰이핑을 하는 netem
  local i cid
  for ((i = 0; i < DIVISOR; i++)); do
    cid=$((base + i))
    tc class add dev "$IFACE" parent 1: classid 1:$(printf '%x' $cid) \
       htb rate 10000mbit ceil 10000mbit quantum 1400
    tc qdisc add dev "$IFACE" parent 1:$(printf '%x' $cid) \
       handle $(printf '%x' $cid): netem rate "$rate"
  done
  # dst IP 로 거른 뒤 dst 포트 하위비트로 해싱
  tc filter add dev "$IFACE" parent 1: prio "$idx" handle ${ht}: protocol ip u32 \
     divisor "$DIVISOR"
  tc filter add dev "$IFACE" parent 1: prio "$idx" protocol ip u32 \
     match ip dst "$ip"/32 hashkey mask "$MASK" at 20 link ${ht}:
  for ((i = 0; i < DIVISOR; i++)); do
    cid=$((base + i))
    tc filter add dev "$IFACE" parent 1: prio "$idx" protocol ip u32 \
       ht ${ht}:$(printf '%x' $i): match ip dst "$ip"/32 \
       flowid 1:$(printf '%x' $cid)
  done
}

apply() {
  local r1 r2
  r1=$(parse_rate "$1"); r2=$(parse_rate "$2")
  tc qdisc del dev "$IFACE" root 2>/dev/null || true
  # default 9999 = 미매칭(코호트 외 트래픽, none 인 코호트) -> 무제한
  tc qdisc add dev "$IFACE" root handle 1: htb default 9999
  tc class add dev "$IFACE" parent 1: classid 1:9999 \
     htb rate 10000mbit ceil 10000mbit quantum 1400
  setup_cohort 1 "$C1_IP" "$r1"
  setup_cohort 2 "$C2_IP" "$r2"
}

clear_all() { tc qdisc del dev "$IFACE" root 2>/dev/null || true; }
show() { tc qdisc show dev "$IFACE" | head -5; echo "..."; tc filter show dev "$IFACE" | head -6; }
classes() {
  # 버킷별 실제 트래픽 분포 — 커넥션이 고르게 흩어졌는지 본다.
  tc -s class show dev "$IFACE" \
    | awk '/^class htb/{c=$3} /Sent/{if ($2+0 > 0) print c, $2" bytes", $4" pkt"}'
}

case "${1:-show}" in
  apply)   apply "${2:?}" "${3:?}"; ;;
  clear)   clear_all ;;
  show)    show ;;
  classes) classes ;;
  *) echo "usage: $0 {apply <spec1> <spec2>|clear|show|classes}" >&2; exit 2 ;;
esac

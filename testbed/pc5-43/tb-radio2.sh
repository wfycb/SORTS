#!/usr/bin/env bash
# UE 하향 무선 열화 주입 v4 — **커넥션(=가상 사용자) 단위** 셰이핑, N 코호트.
#   applyn <ip1,ip2,...> <spec1> ... <specN>   (N 코호트 — STAGE3+)
#   apply  <spec1> <spec2>                     (C1_IP/C2_IP env, v3 하위 호환)
#   clear | show | classes
# spec 예: "rate 2300kbit"  "none"
#
# ── v3 -> v4 (2026-08-12, STAGE3 코호트 확장) ─────────────────────────────
# 코호트 열거 일반화. IPs 는 **argv**(applyn) 로 받는다 — sudo 가 env 를
# 스트립하고 sudoers env_keep 은 C1_IP/C2_IP 만 등록돼 있어서 env 방식은
# COHORT_IPS 가 조용히 사라진다(실측). v4 는 env_keep 목록을 늘리지 않는다.
# classid 규약 cN = N*0x1000 (N<=8; default 1:9999 는 coh 9 라 충돌 없음),
# 필터 prio=N, 해시테이블 핸들 N*16. classes->filters->netem 순서(가시화==
# 발효, I-16 해소)와 tc -batch 단일 호출은 v3 그대로.
#
# ── v2 -> v3 (2026-08-11, STAGE2 §4 / ISSUES I-16) ─────────────────────────
# v2 는 tc 를 194회 개별 호출(0.86 s), leaf -> filter 순서라 컨트롤러가
# "아무 패킷도 셰이핑되지 않은 상태"에서 밴드를 봤다(감지 +0.307 < 필터
# +0.52 < 발효 +0.60 s). v3 = tc -batch 단일 호출(~10 ms) + classes ->
# filters -> netem 재배열로 가시화 == 발효.
#
# ── v1 이 왜 틀렸나 ────────────────────────────────────────────────────────
# v1 은 코호트 전체를 netem 하나에 태웠다. §0.3 밴드의 d_acc 는 "요청 1건을
# 고립해서 잰 직렬화 시간"인데, 코호트당 700 rps 의 하향 요구량은 8.89 Mbit/s
# 라 Poor(2.3M)/Fair(4.5M)/극단(1.6M) 에서 링크가 포화한다. 실측: 코호트1
# 처리량 8.895 -> 2.074 Mbit/s, 완료율 700 -> 163/s. 코호트 전체 붕괴였다.
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
# 아니라 **dst 포트**(UE 의 ephemeral 포트)다. ogstun 은 tun(pi off) 이라
# IP 헤더가 오프셋 0 이고, u32 의 at 20 = [sport(2) | dport(2)] 워드.
set -euo pipefail

IFACE=ogstun
DIVISOR="${TB_RADIO_DIVISOR:-64}"      # 코호트당 버킷(=가상 사용자 슬롯) 수
MASK=$(printf '0x%08x' $((DIVISOR - 1)))
IPS=()
NCOH=0
BF=""

set_ips() {                            # apply 계열에서만 호출 (clear 는 불필요)
  IFS=',' read -r -a IPS <<< "$1"
  NCOH=${#IPS[@]}
  if [ "$NCOH" -lt 1 ] || [ "$NCOH" -gt 8 ]; then
    echo "코호트 수 범위 밖: $NCOH (1~8)" >&2; exit 2
  fi
}

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

# 배치 명령을 표준출력으로 낸다 (최종 상태는 v2/v3 과 동일 규약).
emit_cohort() {
  local idx="$1" ip="$2" rate="$3"
  [ -z "$rate" ] && return 0
  local base=$((idx * 0x1000))          # 1:1000.. ~ 1:6000..
  local ht=$((idx * 16))                # 해시테이블 핸들 16:/32:/48:/...
  local i cid
  # (1) 분류용 leaf class (무제한)
  for ((i = 0; i < DIVISOR; i++)); do
    cid=$((base + i))
    printf 'class add dev %s parent 1: classid 1:%x htb rate 10000mbit ceil 10000mbit quantum 1400\n' \
      "$IFACE" "$cid"
  done
  # (2) dst IP 로 거른 뒤 dst 포트 하위비트로 해싱 — **netem 보다 먼저**
  printf 'filter add dev %s parent 1: prio %d handle %d: protocol ip u32 divisor %d\n' \
    "$IFACE" "$idx" "$ht" "$DIVISOR"
  printf 'filter add dev %s parent 1: prio %d protocol ip u32 match ip dst %s/32 hashkey mask %s at 20 link %d:\n' \
    "$IFACE" "$idx" "$ip" "$MASK" "$ht"
  for ((i = 0; i < DIVISOR; i++)); do
    cid=$((base + i))
    printf 'filter add dev %s parent 1: prio %d protocol ip u32 ht %d:%x: match ip dst %s/32 flowid 1:%x\n' \
      "$IFACE" "$idx" "$ht" "$i" "$ip" "$cid"
  done
  # (3) 실제 셰이핑 — 이 시점부터 가시화 == 발효
  for ((i = 0; i < DIVISOR; i++)); do
    cid=$((base + i))
    printf 'qdisc replace dev %s parent 1:%x handle %x: netem rate %s\n' \
      "$IFACE" "$cid" "$cid" "$rate"
  done
}

apply() {
  local i sp
  if [ "$#" -ne "$NCOH" ]; then
    echo "spec $#개 != 코호트 $NCOH개" >&2; exit 2
  fi
  BF=$(mktemp /var/tmp/tb-radio2.batch.XXXXXX)
  trap 'rm -f "${BF:-}"' EXIT
  {
    # default 9999 = 미매칭(코호트 외 트래픽, none 인 코호트) -> 무제한
    printf 'qdisc add dev %s root handle 1: htb default 9999\n' "$IFACE"
    printf 'class add dev %s parent 1: classid 1:9999 htb rate 10000mbit ceil 10000mbit quantum 1400\n' "$IFACE"
    for ((i = 1; i <= NCOH; i++)); do
      sp=$(parse_rate "${!i}")
      emit_cohort "$i" "${IPS[i-1]}" "$sp"
    done
  } > "$BF"
  # del 은 배치 밖 — 빈 상태에서 실패하면 -batch 가 첫 줄에서 중단된다.
  tc qdisc del dev "$IFACE" root 2>/dev/null || true
  tc -batch "$BF"          # 전 명령 성공해야 0 (실패 시 set -e 로 중단)
}

clear_all() { tc qdisc del dev "$IFACE" root 2>/dev/null || true; }
show() { tc qdisc show dev "$IFACE" | head -5; echo "..."; tc filter show dev "$IFACE" | head -6; }
classes() {
  # 버킷별 실제 트래픽 분포 — 커넥션이 고르게 흩어졌는지 본다.
  tc -s class show dev "$IFACE" \
    | awk '/^class htb/{c=$3} /Sent/{if ($2+0 > 0) print c, $2" bytes", $4" pkt"}'
}

case "${1:-show}" in
  apply)   set_ips "${C1_IP:?C1_IP 필요(v3 호환 모드)},${C2_IP:?C2_IP 필요(v3 호환 모드)}"
           shift; apply "$@" ;;
  applyn)  set_ips "${2:?ip1,ip2,... 필요}"; shift 2; apply "$@" ;;
  clear)   clear_all ;;
  show)    show ;;
  classes) classes ;;
  *) echo "usage: $0 {applyn <ips_csv> <spec...>|apply <spec1> <spec2>|clear|show|classes}" >&2; exit 2 ;;
esac

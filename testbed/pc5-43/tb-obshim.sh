#!/usr/bin/env bash
# tb-obshim — STAGE4 관측 감쇠 shim 제어 래퍼 (sudoers NOPASSWD 대상).
#
#   setup                                  더미 obsshim0 생성·up
#   start <delay> <avg> <disc> <eps> <seed>  shim 데몬 기동 (인자 전부 형식 검증)
#   stop                                   데몬 정지 (pidfile — I-15)
#   teardown                               stop + 더미 제거
#   status
#
# ── 안전 규약 (STAGE4 G1 회신 §1.2) ─────────────────────────────────────
# 1. 인터페이스는 **인자로 받지 않는다** — SRC/DST 하드코딩. 화이트리스트의
#    최강형: 우회할 인자 자체가 없다. (--dst 유사 인자가 어디서 오든
#    obs_shim.py 호출은 아래 고정 argv 로만 구성된다.)
# 2. 전 인자 형식 검증(정규식) 후 **고정 배열 argv** 로만 전달 — 문자열
#    조립·eval 없음. 검증 실패는 즉시 거부 + stderr 로그 + exit 2.
# 3. SRC(ogstun)는 읽기 전용(tc show)이며 이 스크립트는 SRC 에 어떤 tc
#    조작도 하지 않는다. tc 쓰기는 DST(obsshim0) 한정 (obs_shim.py 내부).
set -euo pipefail

DST=obsshim0                       # 고정 — 변경하려면 이 파일(root 소유) 수정
SRC=ogstun                         # 고정 — 읽기 전용
PIDFILE=/var/tmp/obs_shim.pid
SHIM=/usr/local/sbin/obs_shim.py   # root 소유 (sudo 로 도는 코드는 user-writable 금지)
LOG=/var/tmp/obs_shim.log

die() { echo "tb-obshim: 거부: $*" >&2; exit 2; }

setup() {
  ip link add "$DST" type dummy 2>/dev/null || true
  ip link set "$DST" up
  echo "setup OK ($DST)"
}

stop_() {
  if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "stopped"
  else
    echo "not running"
  fi
}

teardown() {
  stop_
  ip link del "$DST" 2>/dev/null || true
  echo "teardown OK"
}

start_() {
  local delay="$1" avg="$2" disc="$3" eps="$4" seed="$5"
  # 형식 검증 — 전부 화이트리스트 정규식. 하나라도 어긋나면 거부.
  [[ "$delay" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "delay 형식 ($delay)"
  awk -v d="$delay" 'BEGIN{exit !(d>=0 && d<=10)}' || die "delay 범위 0~10 ($delay)"
  [[ "$avg" =~ ^[01]$ ]] || die "avg 는 0|1 ($avg)"
  if [ "$disc" != "-" ]; then
    [[ "$disc" =~ ^[0-9]{3,6}(,[0-9]{3,6})*$ ]] || die "disc 형식 ($disc)"
  fi
  [[ "$eps" =~ ^0(\.[0-9]+)?$ ]] || die "eps 형식 0~1 미만 ($eps)"
  [[ "$seed" =~ ^[0-9]{1,6}$ ]] || die "seed 형식 ($seed)"
  ip link show "$DST" >/dev/null 2>&1 || die "$DST 없음 — setup 먼저"

  stop_ >/dev/null || true
  local args=(python3 "$SHIM" --src "$SRC" --dst "$DST" --n-cohorts 6
              --poll 0.02 --pidfile "$PIDFILE" --delay "$delay"
              --noise "$eps" --noise-seed "$seed")
  [ "$avg" = "1" ] && args+=(--average)
  [ "$disc" != "-" ] && args+=(--discretize "$disc")
  nohup "${args[@]}" > "$LOG" 2>&1 &
  echo "started pid=$! (delay=$delay avg=$avg disc=$disc eps=$eps seed=$seed)"
}

case "${1:-status}" in
  setup)    setup ;;
  start)    [ "$#" -eq 6 ] || die "start 인자 5개 필요 (delay avg disc eps seed)"
            start_ "$2" "$3" "$4" "$5" "$6" ;;
  stop)     stop_ ;;
  teardown) teardown ;;
  status)   ip link show "$DST" 2>/dev/null | head -1 || echo "$DST 없음"
            [ -f "$PIDFILE" ] && echo "pid $(cat "$PIDFILE")" || echo "shim 정지" ;;
  *) die "지원 안 함: ${1:-}" ;;
esac

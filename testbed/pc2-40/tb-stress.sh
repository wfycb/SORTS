#!/usr/bin/env bash
# 사이트별 서버 교란.  tb-stress.sh [SITE] {start [지속초] | stop | status}
#
# SITE 를 생략하면 S3 다 (구 동작 유지 — run_all.py 가 인자 없이 부른다).
#
# 왜 stress-ng 를 사이트 로컬에서 직접 도는가:
#   네트워크를 쓰지 않고 front Envoy 를 거치지 않으므로, 비교군 LB 가 큐 길이나
#   활성 요청 수로 "미리" 감지할 수 없는 순수 서버측 열화가 된다.
#   DSB 컨테이너가 핀된 cpuset 과 같은 곳에 핀해야 f_c 가 오른다.
#
# 사이트마다 cpuset 이 다르다 (2026-08-04 실측):
#   S1(.3)  cpuset 0     = 1코어   <- S3 와 같은 강도를 쓰면 즉사한다
#   S2(.2)  cpuset 0-1   = 2코어
#   S3(.40) cpuset 0-3   = 4코어
# 호스트는 셋 다 nproc=6.
#
# 원격 사이트에는 아무것도 설치하지 않는다. ssh 로 인라인 실행한다
#   (stress-ng 는 세 사이트에 이미 있다. 2026-08-04 확인).
#
# ---------------------------------------------------------------------------
# 강도 캘리브레이션
#
# [구] 2026-08-03~04, S3 전용, 800 rps · site_s3 · f_c(Envoy 필드18 − d_net) p95:
#         --cpu 1 load  80:  6.34 -> 10.55  상승 1.664x  (2배 미달)
#         --cpu 1 load  90:  6.48 -> 10.91  상승 1.684x  (2배 미달)
#         --cpu 1 load 100:  6.57 -> 11.41  상승 1.737x  (2배 미달)
#         --cpu 2 load  80:  6.41 -> 33.86  상승 5.282x  복귀 0.95x  <- 당시 채택
#       (이 값들은 시드 3x/2x 시절, 즉 search 파싱량이 더 크던 때의 것이다.
#        지시서 v9 로 1x 정규화했으므로 아래 [신] 표가 유효하다. 구 값은 보존만.)
#
# [신] 2026-08-05 시드 정규화(1x) 후 재캘리브레이션 — STEP S6.
#      기준: 대상 사이트 f_c p95 상승 2~4배, 달성률 90%+, 5xx 1%-, 복귀 1.2x 이내.
#      f_c = front Envoy 필드18 - d_net. pre/during/post 각 60s, 가드 2s.
#
#   * 결론 1: --cpu-load 에는 레버리지가 없다. 응답이 강도에 평평한 계단이다.
#      S1 bl_lr 800rps:  load 10 -> 3.20x / load 20 -> 3.14x / load 30 -> 3.11x
#      S3 site_s3 1072:  load 10 -> 5.45x / load 50 -> 5.41x / load 100 -> 5.12x
#      DSB 요청 1건이 6+ 프로세스 체인을 거치므로, 같은 cpuset 에 CPU-bound
#      프로세스가 "존재하는 것" 자체가 스케줄링 지연을 고정량 얹는다. 뺏는 CPU
#      총량은 부차적이다. --cpu 2 가 --cpu 1 보다 낮게 나오기도 한다(런간 변동).
#
#   * 결론 2: 상승배수를 정하는 것은 강도가 아니라 **도착률**이다.
#      S3 cpu2/load80:   800 rps -> 2.50x (통과)  /  1072 rps -> 5.08x (초과)
#      즉 도착률 1072 에서는 어떤 강도로도 2~4배 창에 들어갈 수 없다.
#
#   확정 (도착률 800 rps 에서만 유효):
#         S1 (bl_lr,   S1 유입 319rps): --cpu 1 --cpu-load 30   3.11x
#         S3 (site_s3, S3 유입 800rps): --cpu 2 --cpu-load 80   2.50x  (구 값 유지)
#         S2 : 미캘리브레이션 (지시서 v9 범위 밖). 기본값은 잠정치다.
#      도착률을 바꾸면 **반드시 재캘리브레이션**할 것.
# ---------------------------------------------------------------------------
set -uo pipefail

SITE="S3"
case "${1:-}" in
  S1|S2|S3) SITE="$1"; shift ;;
esac
ACTION="${1:-status}"

case "$SITE" in
  S1) IP=192.168.0.3;  DEF_CPUSET="0";   DEF_CPU=1; DEF_LOAD=30 ;;
  S2) IP=192.168.0.2;  DEF_CPUSET="0-1"; DEF_CPU=1; DEF_LOAD=50 ;;
  S3) IP=192.168.0.40; DEF_CPUSET="0-3"; DEF_CPU=2; DEF_LOAD=80 ;;
  *)  echo "usage: $0 [S1|S2|S3] {start [지속초]|stop|status}" >&2; exit 2 ;;
esac

CPU="${TB_STRESS_CPU:-$DEF_CPU}"
LOAD="${TB_STRESS_LOAD:-$DEF_LOAD}"
CPUSET="${TB_STRESS_CPUSET:-$DEF_CPUSET}"
LOGF="${TB_STRESS_LOG:-/var/tmp/tb-stress.log}"

# 대상 호스트에서 실행할 본체. 인자를 값으로 박아 넣어 원격에서도 동일하게 돈다.
payload() {
cat <<PAYLOAD
set -uo pipefail
CPU=$CPU; LOAD=$LOAD; CPUSET="$CPUSET"; LOGF="$LOGF"; DUR="${1:-0}"

stop_all() {
  # stress-ng 자식은 procname 이 stress-ng-cpu 로 바뀐다. 부모부터 죽이고
  # 남은 자식을 정확 이름(-x)으로 회수한다.
  # \`pkill -f stress-ng\` 는 이 스크립트를 감싼 ssh 명령줄까지 매칭해
  # 자기 자신을 죽일 수 있으므로 쓰지 않는다.
  pkill -x stress-ng     2>/dev/null
  pkill -x stress-ng-cpu 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -x stress-ng >/dev/null 2>&1 || pgrep -x stress-ng-cpu >/dev/null 2>&1 || break
    sleep 0.2
  done
  pkill -9 -x stress-ng     2>/dev/null
  pkill -9 -x stress-ng-cpu 2>/dev/null
  return 0
}

status() {
  local n m
  n=\$(pgrep -xc stress-ng     2>/dev/null); n=\${n:-0}
  m=\$(pgrep -xc stress-ng-cpu 2>/dev/null); m=\${m:-0}
  if [ "\$n" -gt 0 ] || [ "\$m" -gt 0 ]; then
    echo "running  parent=\$n worker=\$m  cpu=\$CPU load=\$LOAD cpuset=\$CPUSET"
  else
    echo "stopped"
  fi
}

case "$ACTION" in
  start)
    stop_all
    t=()
    [ "\$DUR" != "0" ] && t=(--timeout "\${DUR}s")
    # setsid 로 호출자(ssh/systemd)에서 떼어낸다. 부모가 죽어도 남고,
    # 회수는 항상 stop 으로 한다.
    setsid taskset -c "\$CPUSET" \
        stress-ng --cpu "\$CPU" --cpu-load "\$LOAD" "\${t[@]}" \
        >>"\$LOGF" 2>&1 </dev/null &
    sleep 0.5
    status
    ;;
  stop)   stop_all; status ;;
  status) status ;;
  *) echo "usage: tb-stress.sh [S1|S2|S3] {start [지속초]|stop|status}" >&2; exit 2 ;;
esac
PAYLOAD
}

SELF_IP_LIST=$(hostname -I 2>/dev/null)
if [[ " $SELF_IP_LIST " == *" $IP "* ]]; then
  payload "${2:-0}" | bash
else
  payload "${2:-0}" | ssh -o BatchMode=yes -o ConnectTimeout=8 "$IP" 'bash -s'
fi

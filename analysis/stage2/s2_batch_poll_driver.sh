#!/usr/bin/env bash
# 다중 런 배치 + 런마다 tc 폴러 (STAGE2 v3 primitive 검증·앵커·본 배치 공용).
#
# 사용: MAN=<manifest> OUT=<outdir> DSTART=<주입 상대초> [POLL_DUR=14] 이 스크립트
# 러너 로그의 "본측정=<epoch>" 을 런 순서대로 읽어, 각 런의 주입 5s 전부터
# 폴러를 띄운다. 폴러는 .43 현지에서 tc show 만 부른다(read-only).
set -u
EXP=/home/user/exp
MAN=${MAN:?manifest 경로}
OUT=${OUT:?출력 디렉터리}
DSTART=${DSTART:?주입 상대초 (disturb_start)}
POLL_DUR=${POLL_DUR:-14}
POLL_INT=${POLL_INT:-0.008}
LOG=$OUT/batch_driver.log
mkdir -p "$OUT"
exec >>"$LOG" 2>&1
echo "=== batch+poll driver start $(date +%F' '%T) man=$MAN"

scp -q "$EXP/analysis/stage2/s2_tcpoll.py" user@192.168.0.43:/var/tmp/s2_tcpoll.py

cd "$EXP"
python3 run_all.py --manifest "$MAN" --outdir "$OUT" &
RUNNER=$!

seen=0
while kill -0 $RUNNER 2>/dev/null; do
  # 새 런의 t_meas 가 나타나면 그 런의 주입 시각에 맞춰 폴러 예약
  n=$(grep -c '본측정=' "$LOG" || true)
  if [ "${n:-0}" -gt "$seen" ]; then
    seen=$((seen + 1))
    TMEAS=$(grep -o '본측정=[0-9.]*' "$LOG" | sed -n "${seen}p" | cut -d= -f2)
    RID=$(grep -o '^\[[0-9:]*\] \[[0-9]*/[0-9]*\] [a-zA-Z0-9_]*' "$LOG" | sed -n "${seen}p" | awk '{print $3}')
    RID=${RID:-run$seen}
    START=$(python3 -c "print($TMEAS + $DSTART - 5)")
    echo "런$seen($RID) t_meas=$TMEAS 폴러 예약=$START"
    python3 -c "
import time
d=$START-time.time()
if d>0: time.sleep(d)"
    echo "  폴러 기동 $(date +%s.%N)"
    ssh user@192.168.0.43 "python3 /var/tmp/s2_tcpoll.py $POLL_DUR $POLL_INT" \
      > "$OUT/tcpoll_$RID.csv" 2>>"$OUT/tcpoll.err"
    echo "  폴러 종료 $(date +%s.%N) 행수=$(wc -l < "$OUT/tcpoll_$RID.csv")"
  fi
  sleep 2
done

wait $RUNNER
RC=$?
echo "=== batch+poll driver end $(date +%F' '%T) rc=$RC"
touch "$OUT/DRIVER_DONE"

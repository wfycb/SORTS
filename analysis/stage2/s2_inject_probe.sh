#!/usr/bin/env bash
# STAGE2 §4-1 실측 드라이버: 부하 있는 짧은 radio 런 + tc 폴러 동시 실행.
#
# 목적 (G1 회신 §4): "감지 시각"이 tc 실제 적용 시각보다 앞서는가?
#   - 폴러(.43 현지, 20ms)가 netem leaf 가시화 시각과 u32 필터 부착 시각을 분리
#   - 러너가 같은 런에서 decisions.csv(감지 tick)와 load_c1.csv(트래픽 발효)를 남김
# 폴러는 tc show 만 부른다 — 상태 변경 없음.
set -u
EXP=/home/user/exp
OUT=$EXP/runs/stage2-inject-20260811${INJ_SUFFIX:-}
LOG=$OUT/inject_driver.log
MAN=$EXP/manifest_stage2_inject.json
mkdir -p "$OUT"
exec >>"$LOG" 2>&1
echo "=== inject probe driver start $(date +%F' '%T)"

scp -q "$EXP/analysis/stage2/s2_tcpoll.py" user@192.168.0.43:/var/tmp/s2_tcpoll.py

cd "$EXP"
python3 run_all.py --manifest "$MAN" --outdir "$OUT" &
RUNNER=$!

# 러너 로그에서 본측정 시각(t_meas, .40 epoch)을 읽어 주입 시점을 계산한다.
# manifest: warmup 30 / disturb_start 40  -> 주입 = t_meas + 40
TMEAS=""
for i in $(seq 1 120); do
  TMEAS=$(grep -o '본측정=[0-9.]*' "$LOG" | tail -1 | cut -d= -f2)
  [ -n "$TMEAS" ] && break
  sleep 1
done
if [ -z "$TMEAS" ]; then
  echo "★ t_meas 파싱 실패 — 폴러 미기동"; wait $RUNNER; exit 1
fi
START=$(python3 -c "print($TMEAS + 40 - 5)")   # 주입 5s 전부터
echo "t_meas=$TMEAS 폴러 시작 예정=$START (주입 예상 $(python3 -c "print($TMEAS+40)"))"
python3 -c "
import time,sys
d=$START-time.time()
if d>0: time.sleep(d)"
echo "폴러 기동 $(date +%s.%N)"
ssh user@192.168.0.43 "python3 /var/tmp/s2_tcpoll.py 14 0.008" > "$OUT/tcpoll.csv" 2>"$OUT/tcpoll.err"
echo "폴러 종료 $(date +%s.%N) 행수=$(wc -l < "$OUT/tcpoll.csv")"

wait $RUNNER
RC=$?
echo "=== inject probe driver end $(date +%F' '%T) rc=$RC"
touch "$OUT/DRIVER_DONE"

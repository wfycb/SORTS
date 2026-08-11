#!/usr/bin/env bash
# STAGE2 S2-0-3 드라이런 드라이버 — nohup 무인 완주 (feedback-longrun-autonomy).
# 1) .43 컨트롤러 CPU 샘플러 기동  2) run_all 배치  3) 샘플러 수거  4) DONE.
set -u
EXP=/home/user/exp
OUT=$EXP/runs/stage2-dryrun-20260811${DRYRUN_SUFFIX:-}
LOG=$OUT/dryrun_driver.log
mkdir -p "$OUT"
exec >>"$LOG" 2>&1
echo "=== dryrun driver start $(date +%F' '%T)"

# .43 CPU 샘플러: sorts_ctl pid 의 utime+stime 을 1s 간격 기록 (러너가 런마다
# 컨트롤러를 새로 띄우므로 pid 변화도 함께 기록된다). 30분 상한 자체 종료.
# 함정 주의(2회 실측): pkill -f 는 자기 ssh 원격 셸 명령줄과 매칭돼 자기를
# 죽인다 — 경로 리터럴이 명령줄에 있는 한 [.] 트릭도 소용없다. pidfile 로만
# 정지한다 (pkill 금지).
ssh user@192.168.0.43 'PF=/var/tmp/s2dry_cpusample.pid
[ -f $PF ] && kill "$(cat $PF)" 2>/dev/null; rm -f $PF
cat > /var/tmp/s2dry_cpusample.sh <<"EOF"
#!/bin/sh
echo $$ > /var/tmp/s2dry_cpusample.pid
for i in $(seq 1 1800); do
  pid=$(pgrep -f "sorts_ctl[.]py" | head -1)
  if [ -n "$pid" ] && [ -r /proc/$pid/stat ]; then
    echo "$(date +%s.%N) $pid $(awk "{print \$14, \$15}" /proc/$pid/stat)"
  fi
  sleep 1
done
EOF
chmod +x /var/tmp/s2dry_cpusample.sh
nohup /var/tmp/s2dry_cpusample.sh > /var/tmp/s2dry_cpu.log 2>&1 & echo sampler_pid=$!'

cd "$EXP"
python3 run_all.py --manifest manifest_stage2_dryrun.json --outdir "$OUT"
RC=$?
echo "run_all rc=$RC"

ssh user@192.168.0.43 'PF=/var/tmp/s2dry_cpusample.pid; [ -f $PF ] && kill "$(cat $PF)" 2>/dev/null; rm -f $PF; true'
scp -q user@192.168.0.43:/var/tmp/s2dry_cpu.log "$OUT/s2dry_cpu.log"
echo "=== dryrun driver end $(date +%F' '%T) rc=$RC"
touch "$OUT/DRIVER_DONE"

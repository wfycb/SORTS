#!/bin/bash
# STAGE5 S5-0 §2.2.3 프로브 드라이버 — 샘플러 기동 → 배치 → 샘플러 정지 → 수거.
# I-15: 원격 데몬 정지는 pidfile 방식(pkill 금지).
set -u
OUT=/home/user/exp/runs/stage5-probe-20260812
LOADGEN=192.168.0.12
S1=192.168.0.3
mkdir -p "$OUT"

start_sampler() {  # $1=host $2=extra_files
  ssh -n -o ConnectTimeout=8 "$1" "nohup bash -c 'echo \$\$ > /var/tmp/s5probe.pid;
    while true; do
      printf \"%s \" \$(date +%s);
      grep ^cpu\\  /proc/stat | tr -s \" \" \" \";
      for f in $2; do printf \"%s=%s \" \$(basename \$f) \$(cat \$f 2>/dev/null); done;
      echo;
      sleep 2;
    done' > /var/tmp/s5probe_sample.txt 2>&1 < /dev/null &"
  echo "[drv] 샘플러 기동 $1"
}

stop_sampler() {   # $1=host $2=local name
  ssh -n -o ConnectTimeout=8 "$1" 'kill $(cat /var/tmp/s5probe.pid) 2>/dev/null; sleep 0.3; rm -f /var/tmp/s5probe.pid'
  scp -q "$1:/var/tmp/s5probe_sample.txt" "$OUT/sample_$2.txt" 2>/dev/null
  echo "[drv] 샘플러 정지·수거 $1 -> sample_$2.txt"
}

echo "[drv] 시작 $(date +%H:%M:%S)"
start_sampler "$LOADGEN" ""
start_sampler "$S1" "/sys/class/net/enp1s0/statistics/rx_bytes /sys/class/net/enp1s0/statistics/tx_bytes"

cd /home/user/exp
python3 run_all.py --manifest /home/user/exp/manifest_stage5_probe.json --outdir "$OUT" > "$OUT/runner.log" 2>&1
RC=$?
echo "[drv] 배치 종료 rc=$RC"

stop_sampler "$LOADGEN" "loadgen"
stop_sampler "$S1" "s1"
echo "rc=$RC" > "$OUT/PROBE_DONE"
echo "[drv] 완료 $(date +%H:%M:%S)"

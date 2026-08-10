#!/usr/bin/env bash
# T1 §2.3 계단 실측: 사이트 고정 x 밴드 -> 코호트1 search E2E p50 vs SLO 45.
# [주의] 이 스크립트는 v10 T2 이전(단일 route/site_weights 키) 시절에 실행됐다.
# T2 이후에는 setpol 이 무효다 — 다시 쓰려면 7-prefix 로 갱신하라.
# 각 칸 30s, 20 rps (2conn x 10rps), UE 경로 (--cohort 1).
set -uo pipefail

LOADGEN=192.168.0.12
ENVOY=192.168.0.43
PIN="taskset -c 6-15"
OUT=$HOME/exp/t1_stair
mkdir -p "$OUT"

C1=$(ssh $LOADGEN "awk -F'\t' '\$1==\"1\"{print \$3}' /run/tb-cohort.map")
C2=$(ssh $LOADGEN "awk -F'\t' '\$1==\"2\"{print \$3}' /run/tb-cohort.map")
echo "cohort1=$C1 cohort2=$C2"

SEARCH="/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7867&lon=-122.4112"

setpol() {
  local q=""
  for k in site_s1 site_s2 site_s3 bl_rr bl_lr bl_loc; do
    w=0; [ "$k" = "$1" ] && w=100
    q="$q&routing.site_weights.$k=$w"
  done
  ssh $ENVOY "curl -s -X POST 'http://127.0.0.1:9901/runtime_modify?${q#&}'" >/dev/null
}

band() {  # $1 = kbit or "clear"
  if [ "$1" = "clear" ]; then
    ssh $ENVOY "C1_IP=$C1 C2_IP=$C2 sudo -n /usr/local/sbin/tb-radio2.sh clear" >/dev/null
  else
    ssh $ENVOY "C1_IP=$C1 C2_IP=$C2 sudo -n /usr/local/sbin/tb-radio2.sh apply 'rate ${1}kbit' 'none'" >/dev/null
  fi
}

for site in site_s3 site_s2 site_s1; do
  setpol "$site"
  for kb in 20000 4500 2300 1600; do
    band "$kb"
    sleep 1
    tag="${site}_${kb}"
    ssh $LOADGEN "$PIN python3 ~/tb-load.py --host $ENVOY --port 8080 \
      --cohort 1 --path '$SEARCH' --connections 2 --rps-per-connection 10 \
      --warmup 5 --duration 30 --csv /var/tmp/t1_${tag}.csv --label t1-$tag" \
      2>&1 | grep -E "^---|corrected|service" | head -3
    scp -q $LOADGEN:/var/tmp/t1_${tag}.csv "$OUT/" && ssh $LOADGEN "rm -f /var/tmp/t1_${tag}.csv"
    band clear
    sleep 1
  done
done
setpol site_s3
echo "DONE"

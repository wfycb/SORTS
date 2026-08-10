#!/usr/bin/env bash
# 야간배치2 과제3: S3(.40):5000 을 .43 발신에 한정해 차단 (수신측 .40 로컬).
# ssh(22)·타 포트·컨테이너 무접촉. 반드시 자동 해제 타이머와 함께 쓴다.
set -u
D=${1:-300}   # 자동 해제 지연 [s] — 실험 창(120s)보다 넉넉히
sudo -n iptables -I INPUT 1 -p tcp --dport 5000 -s 192.168.0.43 -j DROP
sudo -n iptables -I DOCKER-USER 1 -p tcp --dport 5000 -s 192.168.0.43 -j DROP 2>/dev/null || true
# 독립 자동 해제 (배치가 죽어도 풀린다)
nohup sh -c "sleep $D; /home/user/exp/analysis/night2/node_unblock.sh" \
  > /var/tmp/node_autounblock.log 2>&1 & disown
echo "BLOCKED $(date +%H:%M:%S.%N) auto-unblock in ${D}s (timer pid $!)"

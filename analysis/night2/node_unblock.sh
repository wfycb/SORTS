#!/usr/bin/env bash
# 차단 해제 — 멱등 (룰이 몇 개 쌓였든 전부 제거)
# 신 태그판(-m comment)과 구판(무태그) 둘 다 제거 — 전이기 잔재까지 청소
while sudo -n iptables -D INPUT -p tcp --dport 5000 -s 192.168.0.43 \
  -m comment --comment "sorts-fault" -j DROP 2>/dev/null; do :; done
while sudo -n iptables -D DOCKER-USER -p tcp --dport 5000 -s 192.168.0.43 \
  -m comment --comment "sorts-fault" -j DROP 2>/dev/null; do :; done
while sudo -n iptables -D INPUT -p tcp --dport 5000 -s 192.168.0.43 -j DROP 2>/dev/null; do :; done
while sudo -n iptables -D DOCKER-USER -p tcp --dport 5000 -s 192.168.0.43 -j DROP 2>/dev/null; do :; done
echo "UNBLOCKED $(date +%H:%M:%S.%N)"

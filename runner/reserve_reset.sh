#!/usr/bin/env bash
# 각 reserve 실험 직전 세 사이트 동시 초기화.
#  - reservation drop: 문서 누적 -> 인덱스 성장 -> insert 비용 상승이
#    나중 실험을 느리게 만들어 SORTS 동작으로 오인될 수 있다.
#  - memcached flush_all: 예약수 캐시 "<hotelId>_<in>_<out>" 와 용량 캐시
#    "<hotelId>_cap" 을 비운다. drop 만 하면 캐시된 옛 카운트로 계속 거절된다.
#    컨테이너 재시작은 쓰지 않는다 — reservation 서비스가 memcached 오류에
#    log.Panic() 을 써서 재시작 직후 500 이 21건씩 발생한다 (2026-08-02 실측).
#    flush_all 은 연결을 유지하므로 이 문제가 없다.
set -uo pipefail
SP=/home/user/exp
MONGO=hotelreservation-mongodb-reservation-1
MEMC=hotelreservation-memcached-reserve-1

for ip in 192.168.0.3 192.168.0.2 192.168.0.40; do
  echo -n "  [reset] $ip  "
  if [ "$ip" = "192.168.0.40" ]; then
    docker exec -i $MONGO mongosh --quiet < $SP/res_drop.js 2>&1 | grep -oE "문서수 = [0-9]+" | tr -d '\n'
    mip=$(docker inspect $MEMC --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
    r=$(printf 'flush_all\r\nquit\r\n' | timeout 5 nc "$mip" 11211 | tr -d '\r\n')
  else
    ssh $ip "docker exec -i $MONGO mongosh --quiet" < $SP/res_drop.js 2>&1 | grep -oE "문서수 = [0-9]+" | tr -d '\n'
    r=$(ssh $ip "mip=\$(docker inspect $MEMC --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'); printf 'flush_all\r\nquit\r\n' | timeout 5 nc \$mip 11211 | tr -d '\r\n'")
  fi
  echo "  flush_all=$r"
done

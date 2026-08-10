# 출처 (testbed/) — 호스트별
| 하위 | 호스트 | 원 경로 |
|---|---|---|
| pc5-43/tb-radio2.sh | `.43` | `/usr/local/sbin/tb-radio2.sh` (무선 밴드 v2 — 커넥션 단위 64버킷; sudoers NOPASSWD 등록 필요) |
| pc5-43/tb-radio.sh | `.43` | `/usr/local/sbin/tb-radio.sh` (v1 — 폐기 이력용. v1로 되돌리지 말 것: 코호트 붕괴 실측) |
| pc5-43/tb-netem.sh | `.43` | `/usr/local/sbin/tb-netem.sh` (경로 지연 d_net — 사이트 IP 하드코딩, 재부팅마다 재적용 필요) |
| pc2-40/tb-stress.sh | `.40` | `/usr/local/sbin/tb-stress.sh` (S3 서버 교란 — 지시서 표와 달리 .43 아님, 실측 확인) |
| pc3-12/tb-load.py | `.12` | `~/tb-load.py` (open-loop 부하 생성기; 소스 포트 고정 --port-base 지원) |

# 출처 (envoy/)
- gen_envoy_v10.py — `.40:/home/user/exp/` (envoy.yaml + envoy_keys.json 생성기)
- envoy_keys.json — `.40:/home/user/exp/` (클러스터/prefix 키 단일 출처;
  `.43:~/envoy_keys.json` 에도 배포 — md5 일치 확인)
- envoy.yaml — `.43:/etc/envoy/envoy.yaml` (gen_envoy_v10.py 산출물 배포본.
  코호트 IP(XFF 매치)가 렌더 시점 값으로 박혀 있음 — 재구성 시 재생성 필요)

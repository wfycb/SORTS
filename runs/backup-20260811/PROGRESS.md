# SORTS GitHub 백업 (2026-08-11 01:4x~)

## Phase 0 조사 (01:4x)
- 배치 없음·테스트베드 클린 (컨트롤러/loadgen 잔재 0, ogstun fq_codel).
- git 저장소: ~/exp 및 상위에 없음 → 권고대로 **~/sorts-backup 신규** 채택.
  (기존 repo는 DSB/wrk2/.nvm 서드파티 클론과 N6_Observe 별개 프로젝트뿐.)
- 인증: gh HTTPS(계정 wfycb, credential helper) OK — `git ls-remote` 성공.
  SSH 키는 미등록(denied) → HTTPS 사용. 원격 wfycb/SORTS = **empty·private**
  (isEmpty true, diskUsage 0) — 보존할 히스토리 없음, force push 논점 없음.
- 인벤토리: .40 exp(*.py 42 + analysis) / .43 /usr/local/sbin/tb-{radio,radio2,netem}.sh
  + /etc/envoy/envoy.yaml + 배포본 4종 / .12 ~/tb-load.py.
  ★tb-stress.sh 는 지시 표와 달리 .43 이 아니라 **.40 /usr/local/sbin/** (실측).
- 크기: exp 9.4G (runs 8.9G, analysis 251M[cache 213M+obs_replay 36M], calib 199M).
  100MB+ 파일 0, 50MB+ 0. **선별 런 산출물(decisions/obs_state/meta/summary/
  marks/thermal/마커) 전 배치 합 59M** → 예산(500M) 내, 전 배치 포함 가능.
- 비밀정보 스캔: 실제 자격증명 **0건** (호스트 pw 패턴 0, sudo -S 0, gho_/ghp_/
  Authorization/PRIVATE KEY 0 — *.bak·runs md/json 포함). `password=` 히트는
  전부 DSB HotelReservation 공개 벤치마크 합성 계정(Cornell_30/0000000000) —
  비밀 아님, 보고서에 명시. → 정지 조건 미해당, 진행.

# 출처 (runner/)
전부 `.40:/home/user/exp/` 원본, 실행 위치도 `.40`.
- run_all.py — 배치 러너 (매니페스트 실행, 렌더-배포, 가드, precheck)
- reserve_reset.sh + res_drop.js — 런 전 3사이트 reservation 초기화
- obs_ctl_regress.py — 스위치 off 회귀 (신구판 12열 대조)
- make_manifest.py — 매니페스트 생성기
- manifests/ — 실제 사용한 매니페스트 전부 (`.40:/home/user/exp/manifest_*.json`)
- tools/ — 캘리브레이션·검증 단발 스크립트 (`.40:/home/user/exp/*.py`)

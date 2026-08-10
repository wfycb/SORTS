# 출처 (controller/)
| 파일 | 원본 (편집 위치) | 배포 위치 (실행) |
|---|---|---|
| sorts_ctl.py | `.40:/home/user/exp/sorts_ctl.py` | `.43:~/sorts_ctl.py` |
| obs.py | `.40:/home/user/exp/obs.py` | `.43:~/obs.py` |
| sorts.yaml.tmpl | `.40:/home/user/exp/sorts.yaml.tmpl` | (렌더 소스 — 배포 안 함) |
| sorts.yaml | `.40:/home/user/exp/sorts.yaml` (run_all 렌더 산출물 — **손편집 금지**) | `.43:~/sorts.yaml` |

배포는 `run_all.py`의 `render_deploy_ctl()`(sorts.yaml)과 수동 scp(코드) +
`check_deploy()` md5 검증. 2026-08-11 수집 시점 배포본과 원본 md5 전부 일치 확인.

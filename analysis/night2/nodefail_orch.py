#!/usr/bin/env python3
"""과제3 오케스트레이터 — 러너(동결) 무수정으로 차단 타이밍 주입.

배치 로그를 감시해 각 런의 본측정 시각(t_meas, .12 시계 — 로컬과 수십 ms
오프셋뿐이라 120s 타이밍에 무시 가능)을 파싱, t+120 에 차단(자동 해제 300s
백스톱 포함), t+240 에 해제하고 즉시 도달성을 실증한다. 3런 처리 후 종료.
모든 사건을 stdout(오케스트레이터 로그)에 남긴다.
"""
import re
import subprocess
import sys
import time

LOG = sys.argv[1]
N_RUNS = 3
BLOCK = "/home/user/exp/analysis/night2/node_block.sh"
UNBLOCK = "/home/user/exp/analysis/night2/node_unblock.sh"
CURL = ("ssh 192.168.0.43 \"curl -s -m 3 -o /dev/null -w '%{http_code}' "
        "'http://192.168.0.40:5000/hotels?inDate=2015-04-09&"
        "outDate=2015-04-10&lat=37.7867&lon=-122.4112'\"")


def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=60)
    return r.stdout.strip()


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


seen = set()
done = 0
while done < N_RUNS:
    try:
        txt = open(LOG, errors="replace").read()
    except OSError:
        time.sleep(2)
        continue
    for m in re.finditer(r"부하 기동 t_start=([0-9.]+) 본측정=([0-9.]+)", txt):
        t_meas = float(m.group(2))
        if t_meas in seen:
            continue
        seen.add(t_meas)
        done += 1
        log(f"런 {done}: t_meas={t_meas:.1f} 감지 — t+120 차단 예약")
        while time.time() < t_meas + 120:
            time.sleep(0.5)
        log(f"런 {done}: 차단 → {sh(BLOCK + ' 300')}")
        log(f"런 {done}: 차단 검증 curl={sh(CURL) or '000/timeout'}")
        while time.time() < t_meas + 240:
            time.sleep(0.5)
        log(f"런 {done}: 해제 → {sh(UNBLOCK)}")
        ok = sh(CURL)
        log(f"런 {done}: 해제 검증 curl={ok}")
        if ok != "200":
            time.sleep(3)
            ok = sh(CURL)
            log(f"런 {done}: 해제 재검증 curl={ok}")
            if ok != "200":
                log("★해제 검증 실패 — 정지 조건 6. 재해제 시도 후 종료")
                sh(UNBLOCK)
                sys.exit(2)
    time.sleep(2)
log("3런 처리 완료 — 최종 해제(멱등) 후 종료")
sh(UNBLOCK)
log(f"최종 도달성 curl={sh(CURL)}")

#!/usr/bin/env python3
"""1단계 노드 장애 오케스트레이터 — 야간배치2 판(analysis/night2)과 동일
절차(수신측 .40 로컬 DROP, S3:5000 만, .43 발신 한정, 300 s 자동 해제 백스톱,
curl 실증)에 두 가지만 더한다:
  (1) 런 수 파라미터화 (argv[2], 기본 3)
  (2) 사건 epoch 를 events.json 에 남긴다 — 복구 3지표(재개/정상화/드레인)
      계산이 절대 시각을 요구한다 (야간판은 HH:MM:SS 로그뿐이었다).

사용: nodefail_orch.py <배치로그> <런수> <events.json>
"""
import json
import re
import subprocess
import sys
import time

LOG = sys.argv[1]
N_RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
EV_PATH = sys.argv[3] if len(sys.argv) > 3 else "/var/tmp/s1_nf_events.json"
BLOCK = "/home/user/exp/analysis/night2/node_block.sh"      # 야간과 동일
UNBLOCK = "/home/user/exp/analysis/night2/node_unblock.sh"  # 야간과 동일
CURL = ("ssh 192.168.0.43 \"curl -s -m 3 -o /dev/null -w '%{http_code}' "
        "'http://192.168.0.40:5000/hotels?inDate=2015-04-09&"
        "outDate=2015-04-10&lat=37.7867&lon=-122.4112'\"")

events = []


def save():
    json.dump(events, open(EV_PATH, "w"), ensure_ascii=False, indent=1)


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
        ev = {"idx": done, "t_meas_12": t_meas}
        log(f"런 {done}: t_meas={t_meas:.1f} 감지 — t+120 차단 예약")
        while time.time() < t_meas + 120:
            time.sleep(0.5)
        t0 = time.time()
        out = sh(BLOCK + " 300")
        ev["block_ts"] = t0
        ev["block_done_ts"] = time.time()
        ev["block_out"] = out
        log(f"런 {done}: 차단 → {out}")
        ev["block_curl"] = sh(CURL) or "000/timeout"
        log(f"런 {done}: 차단 검증 curl={ev['block_curl']}")
        save()
        while time.time() < t_meas + 240:
            time.sleep(0.5)
        t1 = time.time()
        out = sh(UNBLOCK)
        ev["unblock_ts"] = t1
        ev["unblock_done_ts"] = time.time()
        log(f"런 {done}: 해제 → {out}")
        ok = sh(CURL)
        ev["unblock_curl"] = ok
        log(f"런 {done}: 해제 검증 curl={ok}")
        if ok != "200":
            time.sleep(3)
            ok = sh(CURL)
            ev["unblock_curl_retry"] = ok
            log(f"런 {done}: 해제 재검증 curl={ok}")
            if ok != "200":
                log("★해제 검증 실패 — 정지 조건 4/6. 재해제 시도 후 종료")
                sh(UNBLOCK)
                events.append(ev)
                save()
                sys.exit(2)
        events.append(ev)
        save()
    time.sleep(2)
log(f"{N_RUNS}런 처리 완료 — 최종 해제(멱등) 후 종료")
sh(UNBLOCK)
final = sh(CURL)
log(f"최종 도달성 curl={final}")
events.append({"idx": "final", "unblock_curl": final, "ts": time.time()})
save()

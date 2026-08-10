#!/usr/bin/env python3
"""P1-4 재확인 — 정책별 사이트 분배 실측 (지시서 v5 §2.4).

저부하 600건 x 4정책 + bl_lr 부하(400 rps, 60s).
Envoy access log 의 UPSTREAM_HOST 를 세는 것이 유일한 근거다. 판정은 하지 않고
기대치와 나란히 찍기만 한다.
"""
import json
import subprocess
import time

LOADGEN, ENVOY, PIN = "192.168.0.12", "192.168.0.43", "taskset -c 6-15"
LOG = "/var/log/envoy/front_access.log"
OUT = "/home/user/exp/calib/dist_check.json"
SITES = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
D = "inDate=2015-04-09&outDate=2015-04-10"
PATH = f"/hotels?{D}&lat=37.7867&lon=-122.4112"
EXPECT = {"site_s3": "0/0/100 (엄격)", "bl_rr": "33/33/33 (±5%p)",
          "bl_loc": "57/29/14 (±8%p)", "bl_lr": "저부하는 RR 유사 (기록만)"}


def sh(c, t=600):
    return subprocess.run(c, shell=True, capture_output=True, text=True,
                          timeout=t).stdout.strip()


def measure(policy, conns, rps_per, dur, tag):
    sh(f"bash /home/user/setpol.sh {policy}")
    time.sleep(2)
    off0 = int(sh(f"ssh {ENVOY} 'stat -c %s {LOG}'") or 0)
    sh(f'ssh {LOADGEN} "{PIN} python3 ~/tb-load.py --host {ENVOY} --port 8080 '
       f"--cohort 1 --path '{PATH}' --connections {conns} "
       f'--rps-per-connection {rps_per} --duration {dur} --csv /var/tmp/{tag}.csv" '
       f">/dev/null 2>&1", 600)
    off1 = int(sh(f"ssh {ENVOY} 'stat -c %s {LOG}'") or 0)
    txt = sh(f"ssh {ENVOY} \"tail -c +{off0 + 1} {LOG} | head -c {max(off1 - off0, 0)}\"",
             600)
    cnt = {"S1": 0, "S2": 0, "S3": 0, "기타": 0}
    n = 0
    for line in txt.splitlines():
        p = line.split(",")
        if len(p) < 11:
            continue
        n += 1
        cnt[SITES.get(p[10].split(":")[0], "기타")] += 1
    share = {k: round(100 * v / n, 2) for k, v in cnt.items()} if n else {}
    return {"n": n, "count": cnt, "share_pct": share}


if __name__ == "__main__":
    res = {}
    print(f"{'정책':>9s} {'건수':>6s}  {'S1/S2/S3 %':>22s}   기대")
    for pol in ("site_s3", "bl_rr", "bl_loc", "bl_lr"):
        r = measure(pol, 4, 5, 30, f"dist_{pol}")      # 20 rps x 30s = 600건
        res[pol] = r
        s = r["share_pct"]
        print(f"{pol:>9s} {r['n']:>6d}  "
              f"{f'{s.get(chr(83) + chr(49), 0):.2f}/{s.get(chr(83) + chr(50), 0):.2f}/{s.get(chr(83) + chr(51), 0):.2f}':>22s}"
              f"   {EXPECT[pol]}")
    # bl_lr 부하 상태 (§2.4: 400 rps 60s -> 용량 큰 S3 쪽으로 기울어야 한다)
    r = measure("bl_lr", 16, 25, 60, "dist_bl_lr_400")
    res["bl_lr_400rps"] = r
    s = r["share_pct"]
    print(f"{'bl_lr@400':>9s} {r['n']:>6d}  "
          f"{f'{s.get(chr(83) + chr(49), 0):.2f}/{s.get(chr(83) + chr(50), 0):.2f}/{s.get(chr(83) + chr(51), 0):.2f}':>22s}"
          f"   부하 상태 (기록만)")
    sh("bash /home/user/setpol.sh site_s3")
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"\n저장: {OUT}")

#!/usr/bin/env python3
"""P3-4 부하 생성기 한계 확인.

1400 rps 를 코호트 2개로 동시에 낼 때 병목이 생성기인지 서버인지 가른다.
서버가 흡수할 수 있는 조건(정책 bl_rr + recommend 단독, 세 사이트 분산)에서
달성 rps 를 재면 생성기 자체의 상한이 나온다.
"""
import csv
import subprocess
import sys
import time

LOADGEN = "192.168.0.12"
ENVOY = "192.168.0.43"
PIN = "taskset -c 6-15"


def sh(c, t=600):
    return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=t).stdout.strip()


def cpu_snap():
    v = [int(x) for x in sh(f"ssh {LOADGEN} 'head -1 /proc/stat'").split()[1:]]
    return sum(v), v[3] + v[4]


def run(tag, policy, path, conns, rps_per, dur=120):
    sh(f"bash /home/user/setpol.sh {policy}")
    c0 = cpu_snap()
    procs = []
    for c in (1, 2):
        cmd = (f'ssh {LOADGEN} "{PIN} python3 ~/tb-load.py --host {ENVOY} --port 8080 '
               f"--cohort {c} --path '{path}' --connections {conns} "
               f'--rps-per-connection {rps_per} --warmup 15 --duration {dur} '
               f'--csv /var/tmp/{tag}_c{c}.csv --label {tag}-c{c}"')
        procs.append(subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL))
    for p in procs:
        p.wait(timeout=dur + 300)
    c1 = cpu_snap()
    lg_cpu = 100.0 * (1 - (c1[1] - c0[1]) / (c1[0] - c0[0]))

    tot, sends, ends, bad = 0, [], [], 0
    target = 2 * conns * rps_per
    for c in (1, 2):
        sh(f"scp -q {LOADGEN}:/var/tmp/{tag}_c{c}.csv /home/user/exp/calib/")
        rows = [r for r in csv.DictReader(open(f"/home/user/exp/calib/{tag}_c{c}.csv"))
                if r["warmup"] == "0"]
        ok = [r for r in rows if r["status"] == "200" and int(r["bytes_recv"]) == 200]
        bad += len(rows) - len(ok)
        tot += len(ok)
        sends += [float(r["send_ts"]) for r in ok]
        ends += [float(r["end_ts"]) for r in ok]
    el = max(ends) - min(sends)
    ach = tot / el
    print(f"{tag:22s} 목표={target:5.0f}/s  달성={ach:7.1f}/s ({100 * ach / target:6.2f}%)  "
          f"이탈={100 * bad / max(tot + bad, 1):.3f}%  생성기CPU={lg_cpu:5.1f}%", flush=True)
    return 100 * ach / target


if __name__ == "__main__":
    P = "/recommendations?require=dis&lat=37.7867&lon=-122.4112"
    print("=== 생성기 상한 확인: recommend 단독, bl_rr 3사이트 분산 ===", flush=True)
    r = []
    r.append(run("lg700", "bl_rr", P, 14, 25))    # 2 x 350 =  700
    r.append(run("lg1400", "bl_rr", P, 28, 25))   # 2 x 700 = 1400
    r.append(run("lg1400c56", "bl_rr", P, 56, 12.5))  # 스레드 2배, 커넥션당 rps 절반
    sh("bash /home/user/setpol.sh site_s3")
    sys.exit(0 if max(r[1:]) >= 99.0 else 1)

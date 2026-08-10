#!/usr/bin/env python3
"""P2-2 서버 교란 강도 캘리브레이션.

policy=site_s3 로 1400 rps(코호트 2개 x 700)를 흘리면서 S3 의 stress-ng 강도를
--cpu 1/2/3 으로 바꿔가며 S3 의 search f_c 를 잰다.

f_c 는 Envoy access log 필드 18 (US_TX_BEG:US_RX_END, 업스트림 왕복 us) 에서
d_net(S3)=25.006ms 를 뺀 값의 p95 로 계산한다. tb-netem 은 .43 egress 에만
지연을 걸므로 왕복 총 증가분이 곧 주입값이다.

목표: f_c(search, S3) 를 5.07ms -> 15~20ms 로 올리는 강도.
"""
import json
import os
import subprocess
import sys
import time

LOADGEN = "192.168.0.12"
ENVOY = "192.168.0.43"
PIN = "taskset -c 6-15"
DNET_S3 = 25.006
OUT = "/home/user/exp/calib"
os.makedirs(OUT, exist_ok=True)

D = "inDate=2015-04-09&outDate=2015-04-10"
MIX = (
    f"reserve=1:/reservation?{D}&hotelId=1&customerName=cal"
    f"&username=Cornell_30&password=0000000000&number=1,"
    f"search=1.5:/hotels?{D}&lat=37.7867&lon=-122.4112,"
    f"recommend=2:/recommendations?require=dis&lat=37.7867&lon=-122.4112"
)

WARMUP = 20
DURATION = 215
# (라벨, stress cpu 개수, 시작초, 끝초)  — 본측정 시작을 0 으로 한 상대시각
WINDOWS = [
    ("none_pre", 0, 2, 40),
    ("cpu1", 1, 45, 85),
    ("cpu2", 2, 90, 130),
    ("cpu3", 3, 135, 175),
    ("none_post", 0, 180, 213),
]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def sh(cmd, timeout=400):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout).stdout.strip()


def stress(n):
    if n == 0:
        return sh("/usr/local/sbin/tb-stress.sh stop")
    return sh(f"TB_STRESS_CPU={n} /usr/local/sbin/tb-stress.sh start 0")


def main():
    log("정책 site_s3 로 고정")
    sh("bash /home/user/setpol.sh site_s3")
    log("reserve 초기화")
    print(sh("bash /home/user/exp/reserve_reset.sh", timeout=200))
    stress(0)

    # 부하를 백그라운드로 띄운다 (코호트 2개 동시)
    procs = []
    for c in (1, 2):
        cmd = (f'ssh {LOADGEN} "{PIN} python3 ~/tb-load.py --host {ENVOY} --port 8080 '
               f"--cohort {c} --mix '{MIX}' --connections 10 --rps-per-connection 25 "
               f'--warmup {WARMUP} --duration {DURATION} '
               f'--csv /var/tmp/calib2_c{c}.csv --label calib-c{c}"')
        procs.append(subprocess.Popen(cmd, shell=True,
                                      stdout=open(f"{OUT}/load2_c{c}.log", "w"),
                                      stderr=subprocess.STDOUT))
    log(f"부하 기동: 2 코호트 x 250 rps (저부하 격리), 워밍업 {WARMUP}s + 본측정 {DURATION}s")

    t_meas = time.time() + 1.0 + WARMUP  # tb-load 는 t_start = now+1.0
    marks = []
    for label, ncpu, t0, t1 in WINDOWS:
        while time.time() < t_meas + t0:
            time.sleep(0.2)
        st = stress(ncpu)
        marks.append(dict(label=label, cpu=ncpu, t0=t_meas + t0, t1=t_meas + t1))
        log(f"  t+{t0:>3}s  {label:>9s}  stress cpu={ncpu}  -> {st}")

    for p in procs:
        p.wait(timeout=600)
    stress(0)
    log("부하 종료, stress 해제")

    # Envoy 로그 슬라이스
    lo = marks[0]["t0"] - 5
    raw = sh(f"ssh {ENVOY} \"awk -F, '\\$1>{lo:.0f} && \\$11==\\\"192.168.0.40:5000\\\" "
             f"{{split(\\$4,a,\\\"?\\\"); print \\$1, a[1], \\$5, \\$15, \\$18}}' "
             f"/var/log/envoy/front_access.log\"", timeout=300)
    rows = []
    for line in raw.splitlines():
        p = line.split()
        if len(p) == 5:
            rows.append((float(p[0]), p[1], p[2], int(p[3]), int(p[4])))
    log(f"S3 업스트림 레코드 {len(rows)}건")

    results = []
    for m in marks:
        sub = [r for r in rows if m["t0"] <= r[0] <= m["t1"]]
        entry = dict(window=m["label"], cpu=m["cpu"], n=len(sub))
        for ep, path, ebytes in (("search", "/hotels", 4474),
                                 ("reserve", "/reservation", 36),
                                 ("recommend", "/recommendations", 200)):
            v = sorted((r[4] / 1000.0 - DNET_S3) for r in sub
                       if r[1] == path and r[2] == "200")
            nb = [r[3] for r in sub if r[1] == path and r[2] == "200"]
            if v:
                pct = lambda q: v[int(round(q * (len(v) - 1)))]
                entry[ep] = dict(n=len(v), p50=round(pct(.50), 3),
                                 p95=round(pct(.95), 3), p99=round(pct(.99), 3),
                                 med_bytes=sorted(nb)[len(nb) // 2])
        results.append(entry)
        s = entry.get("search", {})
        log(f"  {m['label']:>9s} cpu={m['cpu']}  n={entry['n']:6d}  "
            f"search f_c p50={s.get('p50', float('nan')):7.2f} "
            f"p95={s.get('p95', float('nan')):7.2f} p99={s.get('p99', float('nan')):7.2f} ms")

    with open(f"{OUT}/calib_stress_lowload.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    log(f"-> {OUT}/calib_stress_lowload.json")


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""소형 응답(recommend)의 d_acc 가 §0.3 기준값의 2.4~2.8배로 나온 원인 진단.

게이트(gate_radio2.json, 혼합 부하)에서 코호트1 d_acc 배수:
    search 1.118 / 1.094   <- 기준값과 일치
    reserve 2.47 / 2.48 ,  recommend 2.76 / 2.74   <- 2.4~2.8배

가설 A: 같은 가상 사용자 링크에 4474B search 응답이 함께 흐르므로 소형 응답이
        그 뒤에 줄 서서(HOL) 대기시간이 붙는다. §0.3 의 d_acc 는 "요청 1건을
        고립해서 잰 직렬화 시간"이라 대기시간을 포함하지 않는다.
        -> recommend 만 흘리면(대형 응답 없음) 배수가 1.0 근처로 내려간다.
가설 B: 응답 헤더·패킷화 때문에 실제 전송 바이트가 payload 보다 크다.
        -> 단독으로 흘려도 2.7배 그대로다. 그렇다면 §0.3 표 자체가 과소값이다.

두 구성(recommend 단독 / 실제 혼합)을 같은 정책·같은 스크립트로 돌려 비교한다.
정책은 site_s3 로 고정한다 — d_net 이 25ms 로 일정해 LB 이동에 의한 교란이
없다. 판정은 하지 않고 숫자만 낸다.
"""
import csv
import json
import subprocess
import time

LOADGEN, ENVOY, PIN = "192.168.0.12", "192.168.0.43", "taskset -c 6-15"
OUT = "/home/user/exp/calib"
D = "inDate=2015-04-09&outDate=2015-04-10"
REC = "/recommendations?require=dis&lat=37.7867&lon=-122.4112"
MIX = (f"reserve=1:/reservation?{D}&hotelId=1&customerName=diag"
       f"&username=Cornell_30&password=0000000000&number=1,"
       f"search=1.5:/hotels?{D}&lat=37.7867&lon=-122.4112,"
       f"recommend=2:{REC}")
BAND = {"poor": "rate 2300kbit", "extreme": "rate 1600kbit"}
REF = {"poor": {"recommend": 1.59, "search": 17.06, "reserve": 0.92},
       "extreme": {"recommend": 2.28, "search": 24.57, "reserve": 1.46}}
CONNS, RPS_PER, WARMUP, DUR = 28, 25, 8, 30


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def sh(c, t=600):
    return subprocess.run(c, shell=True, capture_output=True, text=True,
                          timeout=t).stdout.strip()


def radio(spec):
    b = (f"ssh {ENVOY} \"C1_IP=10.46.0.6 C2_IP=10.46.0.7 "
         f"sudo -n /usr/local/sbin/tb-radio2.sh ")
    return sh(b + ('clear"' if spec == "none" else f"apply '{spec}' none\""), 120)


def p50(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else float("nan")


def one(comp, band, tag):
    """한 조합을 돌리고 엔드포인트별 p50 과 커넥션당 하향 바이트율을 낸다."""
    radio("none" if band == "none" else BAND[band])
    sel = f"--mix '{MIX}'" if comp == "mix" else f"--path '{REC}'"
    if comp == "mix":
        sh("bash /home/user/exp/reserve_reset.sh", 300)
    sh(f'ssh {LOADGEN} "{PIN} python3 ~/tb-load.py --host {ENVOY} --port 8080 '
       f"--cohort 1 {sel} --connections {CONNS} --rps-per-connection {RPS_PER} "
       f'--warmup {WARMUP} --duration {DUR} --csv /var/tmp/{tag}.csv" '
       f">/dev/null 2>&1", 300)
    sh(f"scp -q {LOADGEN}:/var/tmp/{tag}.csv {OUT}/")
    rows = [r for r in csv.DictReader(open(f"{OUT}/{tag}.csv"))
            if r["warmup"] == "0" and r["status"] == "200"]
    eps = sorted({r.get("ep") or "recommend" for r in rows}) or ["recommend"]
    res = {"n": len(rows), "done_per_s": round(len(rows) / DUR, 1),
           "p50": {e: round(p50([float(r["service_ms"]) for r in rows
                                 if (r.get("ep") or "recommend") == e]), 3)
                   for e in eps}}
    # 커넥션 1개(=가상 사용자 1명)당 하향 바이트율. 밴드 rate 대비가 이용률 rho.
    by = sum(int(r["bytes_recv"]) for r in rows)
    res["per_conn_kbit_s"] = round(by * 8 / DUR / CONNS / 1000, 1)
    return res


if __name__ == "__main__":
    sh("bash /home/user/setpol.sh site_s3")
    out = {}
    for comp in ("rec_only", "mix"):
        out[comp] = {}
        base = one(comp, "none", f"diag2_{comp}_none")
        out[comp]["none"] = base
        log(f"{comp:>9s} none      p50={base['p50']} "
            f"완료={base['done_per_s']}/s conn={base['per_conn_kbit_s']}kbit/s")
        for band in ("poor", "extreme"):
            r = one(comp, band, f"diag2_{comp}_{band}")
            r["d_acc"] = {e: round(r["p50"][e] - base["p50"][e], 3)
                          for e in r["p50"] if e in base["p50"]}
            r["ratio_vs_ref"] = {e: round(r["d_acc"][e] / REF[band][e], 3)
                                 for e in r["d_acc"] if e in REF[band]}
            r["rho"] = round(base["per_conn_kbit_s"]
                             / float(BAND[band].split()[1].rstrip("kbit")), 3)
            out[comp][band] = r
            log(f"{comp:>9s} {band:<9s} p50={r['p50']} d_acc={r['d_acc']} "
                f"배수={r['ratio_vs_ref']} rho={r['rho']}")
    radio("none")
    json.dump(out, open(f"{OUT}/diag_dacc.json", "w"), ensure_ascii=False, indent=1)
    log(f"저장: {OUT}/diag_dacc.json")

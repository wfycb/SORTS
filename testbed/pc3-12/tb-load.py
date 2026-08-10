#!/usr/bin/env python3
"""SORTS 테스트베드 부하 생성기 v2.

v1 대비 추가:
  - 요청별 레코드 CSV (scheduled_ts, send_ts, end_ts, status, bytes_recv, request_id)
  - 두 가지 지연:
      service   = end_ts - send_ts      (서버 관점 응답시간)
      corrected = end_ts - scheduled_ts (coordinated omission 보정. 포화 구간의 진실)
  - 백분위 요약 p50/p95/p99/p99.9/max, 두 지연 각각
  - --warmup <sec> 구간은 요약에서 제외 (CSV 에는 warmup=1 플래그로 남김)
  - x-request-id 를 uuid4 로 직접 생성해 헤더로 실어 보내고 CSV 에 기록.
    front Envoy 가 preserve_external_request_id: true 이므로 그대로 보존되어
    로그 조인 키가 된다. (클라 Envoy 를 설치하지 않고 E2E 진실값을 얻는 방법)

coordinated omission 에 대하여:
  워커는 절대 스케줄(t_start + offset + k*interval)을 유지한다. 서버가 느려
  뒤처져도 스케줄을 재설정하지 않는다. 재설정하면 밀린 시간이 사라져
  포화 구간의 지연이 낙관적으로 보인다. v1 은 재설정했으므로 v1 의
  corrected 는 신뢰할 수 없다.

소스 IP:
  UE 주소는 코어(SMF)가 풀에서 할당하므로 재기동마다 바뀐다. --cohort 는
  /run/tb-cohort.map 을 읽어 해석하고 맵과 실제가 어긋나면 즉시 실패한다.
"""
from __future__ import annotations

import argparse
import csv
import http.client
import json
import subprocess
import sys
import threading
import time
import uuid

REQUEST_TIMEOUT_SEC = 5.0
COHORT_MAP = "/run/tb-cohort.map"

# 작업 C: 소스 포트 고정 (ISSUES.md I-8). tb-radio2.sh 의 버킷 해시 폭이
# 64(=DIVISOR)이므로 64를 더하면 같은 버킷에 남는다.
PORT_BUCKET_MOD = 64
PORT_LADDER_TRIES = 24
_PORT_USED: list = []
_PORT_RETRIES: list = []


# ---------------------------------------------------------------- 코호트 맵

def read_cohort_map(path: str) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) == 3:
                out[int(parts[0])] = (parts[1], parts[2])
    return out


def iface_addr(iface: str) -> str | None:
    try:
        r = subprocess.run(["ip", "-4", "-o", "addr", "show", iface],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.split()[3].split("/")[0]


def resolve_cohort(cohort: int, path: str) -> str:
    try:
        m = read_cohort_map(path)
    except FileNotFoundError:
        sys.exit(f"FATAL: {path} 없음. 런 시작 전 tb-cohort-map 을 실행하라.")
    if cohort not in m:
        sys.exit(f"FATAL: {path} 에 코호트 {cohort} 없음 (있는 것: {sorted(m)})")
    iface, addr = m[cohort]
    actual = iface_addr(iface)
    if actual != addr:
        sys.exit(f"FATAL: 코호트 맵과 실제가 어긋남 — {iface} 맵={addr} 실제={actual}")
    return addr


# ---------------------------------------------------------------- 워커

class Rec:
    __slots__ = ("conn", "seq", "sched", "send", "end", "status",
                 "nbytes", "rid", "warmup", "ep")

    def __init__(self, conn, seq, sched, send, end, status, nbytes, rid, warmup,
                 ep="-"):
        self.conn = conn
        self.seq = seq
        self.sched = sched
        self.send = send
        self.end = end
        self.status = status
        self.nbytes = nbytes
        self.rid = rid
        self.warmup = warmup
        self.ep = ep


def _new_conn(host, port, source_ip, local_port=0):
    """local_port>0 이면 소스 포트를 고정한다 (ISSUES.md I-8 / 작업 C).

    왜 고정하나: tb-radio2.sh v2 는 ogstun egress 에서 **dst 포트 하위
    6비트**로 코호트당 64개 버킷에 해싱하고 버킷마다 독립 netem 을 매단다.
    커널이 배정하는 ephemeral 포트를 그냥 쓰면 커넥션 16개가 64칸에 무작위로
    떨어져 생일문제로 매 런 평균 1.9쌍이 같은 버킷 = 같은 netem 큐를 공유한다
    (= I-8 '커넥션 복권'). 연속 포트 16개를 고정하면 하위 6비트가 전부 달라
    **충돌이 결정적으로 0** 이 된다.

    TIME_WAIT 대응: 재바인드 실패 시 포트를 **64씩** 올려 재시도한다.
    64의 배수를 더하면 하위 6비트가 보존되므로 **버킷이 그대로 유지**된다.
    (SO_REUSEADDR 은 커널/상황에 따라 거동이 갈려 쓰지 않는다.)
    """
    if not source_ip:
        return http.client.HTTPConnection(host, port,
                                          timeout=REQUEST_TIMEOUT_SEC)
    if local_port <= 0:
        return http.client.HTTPConnection(host, port,
                                          timeout=REQUEST_TIMEOUT_SEC,
                                          source_address=(source_ip, 0))
    last = None
    for r in range(PORT_LADDER_TRIES):
        p = local_port + PORT_BUCKET_MOD * r
        c = http.client.HTTPConnection(host, port, timeout=REQUEST_TIMEOUT_SEC,
                                       source_address=(source_ip, p))
        try:
            c.connect()          # 명시 연결 — bind 실패를 여기서 잡는다
            if r:
                _PORT_RETRIES.append((local_port, p))
            _PORT_USED.append(p)
            return c
        except OSError as e:
            last = e
            c.close()
    # 사다리를 다 써도 실패 = 포트 고정 보장이 깨진 것. 조용히 0 으로 떨어지면
    # 복권이 되살아나므로 **크게 알리고** 실패시킨다.
    raise RuntimeError(f"소스 포트 고정 실패 (base={local_port}, "
                       f"{PORT_LADDER_TRIES}회 시도): {last}")


def worker(idx, host, port, seq, interval, first_at, t_end, warmup_until,
           source_ip, out: list, local_port=0) -> None:
    # seq: [(label, path), ...] 를 순환한다. 단일 경로면 길이 1.
    # 결정적 순환이라 혼합 비율이 정확히 지켜진다 (랜덤 추출은 비율이 흔들린다).
    conn = _new_conn(host, port, source_ip, local_port)
    nseq = len(seq)
    k = 0
    while True:
        # 절대 스케줄. 뒤처져도 재설정하지 않는다 (coordinated omission 보정).
        sched = first_at + k * interval
        if sched >= t_end:
            break
        now = time.time()
        if sched > now:
            time.sleep(sched - now)
        ep_label, path = seq[(k + idx) % nseq]
        rid = str(uuid.uuid4())
        send = time.time()
        try:
            conn.request("GET", path, headers={"x-request-id": rid})
            resp = conn.getresponse()
            body = resp.read()
            end = time.time()
            status = str(resp.status)
            nbytes = len(body)
        except Exception as exc:
            end = time.time()
            status = f"err:{type(exc).__name__}"
            nbytes = -1
            conn.close()
            conn = _new_conn(host, port, source_ip, local_port)
        out.append(Rec(idx, k, sched, send, end, status, nbytes, rid,
                       1 if end < warmup_until else 0, ep_label))
        k += 1
    conn.close()


# ---------------------------------------------------------------- 통계

def pctl(xs_sorted, q):
    if not xs_sorted:
        return float("nan")
    i = int(round(q * (len(xs_sorted) - 1)))
    return xs_sorted[i]


def summarize(recs, label):
    ok = [r for r in recs if r.status == "200"]
    svc = sorted((r.end - r.send) * 1000.0 for r in ok)
    cor = sorted((r.end - r.sched) * 1000.0 for r in ok)
    by = sorted(r.nbytes for r in ok if r.nbytes >= 0)
    codes: dict[str, int] = {}
    for r in recs:
        codes[r.status] = codes.get(r.status, 0) + 1

    def block(xs):
        return {
            "p50": pctl(xs, 0.50), "p95": pctl(xs, 0.95),
            "p99": pctl(xs, 0.99), "p999": pctl(xs, 0.999),
            "max": xs[-1] if xs else float("nan"),
        }

    return {
        "label": label,
        "n_total": len(recs),
        "n_200": len(ok),
        "codes": codes,
        "service_ms": block(svc),
        "corrected_ms": block(cor),
        "bytes_median": pctl(by, 0.50) if by else float("nan"),
    }


def print_summary(s):
    print(f"--- {s['label']} ---")
    print(f"  요청 {s['n_total']}건 (200: {s['n_200']})  코드분포 {s['codes']}")
    for key, name in (("service_ms", "service  "), ("corrected_ms", "corrected")):
        b = s[key]
        print(f"  {name} p50={b['p50']:8.3f} p95={b['p95']:8.3f} "
              f"p99={b['p99']:8.3f} p99.9={b['p999']:8.3f} max={b['max']:8.3f} ms")
    print(f"  응답바이트 중앙값 = {s['bytes_median']}")


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.0.43")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--path", default="/")
    ap.add_argument("--source-ip", default=None)
    ap.add_argument("--cohort", type=int, default=None)
    ap.add_argument("--cohort-map", default=COHORT_MAP)
    ap.add_argument("--connections", type=int, default=5)
    ap.add_argument("--rps-per-connection", type=float, default=10.0)
    ap.add_argument("--warmup", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=60.0,
                    help="워밍업을 제외한 본측정 시간")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--json", default=None, help="요약을 JSON 으로도 저장")
    ap.add_argument("--label", default="")
    ap.add_argument("--mix", default=None,
                    help="혼합 부하. 'name=weight:path,...' 형식. --path 대신 쓴다. "
                         "가중치는 정수비로 환산해 결정적으로 순환한다.")
    ap.add_argument("--stagger", action="store_true", default=True)
    ap.add_argument("--no-stagger", dest="stagger", action="store_false")
    ap.add_argument("--port-base", type=int, default=0,
                    help="소스 포트 고정 시작값 (커넥션 i -> base+i). "
                         "0 이면 커널 배정(구동작). ISSUES.md I-8 참조 — "
                         "고정하면 tb-radio2.sh 버킷 충돌이 결정적으로 0.")
    args = ap.parse_args()
    if args.port_base and args.port_base % PORT_BUCKET_MOD:
        sys.exit(f"FATAL: --port-base 는 {PORT_BUCKET_MOD}의 배수여야 한다 "
                 f"(런마다 회전해도 16개가 서로 다른 버킷에 떨어지도록). "
                 f"받은 값 {args.port_base}")
    if args.port_base and args.connections > PORT_BUCKET_MOD:
        sys.exit(f"FATAL: 커넥션 {args.connections} > 버킷 {PORT_BUCKET_MOD} "
                 f"— 충돌 0 을 보장할 수 없다")

    if args.cohort is not None and args.source_ip is not None:
        sys.exit("FATAL: --cohort 와 --source-ip 는 함께 쓸 수 없다")
    source_ip = args.source_ip
    if args.cohort is not None:
        source_ip = resolve_cohort(args.cohort, args.cohort_map)

    # 요청 시퀀스 구성
    if args.mix:
        specs = []
        for part in args.mix.split(","):
            name, rest = part.split("=", 1)
            w, path = rest.split(":", 1)
            specs.append((name, float(w), path))
        # 가중치를 정수비로 (0.5 단위까지 허용)
        mult = 1
        while any(abs(w * mult - round(w * mult)) > 1e-9 for _, w, _ in specs):
            mult *= 2
            if mult > 1024:
                sys.exit("FATAL: --mix 가중치를 정수비로 환산할 수 없다")
        counts = [(n_, int(round(w * mult)), p_) for n_, w, p_ in specs]
        total = sum(c for _, c, _ in counts)
        # 라운드로빈 인터리브 (한 종류가 몰리지 않게)
        seq = []
        rem = {n_: c for n_, c, _ in counts}
        pathmap = {n_: p_ for n_, _, p_ in counts}
        while len(seq) < total:
            for n_, c, _ in counts:
                if rem[n_] > 0:
                    seq.append((n_, pathmap[n_]))
                    rem[n_] -= 1
        print("mix 시퀀스 길이=%d  구성=%s" % (
            len(seq), {n_: c for n_, c, _ in counts}), flush=True)
    else:
        seq = [("-", args.path)]

    n = args.connections
    interval = 1.0 / args.rps_per_connection
    stagger = interval / n if args.stagger else 0.0
    t_start = time.time() + 1.0
    warmup_until = t_start + args.warmup
    t_end = warmup_until + args.duration

    buckets: list[list] = [[] for _ in range(n)]
    threads = [
        threading.Thread(target=worker,
                         args=(i, args.host, args.port, seq, interval,
                               t_start + i * stagger, t_end, warmup_until,
                               source_ip, buckets[i],
                               args.port_base + i if args.port_base else 0),
                         daemon=True)
        for i in range(n)
    ]
    label = args.label or f"{args.host}:{args.port}{args.path.split('?')[0]}"
    print(f"start={t_start:.6f} host={args.host}:{args.port} src={source_ip} "
          f"conns={n} rps_total={n * args.rps_per_connection:.0f} "
          f"grid={(stagger if args.stagger else interval) * 1000:.2f}ms "
          f"warmup={args.warmup}s dur={args.duration}s "
          f"port_base={args.port_base}", flush=True)
    if args.port_base:
        # 의도한 버킷(=포트 하위 6비트)을 미리 찍는다. 러너의 실측 대조용.
        want = [(args.port_base + i) % PORT_BUCKET_MOD for i in range(n)]
        print("port_plan ports=%d-%d buckets=%s distinct=%d"
              % (args.port_base, args.port_base + n - 1,
                 ",".join(map(str, want)), len(set(want))), flush=True)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=args.warmup + args.duration + 60)

    if args.port_base:
        # 실제로 잡힌 포트 (사다리 재시도 포함). 러너 precheck 이 파싱한다.
        got = sorted(set(_PORT_USED))
        bk = sorted({p % PORT_BUCKET_MOD for p in _PORT_USED})
        print("port_actual n=%d distinct_ports=%d distinct_buckets=%d "
              "retries=%d buckets=%s"
              % (len(_PORT_USED), len(got), len(bk), len(_PORT_RETRIES),
                 ",".join(map(str, bk))), flush=True)
        if len(bk) != n:
            print("PORT_BUCKET_WARN 버킷 %d != 커넥션 %d — 충돌 0 보장이 "
                  "깨졌다 (I-8 재발)" % (len(bk), n), flush=True)

    recs = [r for b in buckets for r in b]
    recs.sort(key=lambda r: r.sched)

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["conn", "seq", "scheduled_ts", "send_ts", "end_ts",
                        "status", "bytes_recv", "request_id", "warmup",
                        "service_ms", "corrected_ms", "ep"])
            for r in recs:
                w.writerow([r.conn, r.seq, f"{r.sched:.6f}", f"{r.send:.6f}",
                            f"{r.end:.6f}", r.status, r.nbytes, r.rid, r.warmup,
                            f"{(r.end - r.send) * 1000:.3f}",
                            f"{(r.end - r.sched) * 1000:.3f}", r.ep])
        print(f"csv -> {args.csv} ({len(recs)} rows)", flush=True)

    measured = [r for r in recs if not r.warmup]
    s = summarize(measured, label)
    s["n_warmup_excluded"] = len(recs) - len(measured)
    print(f"워밍업 제외 {s['n_warmup_excluded']}건", flush=True)
    print_summary(s)
    if args.mix:
        for name in sorted({r.ep for r in measured}):
            sub = [r for r in measured if r.ep == name]
            print_summary(summarize(sub, f"{label} / {name}"))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(s, f, ensure_ascii=False)
        print(f"json -> {args.json}", flush=True)

    if args.cohort is not None:
        m = read_cohort_map(args.cohort_map)
        iface, addr = m[args.cohort]
        actual = iface_addr(iface)
        print(f"cohort check: {iface} 맵={addr} 실제={actual} "
              f"{'ok' if actual == addr else 'MISMATCH — 이 런의 코호트 라벨은 신뢰 불가'}",
              flush=True)


if __name__ == "__main__":
    main()

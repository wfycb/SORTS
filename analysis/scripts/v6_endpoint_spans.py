#!/usr/bin/env python3
"""STEP V6 — 표 5 (엔드포인트별 호출 구조).

Jaeger 에 애플리케이션 스팬이 하나도 없다 (§보고서 참조: jaeger v2 all-in-one 이
레거시 thrift-compact UDP 6831 을 더 이상 listen 하지 않는다. DSB 클라이언트는
const/1 샘플링으로 6831 로 UDP 를 쏘지만 받는 쪽이 없어 조용히 버려진다).
그래서 스팬 대신 다음 두 가지로 표 5 를 만든다.
  (a) 호출 구조 = DSB 소스(services/*/server.go) 정적 판독
  (b) 시간/부피 = 유휴 상태 단발 요청 실측 + memcached stats
부하는 걸지 않는다 — 엔드포인트당 소수의 순차 요청뿐이다.
"""
import csv
import json
import os
import socket
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import common as C  # noqa: E402

BASE = "http://192.168.0.40:5000"
D = "inDate=2015-04-09&outDate=2015-04-10"
URLS = {
    "search": f"{BASE}/hotels?{D}&lat=37.7867&lon=-122.4112",
    "recommend": f"{BASE}/recommendations?require=dis&lat=37.7867&lon=-122.4112",
    "reserve": (f"{BASE}/reservation?{D}&hotelId=1&customerName=dexp"
                f"&username=Cornell_30&password=0000000000&number=1"),
}
# reserve 는 실제로 예약을 만든다 — 횟수를 최소로 한다.
N = {"search": 12, "recommend": 12, "reserve": 3}

# (a) 소스 정적 판독 결과 (services/frontend|search|reservation/server.go)
STRUCT = {
    "search": {
        "services": ["frontend", "search", "geo", "rate", "reservation", "profile"],
        "path": "frontend → search → {geo, rate} ; frontend → reservation ; frontend → profile",
        "max_hops": 3,
        "stores": ["mongodb-geo", "memcached-rate(+mongodb-rate)",
                   "memcached-reserve(+mongodb-reservation)",
                   "memcached-profile(+mongodb-profile)"],
    },
    "recommend": {
        "services": ["frontend", "recommendation", "profile"],
        "path": "frontend → recommendation ; frontend → profile",
        "max_hops": 2,
        "stores": ["mongodb-recommendation", "memcached-profile(+mongodb-profile)"],
    },
    "reserve": {
        "services": ["frontend", "user", "reservation"],
        "path": "frontend → user ; frontend → reservation",
        "max_hops": 2,
        "stores": ["mongodb-user", "memcached-reserve(+mongodb-reservation)"],
    },
}

MEMC = {"rate": "172.18.0.7", "profile": "172.18.0.9", "reserve": "172.18.0.13"}


def memc_stats(ip):
    s = socket.create_connection((ip, 11211), 3)
    s.settimeout(4)
    s.sendall(b"stats\r\n")
    b = b""
    while b"END\r\n" not in b:
        d = s.recv(65536)
        if not d:
            break
        b += d
    s.close()
    return {p[1]: p[2] for p in (l.split() for l in b.decode().splitlines())
            if len(p) == 3}


def curl(url):
    t = time.time()
    r = subprocess.run(["curl", "-s", "-o", "/dev/null",
                        "-w", "%{http_code} %{size_download} %{time_total}", url],
                       capture_output=True, text=True, timeout=20)
    code, size, tt = r.stdout.split()
    return int(code), int(size), float(tt) * 1000.0


print("[단발 요청 실측 — 유휴 상태, 순차]")
meas = {}
for ep, url in URLS.items():
    lat, sizes, codes = [], set(), set()
    for i in range(N[ep]):
        c, s, ms = curl(url)
        codes.add(c)
        sizes.add(s)
        lat.append(ms)
        time.sleep(0.3)
    meas[ep] = {"n": len(lat), "codes": sorted(codes), "sizes": sorted(sizes),
                "p50": round(statistics.median(lat), 3),
                "min": round(min(lat), 3), "max": round(max(lat), 3)}
    print(f"  {ep:10s} n={len(lat):3d} code={sorted(codes)} bytes={sorted(sizes)} "
          f"p50={meas[ep]['p50']:.2f}ms  min={meas[ep]['min']:.2f} "
          f"max={meas[ep]['max']:.2f}")

print("\n[memcached stats]")
mst = {}
for name, ip in MEMC.items():
    d = memc_stats(ip)
    g, h = int(d["cmd_get"]), int(d["get_hits"])
    mst[name] = {"curr_items": int(d["curr_items"]), "cmd_get": g, "get_hits": h,
                 "get_misses": int(d["get_misses"]),
                 "hit_pct": round(100 * h / g, 3) if g else None,
                 "bytes": int(d.get("bytes", 0)),
                 "evictions": int(d.get("evictions", 0))}
    print(f"  {name:8s} items={mst[name]['curr_items']:>4d} "
          f"hit={mst[name]['hit_pct']:>7.3f}%  misses={mst[name]['get_misses']:>13,} "
          f"bytes={mst[name]['bytes']:>8,}")

# 런 실측 f_c (A_none, pre, S3) 를 같이 싣는다
fcp = os.path.join(C.ANA, "tables", "table3b_fc_by_site.csv")
fc = {}
if os.path.exists(fcp):
    import pandas as pd
    d = pd.read_csv(fcp)
    d = d[(d["disturb"] == "none") & (d["section"] == "pre") & (d["site"] == "S3")]
    # 엔드포인트 구분이 없는 표라 summary.json 에서 직접 가져온다
s = C.load_json("A_none_site_s3", "summary.json")
for ep, v in s["sections"]["pre"]["fc_ms"]["S3"].items():
    fc[ep] = (v["p50"], v["p95"])

rows = []
for ep in ("search", "reserve", "recommend"):
    st = STRUCT[ep]
    rows.append({
        "endpoint": ep,
        "span_count": "N/A (Jaeger 미수집)",
        "services": " > ".join(st["services"]),
        "n_services": len(st["services"]),
        "call_path": st["path"],
        "max_hops": st["max_hops"],
        "backing_stores": "; ".join(st["stores"]),
        "idle_total_ms_p50": meas[ep]["p50"],
        "idle_total_ms_min": meas[ep]["min"],
        "resp_bytes": meas[ep]["sizes"][0] if len(meas[ep]["sizes"]) == 1
        else str(meas[ep]["sizes"]),
        "fc_p50_ms_run": fc.get(ep, (None, None))[0],
        "fc_p95_ms_run": fc.get(ep, (None, None))[1],
    })

out = os.path.join(C.ANA, "tables", "table5_endpoint_spans.csv")
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
print(f"\n-> {out}")
for r in rows:
    print(f"  {r['endpoint']:10s} 서비스{r['n_services']} 홉{r['max_hops']} "
          f"유휴 p50 {r['idle_total_ms_p50']:.2f}ms  런 f_c p50/p95 "
          f"{r['fc_p50_ms_run']}/{r['fc_p95_ms_run']}ms")

json.dump({"measured": meas, "memcached": mst, "structure": STRUCT},
          open(os.path.join(C.ANA, "tables", "table5_raw.json"), "w"),
          ensure_ascii=False, indent=1)
print(f"-> {os.path.join(C.ANA, 'tables', 'table5_raw.json')}")

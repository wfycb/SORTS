#!/usr/bin/env python3
"""T2 검증 (지시서 v10 §3.3). 6-route 분리 라우팅의 동작 확인.

각 테스트: 7개 prefix 가중치 설정 -> 코호트 1·2 혼합 부하 30초 ->
access log 슬라이스에서 (XFF, path, upstream host) 로 판정.
"""
import subprocess
import time
import sys

LOADGEN = "192.168.0.12"
ENVOY = "192.168.0.43"
LOG = "/var/log/envoy/front_access.log"
PIN = "taskset -c 6-15"
CLUSTERS = ["site_s1", "site_s2", "site_s3", "bl_rr", "bl_lr", "bl_loc"]
PREFIXES = ["c1_search", "c1_reserve", "c1_recommend",
            "c2_search", "c2_reserve", "c2_recommend", "fallback"]
SITE_OF = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
D = "inDate=2015-04-09&outDate=2015-04-10"
MIX = (f"reserve=1:/reservation?{D}&hotelId=1&customerName=t2"
       f"&username=Cornell_30&password=0000000000&number=1,"
       f"search=1.5:/hotels?{D}&lat=37.7867&lon=-122.4112,"
       f"recommend=2:/recommendations?require=dis&lat=37.7867&lon=-122.4112")


def sh(cmd, timeout=300):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout).stdout.strip()


def set_weights(assign: dict):
    """assign: prefix -> 100 을 줄 클러스터. 나머지는 0."""
    parts = []
    for p in PREFIXES:
        tgt = assign.get(p, assign.get("*"))
        for c in CLUSTERS:
            parts.append(f"routing.{p}.{c}={100 if c == tgt else 0}")
    q = "&".join(parts)
    sh(f"ssh {ENVOY} \"curl -s -X POST 'http://127.0.0.1:9901/runtime_modify?{q}'\"", 60)


def load_and_slice(dur=30):
    off0 = int(sh(f"ssh {ENVOY} 'stat -c %s {LOG}'") or 0)
    ps = []
    for c in (1, 2):
        ps.append(subprocess.Popen(
            f'ssh {LOADGEN} "{PIN} python3 ~/tb-load.py --host {ENVOY} --port 8080 '
            f"--cohort {c} --mix '{MIX}' --connections 2 --rps-per-connection 25 "
            f'--warmup 3 --duration {dur} --label t2-c{c}" >/dev/null 2>&1',
            shell=True))
    for p in ps:
        p.wait(timeout=dur + 60)
    time.sleep(1)
    off1 = int(sh(f"ssh {ENVOY} 'stat -c %s {LOG}'") or 0)
    raw = sh(f"ssh {ENVOY} \"tail -c +{off0+1} {LOG} | head -c {off1-off0}\"", 300)
    rows = []
    for line in raw.splitlines():
        f = line.split(",")
        if len(f) < 15:
            continue
        path = f[3]
        xff = f[12]
        host = f[10].split(":")[0]
        rows.append((xff, path.split("?")[0], SITE_OF.get(host, host)))
    return rows


def dist(rows):
    d = {}
    for _, _, s in rows:
        d[s] = d.get(s, 0) + 1
    tot = sum(d.values()) or 1
    return {k: round(100 * v / tot, 1) for k, v in sorted(d.items())}, tot


def main():
    c1, c2 = sys.argv[1], sys.argv[2]
    ep_of = {"/hotels": "search", "/reservation": "reserve", "/recommendations": "recommend"}

    print("=== 검증 1~3: 6 prefix 동일 가중치 -> 기존 분배 재현 ===")
    for pol, expect in (("bl_rr", "33/33/33"), ("bl_loc", "57/29/14"), ("site_s3", "0/0/100")):
        set_weights({"*": pol})
        rows = load_and_slice()
        d, tot = dist(rows)
        print(f"  {pol:8s} n={tot:5d}  분배={d}   기대={expect}")

    print("=== 검증 4: 분리 동작 — c1_search 만 site_s1, 나머지 site_s3 ===")
    set_weights({"*": "site_s3", "c1_search": "site_s1"})
    rows = load_and_slice()
    unit = {}
    fallback_hits = []
    for xff, path, site in rows:
        coh = "c1" if xff == c1 else ("c2" if xff == c2 else "??")
        if coh == "??":
            fallback_hits.append((xff, path, site))
            continue
        key = f"{coh}_{ep_of.get(path, path)}"
        unit.setdefault(key, {}).setdefault(site, 0)
        unit[key][site] += 1
    ok = True
    for k in sorted(unit):
        exp = "S1" if k == "c1_search" else "S3"
        sites = unit[k]
        pure = list(sites.keys()) == [exp]
        ok &= pure
        print(f"  {k:14s} {sites}   기대={exp} 순도={'O' if pure else 'X'}")
    print(f"  비코호트 XFF (폴백행): {len(fallback_hits)}건")

    print("=== 검증 5: 폴백 route — UE 미도달 + LAN 직행은 도달 ===")
    # 폴백만 site_s2 로 구분해 두고 UE 부하를 다시 건다. UE 트래픽에 S2 가
    # 나오면 폴백 매칭 실패(코호트 route 를 놓침)다.
    set_weights({"*": "site_s3", "fallback": "site_s2"})
    rows = load_and_slice(15)
    ue_s2 = [r for r in rows if r[0] in (c1, c2) and r[2] == "S2"]
    print(f"  UE 트래픽 중 S2(폴백) 도달: {len(ue_s2)}건 (0 이어야 함)")
    # LAN 직행 curl -> 폴백을 타고 S2 로 가야 한다
    off0 = int(sh(f"ssh {ENVOY} 'stat -c %s {LOG}'") or 0)
    sh(f"ssh {LOADGEN} \"curl -s -o /dev/null 'http://{ENVOY}:8080/hotels?{D}&lat=37.7867&lon=-122.4112'\"")
    time.sleep(1)
    off1 = int(sh(f"ssh {ENVOY} 'stat -c %s {LOG}'") or 0)
    raw = sh(f"ssh {ENVOY} \"tail -c +{off0+1} {LOG} | head -c {off1-off0}\"")
    for line in raw.splitlines():
        f = line.split(",")
        if len(f) >= 15:
            print(f"  LAN 직행: XFF={f[12]} -> {SITE_OF.get(f[10].split(':')[0])} (기대 S2=폴백)")

    set_weights({"*": "site_s3"})
    print("완료 — 가중치 전부 site_s3 원복")


if __name__ == "__main__":
    main()

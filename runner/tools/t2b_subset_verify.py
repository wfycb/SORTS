#!/usr/bin/env python3
"""작업 A §2.3 순도 검증. t2_verify.py 와 같은 방식.

sub_* 클러스터마다: 7 prefix 가중치를 그 클러스터로 -> 코호트 1·2 혼합
부하 -> access log 슬라이스에서 (필드10, 필드11) 판정.

정지 조건: 허용 집합 밖 사이트 도달 1건이라도 있으면 실패.
부수 확인: LEAST_REQUEST 분배 비율, 신판 obs 필터의 표본 포함(라이브 §5.2).
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obs  # noqa: E402  (신판 필터로 표본 포함 확인)
import yaml  # noqa: E402

LOADGEN = "192.168.0.12"
ENVOY = "192.168.0.43"
LOG = "/var/log/envoy/front_access.log"
PIN = "taskset -c 6-15"
EK = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "envoy_keys.json")))
PREFIXES = EK["route_prefixes"]
CLUSTERS = EK["cluster_keys"]
SITE_OF_IP = {ip: s for s, ip in EK["site_ip"].items()}
D = "inDate=2015-04-09&outDate=2015-04-10"
MIX = (f"reserve=1:/reservation?{D}&hotelId=1&customerName=t2b"
       f"&username=Cornell_30&password=0000000000&number=1,"
       f"search=1.5:/hotels?{D}&lat=37.7867&lon=-122.4112,"
       f"recommend=2:/recommendations?require=dis&lat=37.7867&lon=-122.4112")


def sh(cmd, timeout=300):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout).stdout.strip()


def set_weights(target):
    parts = [f"routing.{p}.{c}={100 if c == target else 0}"
             for p in PREFIXES for c in CLUSTERS]
    sh(f"ssh {ENVOY} \"curl -s -X POST "
       f"'http://127.0.0.1:9901/runtime_modify?{'&'.join(parts)}'\"", 60)


def load_and_slice(dur=15):
    off0 = int(sh(f"ssh {ENVOY} 'stat -c %s {LOG}'") or 0)
    ps = []
    for c in (1, 2):
        ps.append(subprocess.Popen(
            f'ssh {LOADGEN} "{PIN} python3 ~/tb-load.py --host {ENVOY} --port 8080 '
            f"--cohort {c} --mix '{MIX}' --connections 2 --rps-per-connection 25 "
            f'--warmup 3 --duration {dur} --label t2b-c{c}" >/dev/null 2>&1',
            shell=True))
    for p in ps:
        p.wait(timeout=dur + 60)
    time.sleep(1)
    off1 = int(sh(f"ssh {ENVOY} 'stat -c %s {LOG}'") or 0)
    return sh(f"ssh {ENVOY} \"tail -c +{off0+1} {LOG} | head -c {off1-off0}\"", 300)


def main():
    c1, c2 = sys.argv[1], sys.argv[2]
    cfg = yaml.safe_load(open(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "sorts.yaml")))
    fail = 0
    for cl in EK["sorts_clusters"]:
        if not cl.startswith("sub_"):
            continue
        allowed = set(EK["cluster_sites"][cl])
        set_weights(cl)
        raw = load_and_slice()
        ob = obs.Observer(cfg, log_path=None)
        dist, n_ue, n_leak, n_other10 = {}, 0, 0, 0
        for line in raw.splitlines():
            f = line.split(",")
            if len(f) < obs.N_FIELDS:
                continue
            ob.ingest_line(line, 0.0)
            if f[12] not in (c1, c2):        # 관리용/폴백 행 제외
                continue
            n_ue += 1
            if f[obs.F_UPSTREAM_CLUSTER] != cl:
                n_other10 += 1
                continue
            site = SITE_OF_IP.get(f[obs.F_UPSTREAM_HOST].split(":")[0], "?")
            dist[site] = dist.get(site, 0) + 1
            if site not in allowed:
                n_leak += 1
        tot = sum(dist.values()) or 1
        pct = {k: round(100 * v / tot, 1) for k, v in sorted(dist.items())}
        pure = n_leak == 0 and n_other10 == 0 and ob.n_subset_mismatch == 0
        fail += not pure
        print(f"{cl:9s} UE행 {n_ue:5d} 분배={pct} 집합밖누출={n_leak} "
              f"타클러스터={n_other10} obs포함={ob.n_used} "
              f"obs순도카운터={ob.n_subset_mismatch} "
              f"{'순도 O' if pure else '★순도 위반'}")
    set_weights("site_s3")
    print("가중치 site_s3 원복 완료 —", "전부 통과" if fail == 0 else f"실패 {fail}건")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())

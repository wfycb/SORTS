#!/usr/bin/env python3
"""야간배치 과제1: 용량 무릎 분석.

런 디렉터리들(사이트 고정 정책, disturb=none)에서 request_id 조인으로
f_c(=Envoy 필드18 − d_net[site])를 요청 단위로 재구성해 클래스별
p50/p95/p99, 위반율, 달성 rps 를 도착률별로 표로 만든다.

무릎 정의 (PROGRESS.md 사전 등록):
  (i)  f_c p95 > (최저 도착률 지점의 f_c p95) × 2 인 최소 도착률
  (ii) f_c p95 > SLO − GB − d_net(site)  (무선 무제한이라 d_acc=0)
  보조: 달성 rps < 목표의 95%

사용: python3 knee.py <rundir1> <rundir2> ... --csv out.csv
"""
import argparse
import csv
import gzip
import json
import os
import sys

SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
GB = 5.0
D_NET = {"S1": 2.0, "S2": 15.0, "S3": 25.006}
EXPECT_BYTES = {"reserve": 36, "search": 4474, "recommend": 200}
SITE_OF_IP = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}


def pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[int(round(q * (len(xs) - 1)))], 3)


def is_valid(r):
    if r["status"] != "200":
        return False
    e = EXPECT_BYTES.get(r["ep"])
    if e is None:
        return False
    b = int(r["bytes_recv"])
    return abs(b - e) <= (e * 0.10 if e > 1000 else 0)


def one_run(rd):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    hm = {}
    with gzip.open(os.path.join(rd, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) > 17 and p[17].strip().isdigit():
                hm[p[1]] = (p[10].split(":")[0], int(p[17]))
    rows = []
    for c in (1, 2):
        fp = os.path.join(rd, f"load_c{c}.csv")
        if not os.path.exists(fp):
            continue
        for r in csv.DictReader(open(fp)):
            if r["warmup"] != "0":
                continue
            rows.append(r)
    per = {}          # (site, ep) -> dict of lists
    n_join = 0
    t_lo = min(float(r["end_ts"]) for r in rows) if rows else 0
    t_hi = max(float(r["end_ts"]) for r in rows) if rows else 1
    for r in rows:
        h = hm.get(r["request_id"])
        if h is None:
            continue
        n_join += 1
        site = SITE_OF_IP.get(h[0], "?")
        d = per.setdefault((site, r["ep"]), {"fc": [], "svc": [], "cor": [],
                                             "n": 0, "viol": 0})
        d["n"] += 1
        ok = is_valid(r)
        if ok:
            d["fc"].append(h[1] / 1000.0 - D_NET[site])
            d["svc"].append(float(r["service_ms"]))
            d["cor"].append(float(r["corrected_ms"]))
        if not ok or float(r["corrected_ms"]) > SLO[r["ep"]]:
            d["viol"] += 1
    out = []
    ach = round(n_join / max(t_hi - t_lo, 1e-9), 1)
    for (site, ep), d in sorted(per.items()):
        out.append({
            "run_id": meta["run_id"], "target_rps": meta.get("total_rps"),
            "mix": "search_only" if "search=1:" in (meta.get("mix") or "")
                   and "reserve" not in (meta.get("mix") or "") else "mixed",
            "site": site, "class": ep, "n": d["n"],
            "join_rate": round(n_join / len(rows), 4) if rows else None,
            "achieved_rps_total": ach,
            "fc_p50": pctl(d["fc"], .50), "fc_p95": pctl(d["fc"], .95),
            "fc_p99": pctl(d["fc"], .99),
            "svc_p95": pctl(d["svc"], .95),
            "cor_p95": pctl(d["cor"], .95),
            "viol_rate": round(d["viol"] / d["n"], 6) if d["n"] else None,
        })
    return out


def knees(rows, site):
    """클래스별 무릎 두 정의. rows 는 같은 스윕(mix 동일)의 도착률 오름차순."""
    res = {}
    by_ep = {}
    for r in rows:
        if r["site"] != site or r["fc_p95"] is None:
            continue
        by_ep.setdefault(r["class"], []).append(r)
    for ep, rs in by_ep.items():
        rs.sort(key=lambda r: r["target_rps"])
        base = rs[0]["fc_p95"]
        thr_slo = SLO[ep] - GB - D_NET[site]
        k2x = next((r["target_rps"] for r in rs if r["fc_p95"] > 2 * base), None)
        kslo = next((r["target_rps"] for r in rs if r["fc_p95"] > thr_slo), None)
        kach = next((r["target_rps"] for r in rs
                     if r["achieved_rps_total"] < 0.95 * r["target_rps"]), None)
        res[ep] = {"fc_p95_base": base, "knee_2x_base": k2x,
                   "slo_budget_ms": round(thr_slo, 1), "knee_slo": kslo,
                   "knee_achieved95": kach}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rundirs", nargs="+")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--site", default="S1")
    a = ap.parse_args()
    allrows = []
    for rd in a.rundirs:
        if not os.path.exists(os.path.join(rd, "DONE")):
            print(f"# {rd}: DONE 없음 — 제외", file=sys.stderr)
            continue
        if os.path.exists(os.path.join(rd, "SUSPECT")):
            print(f"# {rd}: SUSPECT — 표기만, 포함", file=sys.stderr)
        allrows += one_run(rd)
    cols = list(allrows[0].keys())
    with open(a.csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(allrows)
    for mix in ("mixed", "search_only"):
        sub = [r for r in allrows if r["mix"] == mix]
        if not sub:
            continue
        print(f"\n== {mix} 스윕 무릎 ({a.site}) ==")
        print(json.dumps(knees(sub, a.site), ensure_ascii=False, indent=1))
    print(f"\nCSV -> {a.csv}  ({len(allrows)} 행)")


if __name__ == "__main__":
    main()

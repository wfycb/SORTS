#!/usr/bin/env python3
"""야간배치 과제2: far_tier vs strict_far 반복 — 승격 판정 입력 계산.

런마다 (c1only/both 창) ×:
  - 전체/코호트별/클래스별 위반율 (corrected_ms 기준, 바이트 판정 포함)
  - 커넥션 단위 분해: 커넥션별 위반율 분포 (다중도는 포트 고정으로 32/32
    동일하게 설계됨 — SUSPECT 아닌 런만 유효)
  - 사이트별 유입 rps·svc p95·f_c p95·큐잉 여유(slack = SLO−GB−d_net−fc_p95)
arm 집계: 평균, 반범위(=(max−min)/2), 판정 기준 1/2/3 계산.

사용: python3 t2_policy_repeat.py <rundir> ... --json out.json
"""
import argparse
import csv
import gzip
import json
import os

SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
GB = 5.0
D_NET = {"S1": 2.0, "S2": 15.0, "S3": 25.006}
EXPECT_BYTES = {"reserve": 36, "search": 4474, "recommend": 200}
SITE_OF_IP = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
GUARD = 2.0


def pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[int(round(q * (len(xs) - 1)))], 3)


def is_valid(r):
    if r["status"] != "200":
        return False
    e = EXPECT_BYTES.get(r["ep"])
    b = int(r["bytes_recv"])
    return e is not None and abs(b - e) <= (e * 0.10 if e > 1000 else 0)


def windows(m):
    d12, d43 = m["clock"]["d12_s"], m["clock"]["d43_s"]
    mk = {x["what"]: x for x in m["marks"]}
    w = {"c1only": ("c1_extreme", "c2_extreme"), "both": ("c2_extreme", "clear_all")}
    out = {}
    for k, (a, b) in w.items():
        lo43 = mk[a]["t43_done"] + GUARD
        hi43 = mk[b]["t_issue"] + d43 - GUARD
        out[k] = (lo43 - d43 + d12, hi43 - d43 + d12)
    return out


def arm_of(meta):
    if meta["policy"] != "sorts_reactive":
        return meta["policy"]
    return meta["arm"]["effective"]["subset_policy"]


def one_run(rd):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    win = windows(meta)
    hm = {}
    with gzip.open(os.path.join(rd, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) > 17 and p[17].strip().isdigit():
                hm[p[1]] = (p[10].split(":")[0], int(p[17]))
    res = {"run_id": meta["run_id"], "arm": arm_of(meta),
           "suspect": os.path.exists(os.path.join(rd, "SUSPECT")),
           "windows": {}}
    for wname, (lo, hi) in win.items():
        tot = viol = 0
        by = {}
        per_conn = {}
        site = {}
        for c in (1, 2):
            for r in csv.DictReader(open(os.path.join(rd, f"load_c{c}.csv"))):
                if r["warmup"] != "0":
                    continue
                t = float(r["end_ts"])
                if not (lo <= t <= hi):
                    continue
                bad = (not is_valid(r)) or float(r["corrected_ms"]) > SLO[r["ep"]]
                tot += 1
                viol += bad
                k = (c, r["ep"])
                d = by.setdefault(k, [0, 0])
                d[0] += 1
                d[1] += bad
                pc = per_conn.setdefault((c, int(r["conn"])), [0, 0])
                pc[0] += 1
                pc[1] += bad
                h = hm.get(r["request_id"])
                if h:
                    s = SITE_OF_IP.get(h[0], "?")
                    sd = site.setdefault((s, r["ep"]),
                                         {"n": 0, "viol": 0, "svc": [], "fc": []})
                    sd["n"] += 1
                    sd["viol"] += bad
                    if is_valid(r):
                        sd["svc"].append(float(r["service_ms"]))
                        sd["fc"].append(h[1] / 1000.0 - D_NET[s])
        conn_rates = sorted(100 * v / n for (c, _), (n, v) in per_conn.items() if n)
        res["windows"][wname] = {
            "dur_s": round(hi - lo, 1), "n": tot,
            "viol_pct": round(100 * viol / tot, 3) if tot else None,
            "by_cohort_class": {f"c{c}_{ep}": {"n": n, "viol_pct": round(100 * v / n, 3)}
                                for (c, ep), (n, v) in sorted(by.items())},
            "per_conn_viol_pct": {"n_conns": len(conn_rates),
                                  "min": round(conn_rates[0], 2) if conn_rates else None,
                                  "p50": pctl(conn_rates, .5),
                                  "max": round(conn_rates[-1], 2) if conn_rates else None},
            "site_class": {f"{s}_{ep}": {
                "n": d["n"], "rps": round(d["n"] / (hi - lo), 1),
                "viol_pct": round(100 * d["viol"] / d["n"], 2),
                "svc_p95": pctl(d["svc"], .95), "fc_p95": pctl(d["fc"], .95),
                "slack_ms": (round(SLO[ep] - GB - D_NET[s] - pctl(d["fc"], .95), 2)
                             if d["fc"] else None)}
                for (s, ep), d in sorted(site.items())},
        }
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rundirs", nargs="+")
    ap.add_argument("--json", required=True)
    a = ap.parse_args()
    runs = [one_run(rd) for rd in a.rundirs
            if os.path.exists(os.path.join(rd, "DONE"))]
    arms = {}
    for r in runs:
        if r["suspect"]:
            continue
        arms.setdefault(r["arm"], []).append(r["windows"]["both"]["viol_pct"])
    agg = {}
    for arm, vs in arms.items():
        agg[arm] = {"n": len(vs), "runs_viol_pct": vs,
                    "mean": round(sum(vs) / len(vs), 3),
                    "half_range": round((max(vs) - min(vs)) / 2, 3)}
    verdict = None
    if "far_tier" in agg and "strict_far" in agg and \
            agg["far_tier"]["n"] >= 3 and agg["strict_far"]["n"] >= 3:
        diff = abs(agg["far_tier"]["mean"] - agg["strict_far"]["mean"])
        hrsum = agg["far_tier"]["half_range"] + agg["strict_far"]["half_range"]
        verdict = {"crit1_mean_diff": round(diff, 3),
                   "crit1_half_range_sum": round(hrsum, 3),
                   "crit1_pass": diff > hrsum}
    out = {"runs": runs, "arm_agg": agg, "crit1": verdict}
    json.dump(out, open(a.json, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"arm_agg": agg, "crit1": verdict},
                     ensure_ascii=False, indent=1))
    for r in runs:
        w = r["windows"]["both"]
        print(f"{r['run_id']:>14s} {r['arm']:>10s} both={w['viol_pct']}% "
              f"conn p50/max={w['per_conn_viol_pct']['p50']}/{w['per_conn_viol_pct']['max']}"
              f"{'  [SUSPECT]' if r['suspect'] else ''}")


if __name__ == "__main__":
    main()

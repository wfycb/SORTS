#!/usr/bin/env python3
"""과제3 분석 — 노드 장애(S3:5000 차단 t+120~t+240) 5개 측정 (§3.4).

1. 감지: S3 가 허용 집합(feasible_set)에서 빠지는가, 차단 후 몇 초 만에.
2. f_c_src 전이: obs_state S3 행의 src obs→prior 시각과 그때 value(낙관?).
3. 재진입: 차단 창 안에서 S3 가 집합에 복귀하는가 (=I-6 발현).
4. 위반율·손실: 차단 창의 SLO 위반율, 비정상 응답(비 200/바이트 이탈) 수.
5. 복구: 해제 후 S3 가 집합·실 트래픽에 돌아오는 시각.
시계: decisions/obs_state ts = .43, load end_ts = .12. meta.clock 으로 정렬.
"""
import csv
import glob
import gzip
import json
import os
from collections import defaultdict

SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
EXPECT = {"reserve": 36, "search": 4474, "recommend": 200}
SITE = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
RUNS = "/home/user/exp/runs/night2-20260811/nf"


def one(rd):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    t_meas = meta["t_meas"]                      # .12
    d12 = meta["clock"]["d12_s"]
    d43 = meta["clock"]["d43_s"]
    # 차단 창 (러너 .40 시계 기준 예약 — .12 로는 t_meas+120/+240)
    blk_lo12, blk_hi12 = t_meas + 120, t_meas + 240

    def ts43_to_rel(ts):                          # .43 ts -> 본측정 상대초(.12)
        return (float(ts) - d43 + d12) - t_meas

    # 1·3·5) feasible_set 의 S3 포함률 (tick 단위, 6유닛 중 S3 포함 유닛 수)
    s3_frac = {}
    soft_ticks = defaultdict(int)
    for r in csv.DictReader(open(os.path.join(rd, "decisions.csv"))):
        t = ts43_to_rel(r["ts"])
        b = int(t)
        s3_frac.setdefault(b, [0, 0])
        s3_frac[b][1] += 1
        if "S3" in (r["feasible_set"] or ""):
            s3_frac[b][0] += 1
        if r.get("soft_applied") == "1":
            soft_ticks[b] += 1
    series = {b: v[0] / v[1] for b, v in sorted(s3_frac.items()) if v[1]}
    # 감지 = 차단 후 S3 포함률이 0 이 되는 첫 시각; 재진입 = 그 뒤 창 안 >0
    detect = next((b for b in sorted(series) if 120 <= b < 240
                   and series[b] == 0.0), None)
    reentry = None
    if detect is not None:
        reentry = [b for b in sorted(series) if detect < b < 240
                   and series[b] > 0.0]
    recover = next((b for b in sorted(series) if b >= 240
                    and series[b] >= 0.99), None)
    pre_frac = [series[b] for b in sorted(series) if 60 <= b < 120]

    # 2) obs_state S3: src 전이·값
    trans = []
    prev_src = {}
    for r in csv.DictReader(open(os.path.join(rd, "obs_state.csv"))):
        if r["site"] != "S3":
            continue
        t = ts43_to_rel(r["ts"])
        key = r["class"]
        src = r["src"]
        if prev_src.get(key) == "obs" and src == "prior" and 118 <= t < 245:
            trans.append({"class": key, "t_rel": round(t, 1),
                          "value_ms": float(r["value_ms"]),
                          "prior_ms": float(r["prior_ms"]),
                          "last_obs_ms": float(r["last_obs_ms"])
                          if r["last_obs_ms"] else None})
        prev_src[key] = src

    # 4) 차단 창 위반·손실 (+ 전 창 대비)
    win = {"pre": (0, 120), "block": (120, 240), "post": (240, 360)}
    stats = {k: {"n": 0, "viol": 0, "bad": 0} for k in win}
    for c in (1, 2):
        for r in csv.DictReader(open(os.path.join(rd, f"load_c{c}.csv"))):
            if r["warmup"] != "0":
                continue
            t = float(r["end_ts"]) - t_meas
            for k, (a, b) in win.items():
                if a <= t < b:
                    st = stats[k]
                    st["n"] += 1
                    e = EXPECT[r["ep"]]
                    ok = (r["status"] == "200" and abs(int(r["bytes_recv"]) - e)
                          <= (e * 0.10 if e > 1000 else 0))
                    if not ok:
                        st["bad"] += 1
                    if not ok or float(r["corrected_ms"]) > SLO[r["ep"]]:
                        st["viol"] += 1
    for k in stats:
        n = stats[k]["n"]
        stats[k]["viol_pct"] = round(100 * stats[k]["viol"] / n, 3) if n else None
        stats[k]["bad_pct"] = round(100 * stats[k]["bad"] / n, 3) if n else None

    # 5) 실 트래픽 복귀 (envoy 조인 — S3 도착 rps 시계열, post 창)
    hm = {}
    with gzip.open(os.path.join(rd, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) > 10:
                hm[p[1]] = SITE.get(p[10].split(":")[0])
    s3_traffic = defaultdict(int)
    for c in (1, 2):
        for r in csv.DictReader(open(os.path.join(rd, f"load_c{c}.csv"))):
            if r["warmup"] != "0":
                continue
            if hm.get(r["request_id"]) == "S3":
                s3_traffic[int(float(r["end_ts"]) - t_meas)] += 1
    first_back = next((b for b in sorted(s3_traffic) if b >= 240
                       and s3_traffic[b] > 50), None)

    return {"run_id": os.path.basename(rd),
            "s3_incl_pre_mean": round(sum(pre_frac) / len(pre_frac), 3)
            if pre_frac else None,
            "detect_rel_s": detect,
            "detect_lag_after_block": (detect - 120) if detect is not None
            else None,
            "reentry_ticks_in_block": (len(reentry) if reentry is not None
                                       else None),
            "reentry_times": (reentry[:10] if reentry else []),
            "stale_transitions": trans[:8],
            "windows": stats,
            "soft_ticks_by_min": {f"{60*i}-{60*(i+1)}":
                                  sum(v for b, v in soft_ticks.items()
                                      if 60 * i <= b < 60 * (i + 1))
                                  for i in range(6)},
            "s3_first_traffic_after_unblock": first_back}


def main():
    out = [one(rd) for rd in sorted(glob.glob(os.path.join(RUNS, "nf_*")))
           if os.path.exists(os.path.join(rd, "DONE"))]
    json.dump(out, open("/home/user/exp/analysis/night2/nf_results.json", "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""F1 원자료 → CSV 추출 (원자료를 다시 훑지 않고 스타일을 고칠 수 있게 분리).

입력: runs/stage5-20260812/{s5_sorts,s5_lr,s5_loc}_L{450,800}_1
출력(figures/data/):
  f1_L{L}_latency.csv    1 s 버킷 × 정책 — service/corrected p50·p95·n·요청순번
  f1_L{L}_points.csv     요청 단위 산점도용 (정책별 최대 6000점 균등 추출)
  f1_L{L}_sites.csv      1 s 버킷 × 정책 — S1/S2/S3 완료 건수(분배)
  f1_L{L}_slack.csv      SORTS tick × 사이트 — slack(ms) + 관측 밴드(kbit)
  f1_L{L}_marks.csv      교란 시각(정책별 상대초)
시간 원점 = 각 런의 t_meas(본측정 시작), 축은 상대초.
"""
import argparse
import csv
import gzip
import json
import os
from collections import defaultdict

EXP = "/home/user/exp"
RUNS = os.path.join(EXP, "runs/stage5-20260812")
OUT = os.path.join(EXP, "figures/data")
POLS = [("SORTS", "s5_sorts"), ("bl_lr", "s5_lr"), ("bl_loc_pri", "s5_loc")]
SITE_OF_IP = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
MARKS = [("c1_extreme", "c1 band 1600k"), ("c2_extreme", "both cohorts 1600k"),
         ("clear_all", "band cleared")]


def pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[int(round(q * (len(xs) - 1)))], 2)


def load_rows(rd, meta, ep="search"):
    """요청 단위 행 — (t_rel, service, corrected, request_id, ok)."""
    t0 = meta["t_meas"]
    rows = []
    for c in (1, 2):
        p = os.path.join(rd, f"load_c{c}.csv")
        for r in csv.DictReader(open(p)):
            if r["warmup"] != "0" or (ep and r["ep"] != ep):
                continue
            rows.append((float(r["end_ts"]) - t0, float(r["service_ms"]),
                         float(r["corrected_ms"]), r["request_id"],
                         r["status"] == "200"))
    rows.sort()
    return rows


def hostmap(rd):
    hm = {}
    with gzip.open(os.path.join(rd, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) > 17 and p[17].strip().isdigit():
                hm[p[1]] = SITE_OF_IP.get(p[10].split(":")[0], "?")
    return hm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", type=int, default=450)
    a = ap.parse_args()
    L = a.load
    os.makedirs(OUT, exist_ok=True)

    lat_rows, pt_rows, site_rows, mark_rows, cls_rows = [], [], [], [], []
    for pol, pfx in POLS:
        rd = os.path.join(RUNS, f"{pfx}_L{L}_1")
        meta = json.load(open(os.path.join(rd, "meta.json")))
        t0, d12, d43 = meta["t_meas"], meta["clock"]["d12_s"], meta["clock"]["d43_s"]
        mk = {x["what"]: x for x in meta["marks"]}
        for what, label in MARKS:
            mark_rows.append({"policy": pol, "mark": what, "label": label,
                              "t_rel_s": round(mk[what]["t43_done"] - d43 + d12 - t0, 2)})

        rows = load_rows(rd, meta)
        # --- 1 s 버킷 (search)
        buckets = defaultdict(list)
        for t, svc, cor, _rid, ok in rows:
            if ok:
                buckets[int(t)].append((svc, cor))
        # 위반 = corrected > SLO(search 45 ms) — 판정 산식과 동일
        cum = 0
        for sec in sorted(buckets):
            v = buckets[sec]
            svc = [x[0] for x in v]
            cor = [x[1] for x in v]
            viol = sum(1 for _s, c in v if c > 45.0)
            lat_rows.append({"policy": pol, "t_rel_s": sec, "req_idx_start": cum,
                             "n": len(v),
                             "viol_pct": round(100 * viol / len(v), 3),
                             "service_p50": pctl(svc, .5), "service_p95": pctl(svc, .95),
                             "corrected_p50": pctl(cor, .5), "corrected_p95": pctl(cor, .95)})
            cum += len(v)
        # --- 산점도용 균등 추출 (정책당 ≤6000점)
        step = max(1, len(rows) // 6000)
        for i, (t, svc, cor, _rid, ok) in enumerate(rows):
            if i % step:
                continue
            pt_rows.append({"policy": pol, "t_rel_s": round(t, 3), "req_idx": i,
                            "service_ms": round(svc, 2), "corrected_ms": round(cor, 2),
                            "ok": int(ok)})
        # --- 사이트 분배 (전 클래스 — 라우팅 그림이므로 search 한정 아님)
        hm = hostmap(rd)
        allrows = load_rows(rd, meta, ep=None)
        per = defaultdict(lambda: defaultdict(int))
        for t, _s, _c, rid, ok in allrows:
            if ok:
                per[int(t)][hm.get(rid, "?")] += 1
        for sec in sorted(per):
            d = per[sec]
            tot = sum(v for k, v in d.items() if k in ("S1", "S2", "S3")) or 1
            site_rows.append({"policy": pol, "t_rel_s": sec,
                              **{s: d.get(s, 0) for s in ("S1", "S2", "S3")},
                              **{f"{s}_pct": round(100 * d.get(s, 0) / tot, 2)
                                 for s in ("S1", "S2", "S3")}})
        # 클래스별 사이트 몫 (3단 = search 전용 + 참조선, _byclass 예비판)
        perc = defaultdict(lambda: defaultdict(int))
        for t, _s, _c, rid, ok in allrows:
            pass
        for c in (1, 2):
            for r in csv.DictReader(open(os.path.join(rd, f"load_c{c}.csv"))):
                if r["warmup"] != "0" or r["status"] != "200":
                    continue
                sec = int(float(r["end_ts"]) - meta["t_meas"])
                perc[(sec, r["ep"])][hm.get(r["request_id"], "?")] += 1
        for (sec, ep), d in sorted(perc.items()):
            tot = sum(v for k, v in d.items() if k in ("S1", "S2", "S3")) or 1
            cls_rows.append({"policy": pol, "t_rel_s": sec, "class": ep, "n": tot,
                             **{f"{s}_pct": round(100 * d.get(s, 0) / tot, 2)
                                for s in ("S1", "S2", "S3")}})
        print(f"  {pol}: 요청(search) {len(rows)} · 버킷 {len(buckets)} · "
              f"분배 초 {len(per)}")

    # --- slack + 관측 밴드 (SORTS 런에만 존재: 비교군은 컨트롤러가 없다)
    rd = os.path.join(RUNS, f"s5_sorts_L{L}_1")
    meta = json.load(open(os.path.join(rd, "meta.json")))
    t0, d12, d43 = meta["t_meas"], meta["clock"]["d12_s"], meta["clock"]["d43_s"]
    slack_rows = []
    for r in csv.DictReader(open(os.path.join(rd, "decisions.csv"))):
        if r["class"] != "search":          # 코호트는 둘 다 뽑는다(c2 는 예비 판용)
            continue
        slack_rows.append({"t_rel_s": round(float(r["ts"]) - d43 + d12 - t0, 2),
                           "cohort": r["cohort"], "class": r["class"],
                           "slack_S1": r["slack_s1"], "slack_S2": r["slack_s2"],
                           "slack_S3": r["slack_s3"],
                           "observed_rate_kbit": r["observed_rate_kbit"] or "",
                           "d_acc_ms": r["d_acc_ms"],
                           "subset_cluster": r["subset_cluster"]})

    def dump(name, rows):
        p = os.path.join(OUT, f"f1_L{L}_{name}.csv")
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"-> {p} ({len(rows)}행)")

    dump("latency", lat_rows)
    dump("points", pt_rows)
    dump("sites", site_rows)
    dump("sites_by_class", cls_rows)
    dump("slack", slack_rows)
    dump("marks", mark_rows)


if __name__ == "__main__":
    main()

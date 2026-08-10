#!/usr/bin/env python3
"""D단계 분석 공용 모듈 — 원본 런 디렉터리는 읽기 전용으로만 접근한다.

run_all.py 의 상수/조인 규칙을 그대로 따른다 (지시서 v7 §1).
"""
from __future__ import annotations

import csv
import gzip
import json
import os

RUNS = "/home/user/exp/runs/d1-20260804"
ANA = "/home/user/exp/analysis"
CACHE = os.path.join(ANA, "cache")

SITES = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
SLO_MS = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
EXPECT_BYTES = {"reserve": 36, "search": 4474, "recommend": 200}
# ★ v9 시대 상수. RUNS = d1-20260804 는 S2 netem 이 9.71 ms(목표 10)이던
# 시절의 런이라 이 데이터에는 10.0 이 옳다 (run_all.py.pre-v10.bak:46).
# v10(2026-08-05)부터 S2 = 15.0 — v10 런 분석에 이 모듈을 재사용하면
# f_c(S2) 가 5 ms 틀어진다. 재사용 금지 (ISSUES.md I-4).
D_NET_MS = {"S1": 2.0, "S2": 10.0, "S3": 25.006}
S1_KNEE_RPS = 400.0
GUARD_S = 2.0
RAMP_HI, RAMP_LO, RAMP_STEPS = 20000, 1600, 12

POLICIES = ["site_s3", "bl_rr", "bl_lr", "bl_loc"]
DISTURBS = ["none", "radio", "server"]
EPS = ["reserve", "search", "recommend"]

RUN_IDS = ([f"A_{d}_{p}" for d in DISTURBS for p in POLICIES]
           + [f"B_ramp_{p}" for p in POLICIES])


def run_dir(rid):
    return os.path.join(RUNS, rid)


def load_json(rid, name):
    with open(os.path.join(run_dir(rid), name)) as f:
        return json.load(f)


def is_valid(status, ep, nbytes):
    if status != 200:
        return False
    e = EXPECT_BYTES.get(ep)
    if e is None:
        return False
    return abs(nbytes - e) <= (e * 0.10 if e > 1000 else 0)


def build_sections(marks, t0_12, duration, d12, ds, de):
    """run_all.build_sections 와 동일 (벽시계 마크 ±GUARD)."""
    hi = t0_12 + duration
    st = next((m for m in marks if m.get("phase") == "start"), None)
    en = next((m for m in reversed(marks) if m.get("phase") == "end"), None)
    if not st or not en:
        return {"pre": (t0_12, t0_12 + ds), "during": (t0_12 + ds, t0_12 + de),
                "post": (t0_12 + de, hi)}
    return {"pre": (t0_12, st["t_issue"] + d12 - GUARD_S),
            "during": (st["t_done"] + d12 + GUARD_S,
                       en["t_issue"] + d12 - GUARD_S),
            "post": (en["t_done"] + d12 + GUARD_S, hi)}


def load_hostmap(path):
    hm = {}
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 11:
                continue
            try:
                us = int(p[17]) if len(p) > 17 and p[17].strip().isdigit() else None
                hm[p[1]] = (p[10].split(":")[0], float(p[0]), us)
            except ValueError:
                continue
    return hm


def cache_path(rid):
    return os.path.join(CACHE, f"{rid}.csv")


def build_cache(rid, force=False):
    """런 하나를 조인해 압축 CSV 로 캐시한다 (워밍업 제외 행만).

    컬럼: cohort,ep,t_rel,corrected_ms,service_ms,valid,site,fc_ms,joined
      t_rel = end_ts(.12 시계) − t_meas
    """
    out = cache_path(rid)
    if os.path.exists(out) and not force:
        return out
    meta = load_json(rid, "meta.json")
    t_meas = meta["t_meas"]
    hm = load_hostmap(os.path.join(run_dir(rid), "envoy_access.log.gz"))
    with open(out, "w", newline="") as fo:
        w = csv.writer(fo)
        w.writerow(["cohort", "ep", "t_rel", "corrected_ms", "service_ms",
                    "valid", "site", "fc_ms", "joined"])
        for c in (1, 2):
            f = os.path.join(run_dir(rid), f"load_c{c}.csv")
            if not os.path.exists(f):
                continue
            for r in csv.DictReader(open(f)):
                if r["warmup"] != "0":
                    continue
                ep = r["ep"]
                st = int(r["status"])
                nb = int(r["bytes_recv"])
                h = hm.get(r["request_id"])
                site = SITES.get(h[0]) if h and h[0] else ""
                fc = ""
                if site and h[2] is not None:
                    fc = round(h[2] / 1000.0 - D_NET_MS[site], 4)
                w.writerow([c, ep, round(float(r["end_ts"]) - t_meas, 4),
                            r["corrected_ms"], r["service_ms"],
                            1 if is_valid(st, ep, nb) else 0,
                            site, fc, 1 if h else 0])
    return out


def load_df(rid):
    import pandas as pd
    p = build_cache(rid)
    df = pd.read_csv(p, dtype={"site": str})
    df["site"] = df["site"].fillna("")
    return df


def sections_of(rid):
    """런의 pre/during/post 절대창과 상대창(초)을 돌려준다."""
    meta = load_json(rid, "meta.json")
    marks = load_json(rid, "marks.json")["marks"]
    d12 = meta.get("clock", {}).get("d12_s", 0.0)
    manifest = json.load(open("/home/user/exp/manifest.json"))
    nom = next(r for r in manifest["runs"] if r["run_id"] == rid)
    sec = build_sections(marks, meta["t_meas"], meta["duration"], d12,
                         float(nom["disturb_start"]), float(nom["disturb_end"]))
    t0 = meta["t_meas"]
    return {k: (a - t0, b - t0) for k, (a, b) in sec.items()}


def marks_rel(rid):
    """마크를 (what, phase, t_issue_rel, t_done_rel) 로 — .12 시계 상대초."""
    meta = load_json(rid, "meta.json")
    d12 = meta.get("clock", {}).get("d12_s", 0.0)
    t0 = meta["t_meas"]
    out = []
    for m in load_json(rid, "marks.json")["marks"]:
        out.append({"what": m["what"], "phase": m.get("phase"),
                    "spec": m.get("spec"),
                    "t_issue": m["t_issue"] + d12 - t0,
                    "t_done": m["t_done"] + d12 - t0})
    return out


def policy_of(rid):
    return load_json(rid, "meta.json")["policy"]


def disturb_of(rid):
    return load_json(rid, "meta.json")["disturb"]


POLICY_LABEL = {"site_s3": "site_s3", "bl_rr": "bl_rr",
                "bl_lr": "bl_lr", "bl_loc": "bl_loc (과부하)"}

# 그림 공통 — 검증된 카테고리 팔레트 (dataviz 기준 팔레트 slot 1/2/3)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
EP_COLOR = {"reserve": "#2a78d6", "search": "#eb6834", "recommend": "#1baf7a"}
SITE_COLOR = {"S1": "#2a78d6", "S2": "#eb6834", "S3": "#1baf7a"}
INK, INK2 = "#0b0b0b", "#52514e"
ALERT = "#e34948"


def setup_mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "Noto Sans CJK JP",   # Hangul 포함 pan-CJK
        "axes.unicode_minus": False,
        "figure.facecolor": "#fcfcfb",
        "axes.facecolor": "#fcfcfb",
        "text.color": INK, "axes.labelcolor": INK,
        "xtick.color": INK2, "ytick.color": INK2,
        "axes.edgecolor": "#c9c8c3", "grid.color": "#dedcd6",
        "axes.titlesize": 11, "font.size": 9.5,
    })
    return plt

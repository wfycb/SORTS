#!/usr/bin/env python3
"""obs.py 오프라인 리플레이 하네스 (작업 1 Phase 1, D4).

아카이브된 front Envoy access log 를 로그 시각 순서대로 obs.py 에 흘려서,
각 요청 시점의 **추정값**과 그 요청의 **실측값**을 짝짓는다. 사이트 .2/.3 이
내려가 있어 Phase 4 를 못 도는 동안 Phase 3 의 오차·마진 표를 실기기 없이
산출하는 것이 목적이다.

★ 개루프다. 추정이 라우팅을 바꾸고 그 라우팅이 다음 관측을 바꾸는 되먹임은
  재현되지 않는다. 입력 트래픽은 **상수판 컨트롤러가 이미 내린 결정**의
  결과물이고, 리플레이는 그 위에서 "그때 추정했다면 얼마였나"만 계산한다.
  따라서 아래는 유효하다:
    - 추정 오차 분포 / 커버리지 / 바이어스
    - 마진 표 (임계 밴드 rate 대비 여유)
    - 프라이어->관측 전환 시각과 계단 크기
  아래는 무효하다:
    - 전환 횟수, 감지 지연, 진동 여부 (되먹임이 있어야 나온다 -> Phase 4)

제어 주기 재현: 컨트롤러는 주기마다 한 번 통계를 갱신하고 그 사이엔 값이
얼어 있다. 리플레이도 tick 경계에서만 recompute 를 부르고, tick 사이의
요청은 얼어 있는 추정값과 짝지어진다. 실제 컨트롤러가 보는 것과 같다.

사용:
  python3 obs_replay.py --runs runs/demo-20260805        # v10 era (권고)
  python3 obs_replay.py --runs runs/demo-20260805 --focus D6_sorts_ramp
  python3 obs_replay.py --all                            # 아카이브 전체
"""
from __future__ import annotations

import argparse
import bisect
import csv
import glob
import gzip
import json
import math
import os
import statistics
import sys
from array import array

import yaml

import obs

# 실제 사용된 무선 밴드 [kbit]. run_all.py BANDS(poor=2300),
# RAMP_HI/LO(20000/1600), sorts_ctl.selftest 계단(20000/4500/2300/1600),
# 그리고 ramp 12계단 중 여유가 가장 좁은 3273 (D6 marks.json 실측).
REPORT_BANDS = (20000, 4500, 3273, 2300, 1600)
# 여유가 유일하게 좁은 지점 (지시 §3.4)
FOCUS_BAND_KBIT = 3273
FOCUS_SITE = "S3"
FOCUS_CLASS = "search"

TICK_HEADER = ["file", "tick", "t_rel", "site", "class", "src", "n",
               "stale_ms", "fc_est", "fc_prior", "fc_mean", "fc_p95",
               "realized_p95", "cov_fwd", "n_fwd",
               "bytes_src", "bytes_n", "bytes_est", "bytes_prior",
               "bytes_mean", "update_ms"]


def open_maybe_gz(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", errors="replace")
    return open(path, "rt", errors="replace")


def pctl(sorted_xs, q):
    if not sorted_xs:
        return None
    return sorted_xs[int(round(q * (len(sorted_xs) - 1)))]


class CellSamples:
    """(ts, value) 를 압축 배열로. 전방 윈도 실현 분위수 계산용."""

    __slots__ = ("ts", "val")

    def __init__(self):
        self.ts = array("d")
        self.val = array("d")

    def add(self, ts, v):
        self.ts.append(ts)
        self.val.append(v)

    def window(self, t0, t1):
        """(t0, t1] 구간 값 목록. ts 는 단조증가 가정(로그 순서)."""
        lo = bisect.bisect_right(self.ts, t0)
        hi = bisect.bisect_right(self.ts, t1)
        return self.val[lo:hi]


class Accum:
    """오차 누적기. 요청 단위 페어를 메모리에 다 들고 있지 않는다."""

    __slots__ = ("n", "abs_sum", "pct_sum", "signed_sum", "le", "diffs")

    def __init__(self):
        self.n = 0
        self.abs_sum = 0.0
        self.pct_sum = 0.0
        self.signed_sum = 0.0
        self.le = 0
        self.diffs = []

    def add(self, est, act, keep_diff=False):
        self.n += 1
        d = est - act
        self.abs_sum += abs(d)
        self.signed_sum += d
        if act != 0:
            self.pct_sum += abs(d) / abs(act) * 100.0
        if act <= est:
            self.le += 1
        if keep_diff:
            self.diffs.append(d)

    def mae(self):
        return self.abs_sum / self.n if self.n else float("nan")

    def mape(self):
        return self.pct_sum / self.n if self.n else float("nan")

    def bias(self):
        return self.signed_sum / self.n if self.n else float("nan")

    def coverage(self):
        return self.le / self.n if self.n else float("nan")


def replay_file(path, cfg, tick_s, window_s, n_min_fc, n_min_bytes,
                stale_ttl_s, tick_writer, state, host_fallback=False):
    """한 슬라이스 리플레이. 슬라이스마다 Observer 를 새로 만든다.

    아카이브 슬라이스는 서로 다른 런이라 시각이 크게 벌어져 있다. 이어붙이면
    가짜 스테일이 생기므로 파일 경계에서 상태를 버린다.
    """
    ob = obs.Observer(cfg, log_path=None, window_s=window_s,
                      n_min_fc=n_min_fc, n_min_bytes=n_min_bytes,
                      stale_ttl_s=stale_ttl_s, start_at_end=False,
                      allow_host_fallback=host_fallback)
    name = os.path.basename(os.path.dirname(path)) or os.path.basename(path)

    samples = {}          # (site,cls) -> CellSamples
    ticks = []            # (tick_idx, t, {(s,c): entry}, {c: entry})
    t0 = None
    next_tick = None
    tick_idx = 0

    fc_acc = state["fc_acc"]
    by_acc = state["by_acc"]
    transitions = state["transitions"]
    prev_src = {}

    with open_maybe_gz(path) as f:
        for line in f:
            line = line.rstrip("\n")
            p = line.split(",")
            if len(p) < obs.N_FIELDS:
                continue
            try:
                ts = float(p[obs.F_START_TIME])
            except ValueError:
                continue
            if t0 is None:
                t0 = ts
                next_tick = ts          # 첫 tick 은 즉시 (전부 프라이어)
            # ---- tick 경계: 컨트롤러가 통계를 갱신하는 순간
            while ts >= next_tick:
                ob.update(now=next_tick)
                snap_fc = dict(ob._cache_fc)
                snap_by = dict(ob._cache_bytes)
                # ★ 소요시간은 이 tick 시점 값을 그때 붙잡아야 한다. 아래 쓰기
                #   루프에서 ob.last_update_ms 를 읽으면 파일의 마지막 tick 값이
                #   전 행에 복사된다(=분포가 상수로 보인다).
                ticks.append((tick_idx, next_tick, snap_fc, snap_by,
                              ob.last_update_ms))
                # 프라이어 -> 관측 전환 기록
                for key, e in snap_fc.items():
                    was = prev_src.get(key)
                    if was is not None and was != e["src"]:
                        # 계단 = 결정값의 변화. prior<->prior_fill 은 둘 다
                        # 프라이어를 쓰므로 0 이다 (src 라벨만 바뀜).
                        if e["src"] == obs.SRC_OBS:
                            step = e["value"] - e["prior"]
                        elif was == obs.SRC_OBS:
                            step = e["prior"] - (e["p95"]
                                                 if e["p95"] is not None
                                                 else e["prior"])
                        else:
                            step = 0.0
                        transitions.append({
                            "file": name, "cell": "{}|{}".format(*key),
                            "t_rel": round(next_tick - t0, 3),
                            "from": was, "to": e["src"], "n": e["n"],
                            "step_ms": round(step, 3),
                            "prior": e["prior"],
                            "obs_p95": (None if e["p95"] is None
                                        else round(e["p95"], 3))})
                    prev_src[key] = e["src"]
                tick_idx += 1
                next_tick += tick_s

            # ---- 요청 단위 페어: 현재(얼어 있는) 추정 vs 이 요청의 실측
            if (p[obs.F_RESPONSE_CODE] == obs.OK_RESPONSE_CODE
                    and p[obs.F_RESPONSE_FLAGS] == obs.OK_RESPONSE_FLAGS):
                klass = obs.class_of(p[obs.F_PATH])
                site = obs.site_of(p[obs.F_UPSTREAM_CLUSTER],
                                   p[obs.F_UPSTREAM_HOST], host_fallback)
                if klass and site:
                    try:
                        nbytes = int(p[obs.F_BYTES_SENT])
                        rt_us = int(p[obs.F_US_RT_US])
                    except ValueError:
                        nbytes = 0
                        rt_us = -1
                    if nbytes > 0 and rt_us >= 0:
                        fc_act = rt_us / 1000.0 - ob.d_net[site]
                        ent = ob._cache_fc.get((site, klass))
                        if ent is not None:
                            fc_acc[(site, klass, ent["src"])].add(
                                ent["value"], fc_act)
                        eb = ob._cache_bytes.get(klass)
                        if eb is not None:
                            by_acc[(klass, eb["src"])].add(
                                eb["value"], float(nbytes))
                        samples.setdefault(
                            (site, klass), CellSamples()).add(ts, fc_act)

            ob.ingest_line(line, ts)

    # ---- 전방 윈도 실현 p95 / 커버리지 (추정 시점 이후 window_s 구간)
    for tick_idx_, t, snap_fc, snap_by, upd_ms in ticks:
        for (s, c), e in snap_fc.items():
            cs = samples.get((s, c))
            fwd = cs.window(t, t + window_s) if cs else []
            realized = pctl(sorted(fwd), obs.DEFAULT_FC_QUANTILE) if fwd else None
            cov = (sum(1 for v in fwd if v <= e["value"]) / len(fwd)
                   if fwd else None)
            eb = snap_by[c]
            tick_writer.writerow([
                name, tick_idx_, round(t - t0, 3), s, c, e["src"], e["n"],
                "" if e["stale_ms"] is None else round(e["stale_ms"], 1),
                round(e["value"], 4), round(e["prior"], 4),
                "" if e["mean"] is None else round(e["mean"], 4),
                "" if e["p95"] is None else round(e["p95"], 4),
                "" if realized is None else round(realized, 4),
                "" if cov is None else round(cov, 4), len(fwd),
                eb["src"], eb["n"], round(eb["value"], 2),
                round(eb["prior"], 2),
                "" if eb["mean"] is None else round(eb["mean"], 2),
                round(upd_ms, 4)])
            if e["src"] == obs.SRC_OBS and realized is not None:
                state["q_acc"][(s, c)].add(e["value"], realized, keep_diff=True)

    state["files"].append({"file": name, "path": path,
                           "snapshot": ob.snapshot(),
                           "t0": t0, "ticks": len(ticks)})
    state["ticks_raw"].setdefault(name, ticks)
    state["samples"].setdefault(name, samples)
    return ob


def fmt(v, w=8, p=3):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return " " * (w - 1) + "-"
    return "{:{w}.{p}f}".format(v, w=w, p=p)


def threshold_kbit(nbytes, slo, gb, d_net, fc, overhead):
    """사이트를 버리는 임계 밴드 rate [kbit]. slack=0 인 지점."""
    denom = slo - gb - d_net - fc
    if denom <= 0:
        return float("inf")
    return nbytes * 8.0 * overhead / denom


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sorts.yaml")
    ap.add_argument("--runs", default="runs/demo-20260805",
                    help="슬라이스를 담은 배치 디렉터리")
    ap.add_argument("--all", action="store_true", help="아카이브 전체")
    ap.add_argument("--out", default="analysis/obs_replay")
    ap.add_argument("--tick", type=float, default=None, help="제어 주기 [s]")
    ap.add_argument("--window", type=float, default=obs.DEFAULT_WINDOW_S)
    ap.add_argument("--n-min-fc", type=int, default=obs.DEFAULT_N_MIN_FC)
    ap.add_argument("--n-min-bytes", type=int, default=obs.DEFAULT_N_MIN_BYTES)
    ap.add_argument("--stale-ttl", type=float, default=obs.DEFAULT_STALE_TTL_S)
    ap.add_argument("--focus", default="D6_sorts_ramp")
    ap.add_argument("--host-fallback", action="store_true",
                    help="비교군(bl_*) 슬라이스도 필드11 IP 로 사이트를 복원해 "
                         "관측에 넣는다. 분석 자료용이며 라이브 경로와 다르다.")
    a = ap.parse_args()

    cfg = yaml.safe_load(open(a.config))
    tick_s = a.tick if a.tick is not None else float(cfg["t_ctrl_s"])

    if a.all:
        files = sorted(glob.glob("runs/*/*/envoy_access.log.gz"))
    else:
        files = sorted(glob.glob(os.path.join(a.runs, "*", "envoy_access.log.gz")))
    if not files:
        sys.exit("슬라이스를 못 찾았다: {}".format(a.runs))

    os.makedirs(a.out, exist_ok=True)
    tick_path = os.path.join(a.out, "ticks.csv")

    state = {
        "fc_acc": {}, "by_acc": {}, "q_acc": {},
        "transitions": [], "files": [], "ticks_raw": {}, "samples": {},
    }
    # prior_fill(충전 게이트, Phase 2) 도 별도 src 로 집계한다.
    ALL_SRC = (obs.SRC_PRIOR, obs.SRC_PRIOR_FILL, obs.SRC_OBS)
    for s in obs.SITES:
        for c in obs.CLASSES:
            state["q_acc"][(s, c)] = Accum()
            for src in ALL_SRC:
                state["fc_acc"][(s, c, src)] = Accum()
    for c in obs.CLASSES:
        for src in ALL_SRC:
            state["by_acc"][(c, src)] = Accum()

    print("리플레이 {}개 슬라이스, tick={}s window={}s n_min_fc={} "
          "n_min_bytes={} stale_ttl={}s host_fallback={}"
          .format(len(files), tick_s, a.window, a.n_min_fc, a.n_min_bytes,
                  a.stale_ttl, a.host_fallback))
    if a.host_fallback:
        print("  ★ host-fallback ON: 비교군 슬라이스도 관측에 포함한다. "
              "라이브 obs.py 는 이 경로를 쓰지 않는다 (필드10 엄격).")
    with open(tick_path, "w", newline="") as tf:
        tw = csv.writer(tf)
        tw.writerow(TICK_HEADER)
        for path in files:
            ob = replay_file(path, cfg, tick_s, a.window, a.n_min_fc,
                             a.n_min_bytes, a.stale_ttl, tw, state,
                             host_fallback=a.host_fallback)
            sn = ob.snapshot()
            print("  {:28s} lines={:7d} used={:7d} 배제(cluster={} class={} "
                  "code={} flags={}) novel={}"
                  .format(os.path.basename(os.path.dirname(path)),
                          sn["n_lines"], sn["n_used"], sn["n_other_cluster"],
                          sn["n_other_class"], sn["n_bad_code"],
                          sn["n_bad_flags"], sn["resp_bytes_novel_n"]))

    report(cfg, state, a, tick_s, tick_path)


def report(cfg, state, a, tick_s, tick_path):
    slo, gb, ov = cfg["slo_ms"], cfg["gb_ms"], cfg["overhead"]
    d_net, prior_fc, prior_by = cfg["d_net_ms"], cfg["f_c_ms"], cfg["resp_bytes"]

    print("\n" + "=" * 78)
    print("표 1. resp_bytes 추정 오차 (요청 단위, src=obs 구간)")
    print("=" * 78)
    print("{:10s} {:>10s} {:>9s} {:>9s} {:>9s} {:>10s}"
          .format("class", "n", "MAE[B]", "MAPE[%]", "bias[B]", "프라이어"))
    for c in obs.CLASSES:
        acc = state["by_acc"][(c, obs.SRC_OBS)]
        if acc.n == 0:
            print("{:10s} {:>10s}  (관측 구간 없음)".format(c, "0"))
            continue
        print("{:10s} {:10d} {:9.4f} {:9.4f} {:+9.4f} {:10.0f}"
              .format(c, acc.n, acc.mae(), acc.mape(), acc.bias(),
                      float(prior_by[c])))

    print("\n" + "=" * 78)
    print("표 2. f_c 커버리지 (실측 <= 추정 비율, 목표 0.95)")
    print("      ★ 단일 요청 실측과의 차이는 '오차'가 아니다 — 분위수 추정에")
    print("        맞는 지표는 커버리지다.")
    print("=" * 78)
    print("{:4s} {:10s} {:>9s} {:>9s} | {:>9s} {:>9s}"
          .format("site", "class", "n(obs)", "커버리지", "n(prior)", "커버리지"))
    for s in obs.SITES:
        for c in obs.CLASSES:
            ao = state["fc_acc"][(s, c, obs.SRC_OBS)]
            ap_ = state["fc_acc"][(s, c, obs.SRC_PRIOR)]
            print("{:4s} {:10s} {:9d} {:>9s} | {:9d} {:>9s}"
                  .format(s, c, ao.n,
                          "-" if ao.n == 0 else "{:.4f}".format(ao.coverage()),
                          ap_.n,
                          "-" if ap_.n == 0 else "{:.4f}".format(ap_.coverage())))

    print("\n" + "=" * 78)
    print("표 3. 추정 p95 vs 전방 윈도 실현 p95 (tick 단위, src=obs)")
    print("=" * 78)
    print("{:4s} {:10s} {:>7s} {:>9s} {:>9s} {:>9s} {:>9s} {:>9s}"
          .format("site", "class", "ticks", "MAE[ms]", "MAPE[%]", "bias[ms]",
                  "p50차", "p95차"))
    for s in obs.SITES:
        for c in obs.CLASSES:
            acc = state["q_acc"][(s, c)]
            if acc.n == 0:
                print("{:4s} {:10s} {:>7d}   (관측 구간 없음)".format(s, c, 0))
                continue
            d = sorted(acc.diffs)
            print("{:4s} {:10s} {:7d} {:9.4f} {:9.4f} {:+9.4f} {:+9.4f} {:+9.4f}"
                  .format(s, c, acc.n, acc.mae(), acc.mape(), acc.bias(),
                          pctl(d, 0.5), pctl(d, 0.95)))

    # ---- 관측 f_c 대표값 (tick 중앙값) -> 마진 표
    obs_fc = {}
    import collections
    per_cell = collections.defaultdict(list)
    with open(tick_path) as f:
        for r in csv.DictReader(f):
            if r["src"] == obs.SRC_OBS and r["fc_p95"]:
                per_cell[(r["site"], r["class"])].append(float(r["fc_p95"]))
    for k, v in per_cell.items():
        obs_fc[k] = statistics.median(v)

    print("\n" + "=" * 78)
    print("표 4. 마진 표 — 임계 밴드 rate [kbit] 와 실제 밴드까지의 거리")
    print("      임계 = bytes*8*overhead / (SLO - GB - d_net - f_c)")
    print("      밴드 > 임계 => 그 사이트 사용 가능. 거리 = (밴드-임계)/임계")
    print("=" * 78)
    hdr = "{:4s} {:10s} {:>9s} {:>9s} {:>7s} ".format(
        "site", "class", "임계(상수)", "임계(관측)", "이동%")
    hdr += " ".join("{:>10s}".format(str(b)) for b in REPORT_BANDS)
    print(hdr)
    for s in obs.SITES:
        for c in obs.CLASSES:
            fc_p = float(prior_fc[c][s])
            fc_o = obs_fc.get((s, c))
            nb = float(prior_by[c])
            t_p = threshold_kbit(nb, slo[c], gb, d_net[s], fc_p, ov)
            t_o = (threshold_kbit(nb, slo[c], gb, d_net[s], fc_o, ov)
                   if fc_o is not None else None)
            move = ("-" if t_o is None or math.isinf(t_p)
                    else "{:+.2f}".format((t_o / t_p - 1) * 100))
            row = "{:4s} {:10s} {:9.0f} {:>9s} {:>7s} ".format(
                s, c, t_p, "-" if t_o is None else "{:.0f}".format(t_o), move)
            cells = []
            for b in REPORT_BANDS:
                base = t_o if t_o is not None else t_p
                if math.isinf(base):
                    cells.append("{:>10s}".format("불가"))
                else:
                    cells.append("{:>+9.1f}%".format((b - base) / base * 100))
            print(row + " ".join(cells))

    print("\n  (표 4 셀 = 실제 밴드가 임계보다 몇 % 위/아래인가. 양수면 그 밴드에서"
          "\n   해당 사이트 사용 가능, 음수면 버림. 절댓값이 작을수록 경계에 가깝다.)")

    # ---- 집중 계측: FOCUS_SITE/CLASS @ FOCUS_BAND
    # ---- 밴드-갈림존 자동 검사 (I-7): 밴드·SLO·f_c 를 바꾸면 여기서 잡힌다.
    print("\n" + "=" * 78)
    import obs_band_margin
    rows_bm, viol_bm = obs_band_margin.check(cfg, list(REPORT_BANDS))
    for (site_bm, lo_bm, hi_bm, dist_bm, inside_bm) in rows_bm:
        print("갈림존 {} ({:.0f}, {:.0f}] 최소거리 {} 존내 {}".format(
            site_bm, lo_bm, hi_bm,
            "-" if dist_bm is None else "{:.0f}".format(dist_bm),
            inside_bm if inside_bm else "없음"))
    if viol_bm:
        print("★★ 갈림존 안에 보고 밴드가 있다 — obs_band_margin.py 를 봐라 (I-7)")

    focus_report(cfg, state, a, tick_path)

    # ---- 전환
    print("\n" + "=" * 78)
    print("표 6. 프라이어 -> 관측 전환 (시각, 셀, 계단 크기)")
    print("=" * 78)
    tr = state["transitions"]
    print("총 {}건 (파일당 셀당 첫 전환만 아래 표시)".format(len(tr)))
    shown = set()
    print("{:22s} {:12s} {:>8s} {:>6s} {:>8s} {:>9s} {:>9s} {:>9s}"
          .format("file", "cell", "t_rel", "n", "from->to", "prior", "obs_p95",
                  "계단[ms]"))
    for t in tr:
        key = (t["file"], t["cell"], t["from"], t["to"])
        if key in shown:
            continue
        shown.add(key)
        if len(shown) > 40:
            break
        print("{:22s} {:12s} {:8.3f} {:6d} {:>8s} {:9.3f} {:>9s} {:+9.3f}"
              .format(t["file"][:22], t["cell"], t["t_rel"], t["n"],
                      "{}->{}".format(t["from"][:3], t["to"][:3]),
                      t["prior"],
                      "-" if t["obs_p95"] is None else "{:.3f}".format(t["obs_p95"]),
                      t["step_ms"]))

    # ---- 갱신 소요
    print("\n" + "=" * 78)
    print("표 7. tick 당 recompute 소요 [ms]")
    print("      ★ 리플레이의 update() 는 **recompute 만** 잰다. log_path=None 이라")
    print("        tail 이 없고, ingest 는 줄 단위로 update() 바깥에서 돈다.")
    print("        라이브 주기 비용(tail+parse+ingest+recompute)은 obs_tail_bench.py.")
    print("=" * 78)
    allms = []
    for fi in state["files"]:
        allms.extend([])
    import collections as _c
    per_file = _c.defaultdict(list)
    with open(tick_path) as f:
        for r in csv.DictReader(f):
            if r["update_ms"]:
                per_file[r["file"]].append(float(r["update_ms"]))
    print("{:24s} {:>8s} {:>9s} {:>9s} {:>9s} {:>9s}"
          .format("file", "n", "p50", "p95", "p99", "max"))
    for k in sorted(per_file):
        v = sorted(per_file[k])
        print("{:24s} {:8d} {:9.3f} {:9.3f} {:9.3f} {:9.3f}"
              .format(k[:24], len(v), pctl(v, .5), pctl(v, .95),
                      pctl(v, .99), v[-1]))

    outj = os.path.join(a.out, "summary.json")
    with open(outj, "w") as f:
        json.dump({
            "params": {"tick_s": tick_s, "window_s": a.window,
                       "n_min_fc": a.n_min_fc, "n_min_bytes": a.n_min_bytes,
                       "stale_ttl_s": a.stale_ttl},
            "open_loop_note": "추정이 라우팅을 바꾸는 되먹임은 재현 안 됨",
            "files": [{"file": x["file"], "ticks": x["ticks"],
                       "snapshot": x["snapshot"]} for x in state["files"]],
            "obs_fc_median_p95": {"{}|{}".format(*k): v
                                  for k, v in obs_fc.items()},
            "transitions": state["transitions"][:500],
        }, f, ensure_ascii=False, indent=1)
    print("\n-> {}\n-> {}".format(tick_path, outj))


def focus_report(cfg, state, a, tick_path):
    """S3 search @ 3273 kbit 집중 계측 (지시 §3.4)."""
    slo, gb, ov = cfg["slo_ms"], cfg["gb_ms"], cfg["overhead"]
    d_net, prior_by = cfg["d_net_ms"], cfg["resp_bytes"]
    nb = float(prior_by[FOCUS_CLASS])
    d_acc = nb * 8.0 * ov / FOCUS_BAND_KBIT
    fc_crit = (slo[FOCUS_CLASS] - gb - d_net[FOCUS_SITE] - d_acc)

    print("\n" + "=" * 78)
    print("표 5. 집중 계측 — {} {} @ {} kbit (여유가 유일하게 좁은 지점)"
          .format(FOCUS_SITE, FOCUS_CLASS, FOCUS_BAND_KBIT))
    print("=" * 78)
    print("  d_acc({} kbit, {}B) = {:.3f} ms".format(FOCUS_BAND_KBIT, int(nb), d_acc))
    print("  임계 f_c = SLO {} - GB {} - d_net {} - d_acc {:.3f} = {:.3f} ms"
          .format(slo[FOCUS_CLASS], gb, d_net[FOCUS_SITE], d_acc, fc_crit))
    print("  => 추정 f_c < {:.3f} 이면 {} 를 '사용 가능'으로 판정한다."
          .format(fc_crit, FOCUS_SITE))
    print("     프라이어 f_c = {:.3f} -> 상수판은 이 밴드에서 {} 를 버린다."
          .format(float(cfg["f_c_ms"][FOCUS_CLASS][FOCUS_SITE]),
                  FOCUS_SITE)
          if float(cfg["f_c_ms"][FOCUS_CLASS][FOCUS_SITE]) >= fc_crit else "")
    rows = []
    with open(tick_path) as f:
        for r in csv.DictReader(f):
            if r["site"] == FOCUS_SITE and r["class"] == FOCUS_CLASS:
                rows.append(r)
    if not rows:
        print("  해당 셀 tick 없음")
        return
    by_file = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)
    print("\n{:24s} {:>7s} {:>7s} {:>7s} {:>9s} {:>9s} {:>9s} {:>9s} {:>10s}"
          .format("file", "ticks", "obs", "prior", "est_p50", "est_p95",
                  "est_max", "est_min", "임계미만%"))
    for k in sorted(by_file):
        v = by_file[k]
        est = [float(r["fc_est"]) for r in v]
        nobs = sum(1 for r in v if r["src"] == obs.SRC_OBS)
        under = sum(1 for e in est if e < fc_crit)
        se = sorted(est)
        print("{:24s} {:7d} {:7d} {:7d} {:9.3f} {:9.3f} {:9.3f} {:9.3f} {:9.1f}%"
              .format(k[:24], len(v), nobs, len(v) - nobs, pctl(se, .5),
                      pctl(se, .95), se[-1], se[0], under / len(v) * 100))
    print("\n  '임계미만%' = 그 tick 의 추정 f_c 로 판정했다면 {} 를 계속 썼을 비율."
          "\n  상수판은 프라이어가 고정이라 0% 또는 100% 로만 나온다. 관측판이"
          "\n  그 사이를 오가면 그것이 경계 지터(=진동 후보)다."
          .format(FOCUS_SITE))


if __name__ == "__main__":
    main()

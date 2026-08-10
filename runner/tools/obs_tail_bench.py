#!/usr/bin/env python3
"""obs.py 라이브 주기 비용 벤치 (작업 1 Phase 1, 지시 §2.1 끝줄 / §3.6).

리플레이(obs_replay.py)의 update() 는 recompute 만 잰다 — log_path=None 이라
tail 이 없고 ingest 는 줄 단위로 update() 바깥에서 돌기 때문이다. 작업 2 에서
제어주기를 내릴 수 있는지 판단하려면 **라이브와 같은 경로**의 비용이 필요하다:

    stat -> seek -> read -> decode -> split -> ingest -> prune -> 분위수

그래서 아카이브 슬라이스를 실제 파일에 **덧붙이면서** Observer 가 그 파일을
tail 하게 한다. Envoy 가 쓰고 컨트롤러가 읽는 구조를 그대로 흉내낸다.

주기를 여러 개로 쓸어서(1.0 / 0.1 / 0.05 / 0.025 s) "주기를 내리면 주기당
비용이 어떻게 변하나"를 본다. 총 바이트율은 같으므로 주기당 읽는 양은
비례해 줄지만, 고정비(stat/seek/분위수 9칸)는 줄지 않는다. 그 교차점이
작업 2 의 하한 근거다.

★ 부하율은 슬라이스 그대로다(demo 슬라이스 = 336000줄/420s ≈ 800 lines/s).
  라이브 front 로그는 코호트 6유닛 합류라 이보다 높을 수 있다 — --speedup 으로
  줄 도착률을 배수한다 (같은 줄을 더 빨리 먹인다).

사용:
  python3 obs_tail_bench.py --slice runs/demo-20260805/D6_sorts_ramp
  python3 obs_tail_bench.py --slice ... --ticks 1.0,0.1,0.05,0.025 --speedup 2
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import statistics
import tempfile
import time

import yaml

import obs


def load_lines(path, limit):
    """슬라이스를 (ts_abs, 원본줄) 목록 + 첫 ts 로.

    ★ ts 는 **절대 epoch** 로 둔다. 줄 안의 START_TIME 이 절대값이라
      ingest 가 절대 ts 로 윈도에 넣는데, tick 을 상대초로 굴리면
      prune 의 cutoff 가 음수가 되어 **아무것도 안 버려진다**. 그러면
      윈도가 슬라이스 전체(33.6만)로 자라고 분위수 비용이 폭발해서
      (실측 ~72 ms/주기) 라이브 비용을 완전히 잘못 재게 된다.
      라이브는 obs.update(now=time.time()) 라 둘 다 epoch 로 일치한다.
    """
    out = []
    t0 = None
    with gzip.open(path, "rt", errors="replace") as f:
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
            out.append((ts, line))
            if limit and len(out) >= limit:
                break
    return out, t0


def bench(lines, t0, cfg, tick_s, speedup, workdir):
    """한 주기 설정 벤치. 반환 = 측정치 dict.

    실시간 대기를 하지 않는다 — 슬라이스 420초를 실제로 기다릴 수 없다.
    대신 각 tick 에 속하는 줄을 파일에 append 하고 즉시 update() 를 부른다.
    측정하는 것은 **주기당 작업량의 비용**이지 벽시계 스케줄이 아니다.
    """
    log_path = os.path.join(workdir, "front_access.log")
    open(log_path, "w").close()
    ob = obs.Observer(cfg, log_path=log_path, start_at_end=False)

    fh = open(log_path, "a")
    n_ticks = 0
    idx = 0
    n_lines = len(lines)
    span = (lines[-1][0] - t0) / speedup if lines else 0.0
    # 절대 시간축. speedup 은 도착을 압축하되 축은 epoch 로 유지한다.
    t_next = t0 + tick_s
    durs = []
    bytes_per_tick = []

    while idx < n_lines:
        chunk = []
        while idx < n_lines and t0 + (lines[idx][0] - t0) / speedup < t_next:
            ts, raw = lines[idx]
            if speedup != 1.0:
                # 줄 안의 START_TIME 도 같이 압축해야 한다. 안 그러면 tick 축은
                # 압축됐는데 윈도 내용은 원래 시각이라 prune 이 어긋난다.
                # (쓰기는 측정 구간 밖이다 — 타이밍은 update() 만 감싼다.)
                raw = "{:.6f}{}".format(t0 + (ts - t0) / speedup,
                                        raw[raw.index(","):])
            chunk.append(raw)
            idx += 1
        if chunk:
            blob = "\n".join(chunk) + "\n"
            fh.write(blob)
            fh.flush()
            bytes_per_tick.append(len(blob))
        else:
            bytes_per_tick.append(0)
        # 라이브와 같은 호출: tail + parse + ingest + prune + 분위수
        durs.append(ob.update(now=t_next))
        n_ticks += 1
        t_next += tick_s
    fh.close()

    sd = sorted(durs)
    sn = ob.snapshot()
    return {
        "tick_s": tick_s, "speedup": speedup, "n_ticks": n_ticks,
        "span_s": round(span, 1),
        "lines_used": sn["n_used"], "lines_seen": sn["n_lines"],
        "p50": obs.pctl(sd, .5), "p95": obs.pctl(sd, .95),
        "p99": obs.pctl(sd, .99), "max": sd[-1] if sd else None,
        "mean": statistics.fmean(durs) if durs else None,
        "duty_pct": (statistics.fmean(durs) / (tick_s * 1000.0) * 100.0
                     if durs else None),
        "bytes_per_tick_p50": obs.pctl(sorted(bytes_per_tick), .5),
        "backlog_bytes_end": sn["backlog_bytes"],
        "n_rotations": sn["n_rotations"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sorts.yaml")
    ap.add_argument("--slice", default="runs/demo-20260805/D6_sorts_ramp")
    ap.add_argument("--ticks", default="1.0,0.1,0.05,0.025")
    ap.add_argument("--speedup", type=float, default=1.0,
                    help="줄 도착률 배수 (라이브가 슬라이스보다 빠를 때)")
    ap.add_argument("--limit", type=int, default=0, help="줄 수 상한 (0=전체)")
    ap.add_argument("--out", default="analysis/obs_replay/tail_bench.json")
    a = ap.parse_args()

    cfg = yaml.safe_load(open(a.config))
    path = os.path.join(a.slice, "envoy_access.log.gz")
    print("로드 {} ...".format(path))
    lines, t0 = load_lines(path, a.limit)
    if not lines:
        raise SystemExit("줄이 없다: {}".format(path))
    span0 = lines[-1][0] - t0
    rate = len(lines) / span0 * a.speedup
    print("{}줄, 슬라이스 {:.1f}s, 도착률 {:.0f} lines/s (speedup {}x)"
          .format(len(lines), span0, rate, a.speedup))

    rows = []
    workdir = tempfile.mkdtemp(prefix="obsbench-")
    try:
        for t in [float(x) for x in a.ticks.split(",")]:
            r = bench(lines, t0, cfg, t, a.speedup, workdir)
            r["lines_per_s"] = round(rate, 1)
            rows.append(r)
            print("  tick={:6.3f}s ticks={:6d} p50={:7.3f} p95={:7.3f} "
                  "p99={:7.3f} max={:7.3f} ms  duty={:5.2f}%  read/tick={}B"
                  .format(t, r["n_ticks"], r["p50"], r["p95"], r["p99"],
                          r["max"], r["duty_pct"], r["bytes_per_tick_p50"]))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("\n{:>8s} {:>8s} {:>8s} {:>8s} {:>8s} {:>8s} {:>9s}"
          .format("tick[s]", "p50[ms]", "p95[ms]", "p99[ms]", "max[ms]",
                  "duty%", "여유배수"))
    for r in rows:
        print("{:8.3f} {:8.3f} {:8.3f} {:8.3f} {:8.3f} {:8.2f} {:9.0f}x"
              .format(r["tick_s"], r["p50"], r["p95"], r["p99"], r["max"],
                      r["duty_pct"], r["tick_s"] * 1000.0 / r["p95"]))
    print("\n  duty% = 평균소요/주기. 여유배수 = 주기/p95 (몇 배 여유인가).")
    print("  이 값은 .40(리플레이 호스트) 기준이다. 컨트롤러는 .43 에서 도므로")
    print("  작업 2 결정 전 .43 에서 같은 벤치를 한 번 더 돌려야 한다.")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump({"slice": a.slice, "host": os.uname().nodename,
                   "rows": rows}, f, ensure_ascii=False, indent=1)
    print("-> {}".format(a.out))


if __name__ == "__main__":
    main()

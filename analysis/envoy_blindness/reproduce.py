#!/usr/bin/env python3
"""과제 2: "Envoy 1000:1" 재산출 — 무선 열화 vs Envoy 관측 지연 변화.

주장(재현 대상): "무선이 22.541 ms 나빠지는 동안 front Envoy 가 관측한
지연 변화는 0.022 ms" (원 출처 문서 미확인 — 백업 보고 §6).

정의 (이 스크립트가 산출·명시하는 것):
  접속측 열화  = Δmean corrected_ms (밴드 코호트 c1, pre→during)
                 [보조: Δmean service_ms, 계산 d_acc(2300k)]
  Envoy측 관측 = Δmean 필드18/1000 (US_TX_BEG:US_RX_END 업스트림 왕복 ms,
                 c1 요청 조인, pre→during)
데이터: runs/phase4-20260807/{R1_rr_radio(라우팅 고정 RR — 주 산출),
        A2_both_radio(SORTS 반응 — 보조)}. 창 = meta.json sections_abs_12.
재현: python3 analysis/envoy_blindness/reproduce.py
"""
import csv
import gzip
import json
import os
import sys

RUNS = ["/home/user/exp/runs/phase4-20260807/R1_rr_radio",
        "/home/user/exp/runs/phase4-20260807/A2_both_radio"]


def one(rd):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    sec = meta["sections_abs_12"]
    hm = {}
    with gzip.open(os.path.join(rd, "envoy_access.log.gz"), "rt",
                   errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) > 17 and p[17].strip().isdigit():
                hm[p[1]] = int(p[17])
    out = {}
    for coh in (1, 2):
        rows = [r for r in csv.DictReader(
            open(os.path.join(rd, f"load_c{coh}.csv"))) if r["warmup"] == "0"]
        for win in ("pre", "during"):
            a, b = sec[win]
            sub = [r for r in rows if a <= float(r["end_ts"]) < b]
            for ep in ("search", "reserve", "recommend", "ALL"):
                ss = sub if ep == "ALL" else [r for r in sub if r["ep"] == ep]
                if not ss:
                    continue
                cor = [float(r["corrected_ms"]) for r in ss]
                svc = [float(r["service_ms"]) for r in ss]
                us = [hm[r["request_id"]] / 1000.0 for r in ss
                      if r["request_id"] in hm]
                out[(coh, win, ep)] = {
                    "n": len(ss),
                    "cor_mean": sum(cor) / len(cor),
                    "svc_mean": sum(svc) / len(svc),
                    "us_mean": (sum(us) / len(us)) if us else None,
                    "us_n": len(us)}
    return meta, out


def main():
    res = {}
    for rd in RUNS:
        name = os.path.basename(rd)
        meta, o = one(rd)
        print(f"== {name} (disturb={meta['disturb']}, "
              f"band={meta.get('band_spec') or 'poor 2300k(기본)'})")
        ent = {}
        for coh in (1, 2):
            for ep in ("search", "reserve", "recommend", "ALL"):
                p = o.get((coh, "pre", ep))
                d = o.get((coh, "during", ep))
                if not p or not d or p["us_mean"] is None:
                    continue
                dcor = d["cor_mean"] - p["cor_mean"]
                dsvc = d["svc_mean"] - p["svc_mean"]
                dus = d["us_mean"] - p["us_mean"]
                ratio = abs(dcor / dus) if dus else float("inf")
                ent[f"c{coh}_{ep}"] = {
                    "d_corrected_ms": round(dcor, 3),
                    "d_service_ms": round(dsvc, 3),
                    "d_envoy_upstream_ms": round(dus, 4),
                    "ratio_cor_over_us": round(ratio, 1),
                    "n_pre": p["n"], "n_during": d["n"]}
                print(f"  c{coh} {ep:>9s}: Δcorrected={dcor:8.3f}ms "
                      f"Δservice={dsvc:8.3f}ms ΔEnvoy(f18)={dus:8.4f}ms "
                      f"비={ratio:8.1f}:1 (n={p['n']}/{d['n']})")
        res[name] = ent
    # 계산 d_acc 참조값
    for nb, label in ((4474, "search 4474B"), (4632, "search 4632B")):
        print(f"참조 d_acc(2300k, {label}) = {nb*8/2300*1.1:.3f} ms (무제한 대비 증가분)")
    json.dump(res, open("/home/user/exp/analysis/envoy_blindness/result.json",
                        "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

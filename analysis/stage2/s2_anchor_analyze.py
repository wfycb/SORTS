#!/usr/bin/env python3
"""STAGE2 신규 앵커(P-S2-0') 실측 — v3 primitive, T=1 s n=3.

런마다 (a) A1 = 가시화=발효 시각(폴러), (b) 결정 내용 앵커(감지 tick 의
observed_rate·d_acc·전환·feasible_set), (c) 반응 지연(발효 기준)을 낸다.
전 시각 .43 현지 시계, 기준 0 = radio_on.t_issue + d43.
"""
import csv
import json
import os
import sys


def poll_events(path):
    A1 = A64 = F1 = F64 = None
    n = 0
    for r in csv.DictReader(open(path)):
        try:
            t = float(r["ts"]); nq = int(r["n_netem_c1"]); nf = int(r["n_filter_c1"])
        except (ValueError, TypeError):
            continue
        n += 1
        if nq >= 1 and A1 is None:
            A1 = t
        if nq >= 64 and A64 is None:
            A64 = t
        if nf >= 1 and F1 is None:
            F1 = t
        if nf >= 64 and F64 is None:
            F64 = t
    return A1, A64, F1, F64, n


def main():
    root = sys.argv[1]
    out = []
    for rid in sorted(os.listdir(root)):
        rd = os.path.join(root, rid)
        if not os.path.isdir(rd) or not os.path.exists(os.path.join(rd, "DONE")):
            continue
        meta = json.load(open(os.path.join(rd, "meta.json")))
        d43 = meta["clock"]["d43_s"]
        mk = [m for m in meta["marks"] if m.get("phase") == "start"][0]
        T0 = mk["t_issue"] + d43
        T = float(meta.get("arm", {}).get("effective", {}).get("ctl_period_s", 1.0))
        pf = os.path.join(root, f"tcpoll_{rid}.csv")
        A1, A64, F1, F64, npoll = poll_events(pf) if os.path.exists(pf) \
            else (None, None, None, None, 0)
        det = None
        ticks = []
        for r in csv.DictReader(open(os.path.join(rd, "decisions.csv"))):
            if r["cohort"] != "c1" or r["class"] != "search":
                continue
            ts = float(r["ts"])
            ticks.append(ts)
            if det is None and r["observed_rate_kbit"] and ts >= T0 - 2:
                det = r
        D = float(det["ts"]) if det else None
        out.append({
            "run_id": rid, "T_s": T,
            "A1_effect_rel_s": None if A1 is None else round(A1 - T0, 3),
            "sweep_ms": None if (A1 is None or F64 is None)
            else round(1000 * (F64 - A1), 1),
            "detect_rel_t_issue_s": None if D is None else round(D - T0, 3),
            "react_from_effect_s": None if (D is None or A1 is None)
            else round(D - A1, 3),
            "apply_ms": float(det["apply_latency_ms"] or 0) if det else None,
            "tick_phase_s": None if D is None else round((D - T0) % T, 3),
            "decision": None if det is None else {
                "observed_rate_kbit": det["observed_rate_kbit"],
                "d_acc_ms": det["d_acc_ms"],
                "feasible_set": det["feasible_set"],
                "chosen_site": det["chosen_site"],
                "changed": det["changed"]},
            "poll_n": npoll, "ssh_return_rel_s": round(mk["t43_done"] - T0, 3),
        })

    def ms(vals):
        v = [x for x in vals if x is not None]
        if not v:
            return None, None
        m = sum(v) / len(v)
        sd = (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5 if len(v) > 1 else 0.0
        return round(m, 4), round(sd, 4)

    a1_m, a1_sd = ms([r["A1_effect_rel_s"] for r in out])
    rk_m, rk_sd = ms([r["react_from_effect_s"] for r in out])
    band = None if a1_m is None else round(max(3 * (a1_sd or 0), 0.030), 3)
    summary = {
        "n": len(out),
        "A1_mean_s": a1_m, "A1_sd_s": a1_sd, "A1_band_pm_s": band,
        "react_from_effect_mean_s": rk_m, "react_sd_s": rk_sd,
        "decision_anchor_consistent": len({json.dumps(r["decision"], sort_keys=True)
                                           for r in out if r["decision"]}) == 1,
        "decision_anchor": out[0]["decision"] if out else None,
    }
    json.dump({"runs": out, "summary": summary},
              open(os.path.join(root, "anchor2_results.json"), "w"),
              ensure_ascii=False, indent=1)
    print(f"# STAGE2 신규 앵커 실측 ({root})\n")
    print("| run | T | A1(발효) | 스윕 | 감지(t_issue) | 반응(발효기준) | tick위상 | d_acc | 전환 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in out:
        d = r["decision"] or {}
        print(f"| {r['run_id']} | {r['T_s']} | {r['A1_effect_rel_s']} | "
              f"{r['sweep_ms']} ms | {r['detect_rel_t_issue_s']} | "
              f"{r['react_from_effect_s']} | {r['tick_phase_s']} | "
              f"{d.get('d_acc_ms')} | {d.get('feasible_set')} chg={d.get('changed')} |")
    print(f"\n요약: {json.dumps(summary, ensure_ascii=False)}")


if __name__ == "__main__":
    main()

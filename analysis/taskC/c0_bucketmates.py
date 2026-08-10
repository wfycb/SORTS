#!/usr/bin/env python3
"""C0: 열화 커넥션이 '버킷 메이트'인지 시간 상관으로 검출한다 (read-only).

tb-radio2.sh v2 구조: ogstun egress 를 dst 포트 하위 6비트로 DIVISOR(기본 64)
버킷에 해싱하고, 버킷마다 독립 netem rate 를 매단다. 커넥션 16개가 64칸에
들어가므로 생일문제로 충돌쌍 기대값 = C(16,2)/64 = 1.875.

충돌하면 두 커넥션이 **하나의 netem 큐를 공유**한다 -> 상대가 큰 응답
(search 4474B)을 직렬화하는 동안 내 응답이 뒤에서 기다린다. 따라서:
  "A 의 느림"은 "B 가 전송 중"일 때만 올라가야 하고, 그 관계는 대칭이며,
  그런 짝은 극소수여야 한다.
포트 해시 충돌이 아니라 다른 원인(경로 전반의 열화)이라면 이런 특정
짝 구조가 안 나온다.
"""
import csv, json, os, sys
from itertools import combinations

RUNS = "/home/user/exp/runs/taskA-20260809"
SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
GUARD = 2.0


def window(m, which):
    d12, d43 = m["clock"]["d12_s"], m["clock"]["d43_s"]
    mk = {x["what"]: x for x in m["marks"]}
    if m["disturb"] == "seq_extreme":
        pair = {"c1only": ("c1_extreme", "c2_extreme"),
                "both": ("c2_extreme", "clear_all")}[which]
        lo43, hi43 = mk[pair[0]]["t43_done"] + GUARD, mk[pair[1]]["t_issue"] + d43 - GUARD
    else:
        s = next(x for x in m["marks"] if x["phase"] == "start")
        e = next(x for x in reversed(m["marks"]) if x["phase"] == "end")
        lo43, hi43 = s["t43_done"] + GUARD, e["t_issue"] + d43 - GUARD
    return lo43 - d43 + d12, hi43 - d43 + d12


def load(rd, coh, lo, hi):
    per = {}
    for r in csv.DictReader(open(os.path.join(rd, "load_%s.csv" % coh))):
        if r["warmup"] == "1":
            continue
        t = float(r["end_ts"])
        if not (lo <= t <= hi):
            continue
        c = int(r["conn"])
        bad = r["status"] != "200" or float(r["corrected_ms"]) > SLO[r["ep"]]
        per.setdefault(c, []).append(
            (float(r["send_ts"]), t, float(r["service_ms"]), r["ep"], bad))
    for c in per:
        per[c].sort()
    return per


def overlap_flags(a, b, big_only=False):
    """a 의 각 요청에 대해 b 의 요청과 시간 겹침이 있는지."""
    bi = [(s, e) for s, e, _, ep, _ in b if (not big_only or ep == "search")]
    out, j, n = [], 0, len(bi)
    for s, e, _, _, _ in a:
        while j < n and bi[j][1] < s:
            j += 1
        k, hit = j, False
        while k < n and bi[k][0] <= e:
            if bi[k][1] >= s:
                hit = True
                break
            k += 1
        out.append(hit)
    return out


def analyze(run, which, coh, divisor=64):
    rd = os.path.join(RUNS, run)
    m = json.load(open(os.path.join(rd, "meta.json")))
    lo, hi = window(m, which)
    per = load(rd, coh, lo, hi)
    conns = sorted(per)
    rate = {c: 100 * sum(1 for x in per[c] if x[4]) / len(per[c]) for c in conns}
    hot = sorted([c for c in conns if rate[c] >= 5], key=lambda c: -rate[c])
    print("== %s / %s / %s   요청 %d, 커넥션 %d, 열화 %d개"
          % (run, which, coh, sum(len(v) for v in per.values()), len(conns), len(hot)))
    print("   위반율: " + " ".join("c%d=%.1f" % (c, rate[c]) for c in conns))
    exp_pairs = len(conns) * (len(conns) - 1) / 2 / divisor
    print("   버킷 충돌쌍 기대값(divisor=%d) = %.2f" % (divisor, exp_pairs))

    rows = []
    for a, b in combinations(conns, 2):
        res = {}
        for x, y, tag in ((a, b, "ab"), (b, a, "ba")):
            fl = overlap_flags(per[x], per[y], big_only=True)
            on = [per[x][i][4] for i in range(len(fl)) if fl[i]]
            off = [per[x][i][4] for i in range(len(fl)) if not fl[i]]
            if not on or not off:
                res[tag] = None
                continue
            res[tag] = (100 * sum(on) / len(on), 100 * sum(off) / len(off), len(on))
        if res.get("ab") and res.get("ba"):
            lift = min(res["ab"][0] - res["ab"][1], res["ba"][0] - res["ba"][1])
            rows.append((lift, a, b, res))
    rows.sort(reverse=True)
    print("   %-9s %-28s %-28s" % ("쌍", "A위반 | B전송중 / 아닐때", "B위반 | A전송중 / 아닐때"))
    for lift, a, b, res in rows[:6]:
        print("   c%-2d-c%-3d  %6.1f%% / %6.1f%%  (n=%5d)   %6.1f%% / %6.1f%%  (n=%5d)   대칭리프트 %+6.1f%%p"
              % (a, b, res["ab"][0], res["ab"][1], res["ab"][2],
                 res["ba"][0], res["ba"][1], res["ba"][2], lift))
    med = rows[len(rows) // 2]
    print("   (중앙값 쌍 c%d-c%d 대칭리프트 %+.1f%%p — 배경 수준)" % (med[1], med[2], med[0]))
    return rows, rate, hot


if __name__ == "__main__":
    for run, which, coh in (("T10_fartier_both_edge", "c1only", "c1"),
                            ("T11_strictfar_both_edge", "c1only", "c1"),
                            ("T8_strictfar_both_radio", "during", "c1"),
                            ("T9_fartier_both_radio", "during", "c1")):
        analyze(run, which, coh)
        print()


def control(run, coh, tag, lo_rel, hi_rel):
    """대조: 밴드 없는 구간/코호트에서는 짝 구조가 없어야 한다."""
    rd = os.path.join(RUNS, run)
    m = json.load(open(os.path.join(rd, "meta.json")))
    lo = m["t_start"] + lo_rel
    hi = m["t_start"] + hi_rel
    per = load(rd, coh, lo, hi)
    conns = sorted(per)
    rate = {c: 100 * sum(1 for x in per[c] if x[4]) / len(per[c]) for c in conns}
    rows = []
    for a, b in combinations(conns, 2):
        res = {}
        for x, y, t in ((a, b, "ab"), (b, a, "ba")):
            fl = overlap_flags(per[x], per[y], big_only=True)
            on = [per[x][i][4] for i in range(len(fl)) if fl[i]]
            off = [per[x][i][4] for i in range(len(fl)) if not fl[i]]
            res[t] = (100*sum(on)/len(on), 100*sum(off)/len(off)) if on and off else None
        if res["ab"] and res["ba"]:
            rows.append((min(res["ab"][0]-res["ab"][1], res["ba"][0]-res["ba"][1]), a, b))
    rows.sort(reverse=True)
    print("-- 대조 %s / %s / %s: 최대 위반율 %.1f%% | 최대 대칭리프트 %+.1f%%p (c%d-c%d) | 중앙값 %+.1f%%p"
          % (run, coh, tag, max(rate.values()), rows[0][0], rows[0][1], rows[0][2],
             rows[len(rows)//2][0]))

#!/usr/bin/env python3
"""C1 판정: 포트 고정 후 극단 밴드 창이 여전히 교란으로서 유효한가.

세 갈래 (지시서):
  A) c1단독 조용 + 양코호트 조용  -> 극단 창 폐기, 교란 강도 재캘리브레이션
  B) c1단독 조용 + 양코호트 붕괴  -> 양코호트 창만 유효, 작업 B 근거 유지
  C) 둘 다 남음                   -> 충돌이 주범이 아니었음, 재조사

before(T10/T11, 충돌 있던 런)와 나란히 찍는다.
"""
import csv, json, os, sys
from itertools import combinations

SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
GUARD = 2.0
QUIET = 2.0        # 이 아래면 "조용" (정상 커넥션 배경 0.5~0.8% 대비 여유)
COLLAPSE = 50.0    # 이 위면 "붕괴"


def windows(m):
    d12, d43 = m["clock"]["d12_s"], m["clock"]["d43_s"]
    mk = {x["what"]: x for x in m["marks"]}
    if m["disturb"] == "seq_extreme":
        w = {"c1only": ("c1_extreme", "c2_extreme"),
             "both": ("c2_extreme", "clear_all")}
    else:
        s = next(x for x in m["marks"] if x["phase"] == "start")
        e = next(x for x in reversed(m["marks"]) if x["phase"] == "end")
        w = {"during": (s["what"], e["what"])}
    out = {}
    for k, (a, b) in w.items():
        lo43, hi43 = mk[a]["t43_done"] + GUARD, mk[b]["t_issue"] + d43 - GUARD
        out[k] = (lo43 - d43 + d12, hi43 - d43 + d12)
    return out


def load(rd, coh, lo, hi):
    per = {}
    for r in csv.DictReader(open(os.path.join(rd, "load_%s.csv" % coh))):
        if r["warmup"] == "1":
            continue
        t = float(r["end_ts"])
        if not (lo <= t <= hi):
            continue
        bad = r["status"] != "200" or float(r["corrected_ms"]) > SLO[r["ep"]]
        per.setdefault(int(r["conn"]), []).append(
            (float(r["send_ts"]), t, float(r["service_ms"]), r["ep"], bad))
    for c in per:
        per[c].sort()
    return per


def overlap_flags(a, b):
    bi = [(s, e) for s, e, _, ep, _ in b if ep == "search"]
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


def max_lift(per):
    best = 0.0
    for a, b in combinations(sorted(per), 2):
        v = []
        for x, y in ((a, b), (b, a)):
            fl = overlap_flags(per[x], per[y])
            on = [per[x][i][4] for i in range(len(fl)) if fl[i]]
            off = [per[x][i][4] for i in range(len(fl)) if not fl[i]]
            if not on or not off:
                v = []
                break
            v.append(100 * sum(on) / len(on) - 100 * sum(off) / len(off))
        if v:
            best = max(best, min(v))
    return best


def report(rd, label):
    m = json.load(open(os.path.join(rd, "meta.json")))
    W = windows(m)
    print("== %s  (%s, port_base=%s, bucket_probe_ok=%s)"
          % (label, m["arm"]["effective"]["subset_policy"],
             m.get("port_base", "-"), m.get("bucket_probe_ok", "-")))
    res = {}
    for wname in W:
        lo, hi = W[wname]
        for coh in ("c1", "c2"):
            per = load(rd, coh, lo, hi)
            if not per:
                continue
            rate = {c: 100 * sum(1 for x in per[c] if x[4]) / len(per[c])
                    for c in per}
            tot = sum(len(v) for v in per.values())
            vio = sum(1 for c in per for x in per[c] if x[4])
            hot = [c for c in per if rate[c] >= 5]
            lift = max_lift(per)
            res[(wname, coh)] = 100 * vio / tot
            print("   %-7s %s  전체 %6.2f%%  열화커넥션 %2d/%d  "
                  "커넥션범위 %.1f~%.1f%%  최대대칭리프트 %+.1f%%p"
                  % (wname, coh, 100 * vio / tot, len(hot), len(per),
                     min(rate.values()), max(rate.values()), lift))
    return res


BEFORE = {("c1only", "c1"): {"far_tier": 5.87, "strict_far": 22.38},
          ("both", "c1"): {"far_tier": 80.94, "strict_far": 87.30},
          ("both", "c2"): {"far_tier": 81.54, "strict_far": 85.86}}

if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else \
        "/home/user/exp/runs/taskC1-20260810"
    got = {}
    for d in sorted(os.listdir(base)):
        rd = os.path.join(base, d)
        if os.path.isdir(rd) and os.path.exists(os.path.join(rd, "meta.json")):
            pol = json.load(open(os.path.join(rd, "meta.json")))["arm"]["effective"]["subset_policy"]
            got[pol] = report(rd, d)
            print()
    print("== before(충돌 있던 T10/T11) 대비")
    print("   %-16s %-22s %-22s" % ("창", "far_tier before→after", "strict_far before→after"))
    for key in (("c1only", "c1"), ("both", "c1"), ("both", "c2")):
        row = []
        for pol in ("far_tier", "strict_far"):
            b = BEFORE[key][pol]
            a = got.get(pol, {}).get(key)
            row.append("%6.2f → %s" % (b, "%6.2f%%" % a if a is not None else "  n/a"))
        print("   %-16s %-22s %-22s" % ("/".join(key), row[0], row[1]))

    c1_quiet = all(v.get(("c1only", "c1"), 99) < QUIET for v in got.values())
    both_collapse = any(v.get(("both", "c1"), 0) > COLLAPSE
                        for v in got.values())
    print("\n== 판정  (조용 기준 <%g%%, 붕괴 기준 >%g%%)" % (QUIET, COLLAPSE))
    if c1_quiet and both_collapse:
        print("   ★ 갈래 B — c1단독 창은 조용해졌고 양코호트 창의 붕괴는 남았다.")
        print("     · 극단 밴드 '단독'은 교란으로서 무효 -> radio 축 c1단독 창 폐기")
        print("     · 양코호트 창만 유효한 교란 -> **작업 B 근거 유지**")
        print("     · 다음 대상 = TASKC_REPORT §6 의 미규명 상위 공유 병목")
    elif c1_quiet and not both_collapse:
        print("   ★ 갈래 A — 둘 다 조용하다. 충돌이 두 창의 위반을 전부 만들었다.")
        print("     · 극단 창 폐기 + 교란 강도 재캘리브레이션 필요")
        print("     · ★인수인계 문서의 '양코호트 94% 실패'도 버그 산물 -> "
              "**작업 B 근거 재수립**")
    elif not c1_quiet and both_collapse:
        print("   ★ 갈래 C — c1단독 창이 여전히 시끄럽다.")
        print("     · 버킷 충돌이 주범이 아니었다(또는 제거가 불완전) -> 재조사")
        print("     · bucket_probe_ok 와 최대대칭리프트를 먼저 확인할 것")
    else:
        print("   ★ 예상 밖 — c1단독은 시끄러운데 양코호트는 조용하다. 재조사.")

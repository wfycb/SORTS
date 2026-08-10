#!/usr/bin/env python3
"""STEP S5 파라미터 재계산 (지시서 v9 §6). 계산만 한다 — 확정은 사람이 한다.

입력:
  ~/m1_v2/m1_<site>_<ep>.json   M1 재측정 (f_c = 무부하 service p95)
  ~/n3_v2/n3_results.json       M2 재측정 (혼합 무릎)
  아래 D_ACC 상수                N2 표C (전송률+손실). 응답 바이트가 안 바뀌었으므로
                                재측정 없이 그대로 쓴다 (STEP S2 판정).
"""
import json
import os
import glob

HOME = os.path.expanduser("~")

# §0.2 경로 지연 — ★ v9 시대 상수 (S2 목표 10 ms, 주입 9.71 ms 시절).
# v10(2026-08-05)에서 S2 가 15.0 으로 올랐다. 이 스크립트는 v9 §6 계획
# 도구라 그대로 둔다 (ISSUES.md I-4). v10 이후 파라미터 재계산에 재사용하려면
# sorts.yaml 의 d_net_ms 를 읽도록 바꿔야 한다. d_net 은 f_c 계산에는 안
# 쓰이고(슬랙 표에만 쓰임) f_c 는 m1_v2 직결 실측이라 이 값과 무관하다.
D_NET = {"S1": 2.0, "S2": 10.0, "S3": 25.006}
# §0.1 현행 SLO 와 가드밴드
SLO = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
GB = 5.0
# N2 표C (전송률+손실) — ms
D_ACC = {
    "정상 20M/0.1%":  {"reserve": 0.156, "search": 2.050,  "recommend": 0.213},
    "Fair 4.5M/0.5%": {"reserve": 0.557, "search": 8.677,  "recommend": 0.827},
    "Poor 2.3M/2.5%": {"reserve": 1.031, "search": 17.070, "recommend": 1.577},
    "극단 1.6M/2.5%": {"reserve": 1.483, "search": 24.553, "recommend": 2.308},
}
EPS = ("reserve", "search", "recommend")
SITES = ("S1", "S2", "S3")

OLD_FC = {"S1": {"reserve": 1.658, "search": 8.624, "recommend": 1.240},
          "S2": {"reserve": 1.332, "search": 6.475, "recommend": 0.975},
          "S3": {"reserve": 1.348, "search": 5.070, "recommend": 1.000}}
OLD_KNEE = {"S1": 200, "S2": 400, "S3": 1200}


def load_fc(d):
    fc = {}
    for p in glob.glob(os.path.join(d, "m1_*.json")):
        b = os.path.basename(p)[3:-5]
        site, ep = b.split("_")
        s = json.load(open(p))
        fc.setdefault(site, {})[ep] = {
            "p50": s["service_ms"]["p50"], "p95": s["service_ms"]["p95"],
            "p99": s["service_ms"]["p99"], "bytes": s["bytes_median"],
            "n200": s["n_200"], "codes": s["codes"]}
    return fc


def main():
    fc = load_fc(os.path.join(HOME, "m1_v2"))
    if not fc:
        raise SystemExit("m1_v2 결과 없음")

    print("=" * 96)
    print("표 S3-1. f_c (무부하 service p95, ms)  이전 -> 이후")
    print(f"{'':4s}" + "".join(f"{ep:>24s}" for ep in EPS))
    for st in SITES:
        line = f"{st:4s}"
        for ep in EPS:
            new = fc.get(st, {}).get(ep, {}).get("p95")
            old = OLD_FC[st][ep]
            line += f"  {old:7.3f} -> {new:7.3f} ({100*(new-old)/old:+5.1f}%)" if new else " " * 24
        print(line)

    print()
    print("표 S3-2. 사이트 간 비율 (S3 대비, f_c p95)")
    print(f"{'':4s}" + "".join(f"{ep:>22s}" for ep in EPS))
    for st in SITES:
        line = f"{st:4s}"
        for ep in EPS:
            new = fc[st][ep]["p95"] / fc["S3"][ep]["p95"]
            old = OLD_FC[st][ep] / OLD_FC["S3"][ep]
            line += f"  {old:6.3f}x -> {new:6.3f}x"
        print(line)

    print()
    print("표 S3-3. 응답 바이트 중앙값 / 200 응답 수")
    for st in SITES:
        print(f"  {st}: " + "  ".join(
            f"{ep}={fc[st][ep]['bytes']}B n200={fc[st][ep]['n200']} codes={fc[st][ep]['codes']}"
            for ep in EPS))

    # ---------------- M2 ----------------
    n3p = os.path.join(HOME, "n3_v2", "n3_results.json")
    knee = {}
    if os.path.exists(n3p):
        res = json.load(open(n3p))
        print()
        print("표 S4-2. 단계별 결과")
        print(f"{'site':5s}{'rps':>6s}{'달성/s':>10s}{'달성%':>8s}{'p50':>10s}{'p99':>11s}{'cpu%':>7s}  게이트 무효%")
        for x in res:
            print(f"{x['site']:5s}{x['rps']:6d}{x['achieved']:10.1f}{x['ach_pct']:8.1f}"
                  f"{x['p50']:10.2f}{x['p99']:11.2f}{x['cpu']:7.1f}  "
                  f"{'통과' if x['gate'] else '실패':4s} {x['invalid']*100:6.2f}"
                  + (f"  <- 무릎: {x['knee']}" if 'knee' in x else ""))
        for st in SITES:
            steps = [x for x in res if x["site"] == st]
            stable = [x for x in steps if "knee" not in x]
            knee[st] = stable[-1]["rps"] if stable else None
        print()
        print("표 S4-1. 마지막 안정 단계 (총 rps)   이전 -> 이후")
        for st in SITES:
            o = OLD_KNEE[st]
            print(f"  {st}: {o:5d} -> {knee[st]}"
                  + (f"  ({knee[st]/o:.2f}x)" if knee[st] else ""))
    else:
        print("\n(n3_v2 결과 없음 — M2 미완료)")

    # ---------------- S5 ----------------
    print()
    print("=" * 96)
    if knee.get("S3"):
        base = knee["S3"]
        print(f"§6.1 도착률 후보 = S3 마지막 안정 용량 {base} x 0.67 = {base*0.67:.0f} rps"
              f"   (이전: 1200 x 0.67 = 804 -> 800 채택)")
    print()
    print("표 S5-1. slack = SLO - GB - d_net - f_c - d_acc   (ms, 음수면 배치 불가)")
    for ep in EPS:
        print(f"\n  --- {ep} (SLO {SLO[ep]}ms, GB {GB}ms) ---")
        print(f"    {'밴드':18s}" + "".join(f"{st:>12s}" for st in SITES) + "   배치")
        for band, dv in D_ACC.items():
            cells, feas = [], []
            for st in SITES:
                sl = SLO[ep] - GB - D_NET[st] - fc[st][ep]["p95"] - dv[ep]
                cells.append(f"{sl:12.2f}")
                if sl > 0:
                    feas.append(st)
            # 최원거리 = d_net 가장 큰 곳
            place = max(feas, key=lambda s: D_NET[s]) if feas else "불가"
            print(f"    {band:18s}" + "".join(cells) + f"   {place}")

    print()
    print("표 S5-2. 배치 계단 (slack>0 중 최원거리)")
    print(f"    {'밴드':18s}" + "".join(f"{ep:>12s}" for ep in EPS))
    steps_seen = {}
    for band, dv in D_ACC.items():
        row = []
        for ep in EPS:
            feas = [st for st in SITES
                    if SLO[ep] - GB - D_NET[st] - fc[st][ep]["p95"] - dv[ep] > 0]
            place = max(feas, key=lambda s: D_NET[s]) if feas else "불가"
            row.append(f"{place:>12s}")
            steps_seen.setdefault(ep, []).append(place)
        print(f"    {band:18s}" + "".join(row))

    print()
    print("§6.3 계단 존속 판정")
    for ep in EPS:
        seq = steps_seen[ep]
        uniq = []
        for s in seq:
            if not uniq or uniq[-1] != s:
                uniq.append(s)
        if len(set(seq)) == 1:
            verdict = "계단 소멸 (전 밴드 동일 배치)"
        elif seq[2] != "S3":          # Poor 에서 이동
            verdict = "계단 유지 (Poor 에서 이동)"
        else:
            verdict = "계단 약화 (극단에서만 이동)"
        print(f"  {ep:10s} {' -> '.join(seq)}   => {verdict}")

    print()
    print("§6.3 계단이 성립하려면 SLO 는 얼마여야 하는가 (Poor 밴드 기준)")
    print("   조건: SLO < GB+d_net(S3)+f_c(S3)+d_acc(Poor)   (S3 불가)")
    print("         SLO > GB+d_net(S2)+f_c(S2)+d_acc(Poor)   (S2 가능)")
    poor = D_ACC["Poor 2.3M/2.5%"]
    ok_all = []
    for ep in EPS:
        hi = GB + D_NET["S3"] + fc["S3"][ep]["p95"] + poor[ep]
        lo = GB + D_NET["S2"] + fc["S2"][ep]["p95"] + poor[ep]
        valid = lo < hi
        mid = (lo + hi) / 2
        print(f"  {ep:10s} 구간 ({lo:.2f}, {hi:.2f}) ms  "
              f"{'중앙값 %.2f' % mid if valid else '<<공집합>>':>18s}  현 SLO={SLO[ep]}"
              f"  {'구간안' if valid and lo < SLO[ep] < hi else '구간밖'}")
        if valid:
            ok_all.append((ep, lo, hi))
    print(f"\n  세 클래스 동시 성립: {'가능' if len(ok_all)==3 else '불가 — ' + str(3-len(ok_all)) + '개 클래스 공집합'}")


if __name__ == "__main__":
    main()

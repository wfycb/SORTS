#!/usr/bin/env python3
"""축소판 통과 조건 9개 판정 (지시서 v6 §3.1)."""
import csv
import json
import os
import subprocess
import sys

BATCH = sys.argv[1] if len(sys.argv) > 1 else "/home/user/exp/runs/smoke2-20260804"
EXPECT_LOC = {"S1": 0.57, "S2": 0.29, "S3": 0.14}


def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True,
                          timeout=120).stdout.strip()


man = json.load(open("/home/user/exp/manifest_smoke.json"))
rids = [r["run_id"] for r in man["runs"]]
S = {}
for rid in rids:
    p = os.path.join(BATCH, rid, "summary.json")
    S[rid] = json.load(open(p)) if os.path.exists(p) else None

verdict = {}
notes = []

# 1) DONE 마커
done = [r for r in rids if os.path.exists(os.path.join(BATCH, r, "DONE"))]
verdict[1] = (len(done) == len(rids), f"{len(done)}/{len(rids)} DONE")

# 2) 응답 바이트 이탈률 0%
dev = {}
for rid in rids:
    if not S[rid]:
        continue
    dev[rid] = max(v["byte_deviation_rate"] for v in S[rid]["sections"].values()
                   if v["byte_deviation_rate"] is not None)
verdict[2] = (all(v == 0 for v in dev.values()) and len(dev) == len(rids),
              ", ".join(f"{k}={v * 100:.3f}%" for k, v in dev.items()))

# 3) 달성 rps >= 목표의 99%
#    사용자 결정 2 (2026-08-04): 조건 3 의 취지는 "부하 생성기가 1400 rps 를
#    낼 수 있는가"였고 그건 §4.4 에서 99.96% 로 독립 통과했다. bl_loc 처럼
#    S1 을 무릎(400 rps) 위로 과적재하는 정책이나 server 교란 구간에서 달성률이
#    떨어지는 것은 측정 결함이 아니라 **측정 대상**이므로 결과로 수용한다.
#    따라서 판정은 어느 사이트도 과적재되지 않는 참조 조건(site_s3 + 교란 없음)
#    에서만 하고, 나머지는 데이터로 기록한다.
ach = {}
for rid in rids:
    if not S[rid]:
        continue
    tgt = S[rid]["target_total_rps"]
    ach[rid] = {k: round(100 * v["achieved_rps"] / tgt, 2)
                for k, v in S[rid]["sections"].items() if "achieved_rps" in v}
worst = {k: min(v.values()) for k, v in ach.items()}
judged = [r for r in rids if S[r] and S[r]["policy"] == "site_s3"
          and S[r]["disturb"] == "none"]
verdict[3] = (bool(judged) and all(worst.get(r, 0) >= 99.0 for r in judged),
              "[판정] " + ", ".join(f"{r}={worst.get(r)}%" for r in judged)
              + "  [기록] " + ", ".join(f"{k}={v}%" for k, v in worst.items()
                                       if k not in judged))

# 4) Envoy 로그 조인율 100%
jr = {rid: S[rid]["join_rate"] for rid in rids if S[rid]}
verdict[4] = (all(v == 1.0 for v in jr.values()) and len(jr) == len(rids),
              ", ".join(f"{k}={v}" for k, v in jr.items()))

# 5) 정책별 분배
d5 = []
ok5 = True
for rid in rids:
    if not S[rid]:
        ok5 = False
        continue
    sh_ = S[rid]["sections"]["pre"]["site_share"]
    pol = S[rid]["policy"]
    if pol == "site_s3":
        good = sh_ and sh_["S3"] == 1.0 and sh_["S1"] == 0.0 and sh_["S2"] == 0.0
    else:
        good = sh_ and all(abs(sh_[k] - EXPECT_LOC[k]) <= 0.08 for k in EXPECT_LOC)
    ok5 &= bool(good)
    d5.append(f"{rid}={'/'.join(f'{100 * sh_[k]:.1f}' for k in ('S1', 'S2', 'S3')) if sh_ else 'NA'}"
              f"{'' if good else ' <-X'}")
verdict[5] = (ok5, "; ".join(d5))

# 6) 교란 대상 격리 (핵심) — 개정 A §3.5
#    반드시 **정책이 고정된** site_s3 런으로 판정한다 (v6 §0.2 의 교훈).
#    지표 구분:
#      d_acc 는 ogstun(UE 하향)에 걸리므로 생성기의 service_ms 에만 나타나고
#      Envoy 업스트림 왕복(f_c)에는 나타나지 않는다. 반대로 참조선에 여유가
#      있는지는 f_c 로만 보인다. 그래서 첫 항목은 f_c, 나머지는 service.
REF_POOR = {"reserve": 0.92, "search": 17.06, "recommend": 1.59}
FC_PASS_MS, FC_STOP_MS, DONE_TARGET = 10.0, 20.0, 400.0
iso = {}
for rid in rids:
    s_ = S[rid]
    if not s_ or s_["disturb"] != "radio" or s_["policy"] != "site_s3":
        continue
    row = {"policy": s_["policy"]}
    fc = s_["sections"]["pre"].get("fc_ms", {}).get("S3", {}).get("search", {})
    row["pre_S3_search_fc_p50"] = fc.get("p50")
    for c in ("1", "2"):
        for ep in ("search", "recommend", "reserve"):
            pre = s_["sections"]["pre"]["by_cohort"][c]["by_endpoint"][ep]["service_p50"]
            dur = s_["sections"]["during"]["by_cohort"][c]["by_endpoint"][ep]["service_p50"]
            e = {"svc_pre": pre, "svc_during": dur, "svc_delta": round(dur - pre, 3)}
            if c == "1":
                e["ref"] = REF_POOR[ep]
                e["ratio"] = round((dur - pre) / REF_POOR[ep], 3)
            row[f"c{c}_{ep}"] = e
    a, b = s_["sections"]["during"]["window_abs_12"]
    row["c1_done_per_s"] = round(
        s_["sections"]["during"]["by_cohort"]["1"]["n"] / (b - a), 1)
    iso[rid] = row
ok6 = bool(iso)
fc_stop = False
for rid, row in iso.items():
    fcv = row["pre_S3_search_fc_p50"]
    fc_ok = fcv is not None and fcv < FC_PASS_MS
    if fcv is not None and fcv > FC_STOP_MS:
        fc_stop = True
    c1_ok = all(0.9 <= row[f"c1_{ep}"]["ratio"] <= 1.5 for ep in REF_POOR)
    c2_ok = all(abs(row[f"c2_{ep}"]["svc_delta"]) <= 1.0 for ep in REF_POOR)
    done_ok = row["c1_done_per_s"] >= DONE_TARGET * 0.97
    row["_판정"] = {"무교란 S3 search f_c p50 <10ms": fc_ok, "코호트1 d_acc 0.9~1.5x": c1_ok,
                  "코호트2 <=1ms": c2_ok, "코호트1 완료율 400/s": done_ok}
    ok6 &= fc_ok and c1_ok and c2_ok and done_ok
if fc_stop:
    notes.append("!!! 무교란 S3 search f_c p50 > 20ms — 개정 A §5.5: 800 도 부족. "
                 "기동하지 말고 600 으로 내릴지 지시를 받아라.")
verdict[6] = (ok6, json.dumps(iso, ensure_ascii=False))

# 7) SLO 초과 비율 + S1 점유율이 실제 숫자
ok7 = True
for rid in rids:
    s = S[rid]
    if not s:
        ok7 = False
        continue
    for sec in s["sections"].values():
        if sec["s1_share"] is None:
            ok7 = False
        for ep, v in sec["by_endpoint"].items():
            if not isinstance(v.get("slo_violation_rate"), float):
                ok7 = False
    ts = os.path.join(BATCH, rid, "s1_share_ts.csv")
    if not os.path.exists(ts) or len(open(ts).readlines()) < 10:
        ok7 = False
verdict[7] = (ok7, "summary.json slo_violation_rate / s1_share / s1_share_ts.csv 존재")

# 8) 종료 후 상태
og = sh("ssh 192.168.0.43 'tc qdisc show dev ogstun'")
en = sh("ssh 192.168.0.43 'tc qdisc show dev eno1'")
rt = sh("ssh 192.168.0.43 \"curl -s http://127.0.0.1:9901/runtime\"")
try:
    ent = json.loads(rt)["entries"]
    weights = {k.split(".")[-1]: v["final_value"] for k, v in ent.items()}
except Exception:
    weights = {}
st = sh("/usr/local/sbin/tb-stress.sh status")
lg = sh("ssh 192.168.0.12 \"pgrep -f 'tb-load[.]py' || true\"")
ok8 = ("netem" not in og and weights.get("site_s3") == "100"
       and all(v == "0" for k, v in weights.items() if k != "site_s3")
       and "netem" in en and st == "stopped" and not lg)
verdict[8] = (ok8, f"ogstun='{og.splitlines()[0] if og else ''}' "
                   f"weights={weights} stress={st} eno1_baseline={'netem' in en} "
                   f"생성기={len(lg.split()) if lg else 0}개")

# 9) server 교란이 1400 rps 에서 유효한가 — 지시서 v6 §3.3
#    강도는 총 500 rps 에서 캘리브레이션됐는데 본실험은 1400 rps 에서 돈다.
#    강도는 바꾸지 않는다. 기준을 벗어나면 수치만 보고하고 정지한다.
d9 = {}
ok9 = False
for rid in rids:
    s = S[rid]
    if not s or s["disturb"] != "server":
        continue
    sec = s["sections"]
    # 개정 A §3.4 기준. 지표는 f_c (service 는 d_net 25ms 가 상수로 깔려 왜곡).
    p95 = {k: sec[k].get("fc_ms", {}).get("S3", {}).get("search", {}).get("p95")
           for k in ("pre", "during", "post")}
    tgt = s["target_total_rps"]
    achv = round(100 * sec["during"]["achieved_rps"] / tgt, 2)
    codes = {}
    for c in (1, 2):
        f = os.path.join(BATCH, rid, f"load_c{c}.csv")
        if not os.path.exists(f):
            continue
        for r in csv.DictReader(open(f)):
            if r["warmup"] == "0":
                codes[r["status"]] = codes.get(r["status"], 0) + 1
    n = sum(codes.values())
    n5xx = sum(v for k, v in codes.items() if k.startswith("5"))
    d9 = {"fc_search_p95_pre_during_post": p95,
          "상승배수": round(p95["during"] / p95["pre"], 3),
          "복귀배수": round(p95["post"] / p95["pre"], 3),
          "달성률_during_pct": achv,
          "5xx_pct": round(100 * n5xx / n, 4) if n else None,
          "codes": codes}
    ok9 = (None not in p95.values() and p95["during"] >= p95["pre"] * 2.0
           and achv >= 90.0 and (n5xx / n if n else 0) < 0.01
           and p95["post"] <= p95["pre"] * 1.1)
verdict[9] = (ok9, json.dumps(d9, ensure_ascii=False))

print(f"=== 축소판 통과 조건 9개 ({BATCH}) ===")
names = {1: "5런 DONE", 2: "바이트 이탈률 0%", 3: "site_s3 달성률 >=99%",
         4: "조인율 100%", 5: "정책별 분배", 6: "교란 대상 격리 (핵심)",
         7: "SLO초과율/S1점유율 존재", 8: "종료 후 상태 복원",
         9: "server 교란 800rps 유효"}
allok = True
for i in range(1, 10):
    ok, det = verdict[i]
    allok &= ok
    print(f"  {i}. {names[i]:<24s} {'O' if ok else 'X'}   {det[:700]}")
for n in notes:
    print(f"\n{n}")
print(f"\n전체: {'통과' if allok else '미통과'}")
sys.exit(0 if allok else 1)

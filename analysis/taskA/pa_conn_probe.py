#!/usr/bin/env python3
"""I-8 복권 1차 감별: 열화 커넥션은 service_ms 가 나쁜가, corrected_ms 만 나쁜가?
service 정상 + corrected 열화  -> 생성기측 백로그(스케줄 밀림) 지배
service 도 열화                -> 경로/서버측 실지연
read-only. 기존 산출물만 읽는다."""
import csv, json, os, statistics as st
RUNS="/home/user/exp/runs/taskA-20260809"
SLO={"reserve":35.0,"search":45.0,"recommend":35.0}
GUARD=2.0
def q(v,p):
    v=sorted(v); return v[min(len(v)-1,int(round(p*(len(v)-1))))] if v else float('nan')

CASES=[("T10_fartier_both_edge","c1only","c1"),("T11_strictfar_both_edge","c1only","c1"),
       ("T9_fartier_both_radio","during","c1"),("T8_strictfar_both_radio","during","c1")]
for run,wname,coh in CASES:
    rd=os.path.join(RUNS,run); m=json.load(open(os.path.join(rd,"meta.json")))
    d12,d43=m["clock"]["d12_s"],m["clock"]["d43_s"]; mk={x["what"]:x for x in m["marks"]}
    if m["disturb"]=="seq_extreme":
        lo43,hi43=mk["c1_extreme"]["t43_done"]+GUARD, mk["c2_extreme"]["t_issue"]+d43-GUARD
    else:
        s=next(x for x in m["marks"] if x["phase"]=="start"); e=next(x for x in reversed(m["marks"]) if x["phase"]=="end")
        lo43,hi43=s["t43_done"]+GUARD, e["t_issue"]+d43-GUARD
    lo,hi=lo43-d43+d12,hi43-d43+d12
    per={}
    for r in csv.DictReader(open(os.path.join(rd,"load_%s.csv"%coh))):
        if r["warmup"]=="1": continue
        t=float(r["end_ts"])
        if not (lo<=t<=hi): continue
        c=per.setdefault(int(r["conn"]),{"n":0,"v":0,"svc":[],"cor":[],"lag":[]})
        c["n"]+=1
        if r["status"]!="200" or float(r["corrected_ms"])>SLO[r["ep"]]: c["v"]+=1
        c["svc"].append(float(r["service_ms"])); c["cor"].append(float(r["corrected_ms"]))
        c["lag"].append(float(r["send_ts"])-float(r["scheduled_ts"]))
    rate={k:100*v["v"]/v["n"] for k,v in per.items()}
    hot=[k for k in per if rate[k]>=5]; cold=[k for k in per if rate[k]<5]
    print("== %s / %s / %s  (열화 %d, 정상 %d)"%(run,wname,coh,len(hot),len(cold)))
    for lbl,grp in (("열화",hot),("정상",cold)):
        if not grp: continue
        svc=[x for k in grp for x in per[k]["svc"]]; cor=[x for k in grp for x in per[k]["cor"]]
        lag=[x for k in grp for x in per[k]["lag"]]
        print("   %s  위반%6.2f%% | service p50/p95/p99 %6.1f/%6.1f/%7.1f | corrected %6.1f/%6.1f/%7.1f | send지연(스케줄대비) p50/p95 %6.1f/%8.1f ms"%(
            lbl, 100*sum(per[k]["v"] for k in grp)/sum(per[k]["n"] for k in grp),
            q(svc,.5),q(svc,.95),q(svc,.99), q(cor,.5),q(cor,.95),q(cor,.99),
            1000*q(lag,.5),1000*q(lag,.95)))

#!/usr/bin/env python3
"""매니페스트 생성 (지시서 v5 §4.1 / §5.1)."""
import json
import sys

POLICIES = ["site_s3", "bl_rr", "bl_lr", "bl_loc"]
# 개정 A §2.1: 기준 용량은 S3 단독 1200 (참조선 site_s3 가 전부 S3 로 보낸다).
# 총 800 = 1200 의 67%. 커넥션 수는 기존 규칙 rps/25 유지 -> 코호트당 16.
CONNS, RPS_PER = 16, 25          # 코호트당 400 rps, 총 800 rps


def run_a(policy, disturb):
    # §4.1 타임라인: t=0 부하시작 / t=60 본측정 / t=180 교란 / t=300 해제 / t=420 끝
    # 본측정 상대시각으로: 교란 120~240, 본측정 360s
    return dict(run_id=f"A_{disturb}_{policy}", type="A", policy=policy,
                disturb=disturb, warmup=60, duration=360,
                disturb_start=120, disturb_end=240, rest=60,
                connections=CONNS, rps_per_connection=RPS_PER, total_rps=800)


def run_b(policy):
    # §4.1 B: t=120 램프시작(본측정 t=60), 10초 x 12단계, t=360 해제(본측정 t=300)
    return dict(run_id=f"B_ramp_{policy}", type="B", policy=policy,
                disturb="ramp", warmup=60, duration=360,
                ramp_start=60, ramp_step_s=10, ramp_clear=300,
                disturb_start=60, disturb_end=300, rest=60,
                connections=CONNS, rps_per_connection=RPS_PER, total_rps=800)


def smoke(policy, disturb):
    # §5.1: 워밍업 20s + 본측정 60s, 교란 t=20~50
    return dict(run_id=f"S_{disturb}_{policy}", type="smoke", policy=policy,
                disturb=disturb, warmup=20, duration=60,
                disturb_start=20, disturb_end=50, rest=30,
                connections=CONNS, rps_per_connection=RPS_PER, total_rps=800)


if __name__ == "__main__":
    which = sys.argv[1]
    if which == "main":
        runs = [run_a(p, d) for d in ("none", "radio", "server") for p in POLICIES]
        runs += [run_b(p) for p in POLICIES]
        m = dict(batch_id=sys.argv[2], note="지시서 v5 본실험 A(12) + B(4)", runs=runs)
        path = "/home/user/exp/manifest.json"
    else:
        runs = [smoke(p, d) for d in ("none", "radio") for p in ("site_s3", "bl_loc")]
        # §5.1 이 지정한 4런에 서버 교란 1런을 더한다. tb-stress.sh 강도는 총
        # 500 rps 에서 잡았는데(1400 rps 는 baseline 자체가 포화라 §3.2 의
        # "5.07 -> 15~20 ms" 가 성립하지 않았다) 본실험은 1400 rps 에서 돈다.
        # 그 강도가 본실험 조건에서도 S3 를 실제로 열화시키는지 확인한다.
        runs.append(smoke("site_s3", "server"))
        m = dict(batch_id=sys.argv[2], note="지시서 v5 §5.1 축소판", runs=runs)
        path = "/home/user/exp/manifest_smoke.json"
    json.dump(m, open(path, "w"), ensure_ascii=False, indent=1)
    tot = sum(r["warmup"] + r["duration"] + r["rest"] for r in runs)
    print(f"{path}: {len(runs)} 런, 예상 {tot / 60:.1f} 분 (+ 런당 오버헤드)")

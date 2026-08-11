#!/usr/bin/env python3
"""STAGE2 §4-1 tc 적용 시각 폴러 — .43 현지 실행, read-only.

컨트롤러(`sorts_ctl.read_rates`)와 **동일한 정규식**으로 netem leaf 를 세고,
같은 시점의 u32 필터(flowid 1:1xxx = 코호트1 버킷) 수를 함께 기록한다.

  netem leaf 생성 = 컨트롤러가 "밴드를 본다"는 시점 (지시값 가시화)
  u32 필터 부착   = 트래픽이 실제로 그 leaf 로 분류되는 시점 (물리적 발효)

tb-radio2.sh apply 는 (1) leaf 64개 -> (2) 필터 순서라 둘이 갈릴 수 있다.
출력 CSV: ts,n_netem_c1,mode_rate_kbit_c1,n_filter_c1
"""
import re
import subprocess
import sys
import time

NETEM_RE = re.compile(
    r"qdisc netem [0-9a-f]+: parent 1:([0-9a-f]+) .*?rate (\d+(?:\.\d+)?)([KMG]?)bit",
    re.IGNORECASE)
MULT = {"": 0.001, "K": 1.0, "M": 1000.0, "G": 1000000.0}
FILTER_RE = re.compile(r"flowid 1:1[0-9a-f]{0,3}\b")


def snap():
    q = subprocess.run(["tc", "qdisc", "show", "dev", "ogstun"],
                       capture_output=True, text=True, timeout=2).stdout
    f = subprocess.run(["tc", "filter", "show", "dev", "ogstun"],
                       capture_output=True, text=True, timeout=2).stdout
    seen = {}
    for m in NETEM_RE.finditer(q):
        cls = int(m.group(1), 16) & 0xF000
        if cls != 0x1000:                 # 코호트1 만
            continue
        kbit = float(m.group(2)) * MULT[m.group(3).upper()]
        seen[kbit] = seen.get(kbit, 0) + 1
    n_leaf = sum(seen.values())
    mode = max(seen, key=seen.get) if seen else ""
    return n_leaf, mode, len(FILTER_RE.findall(f))


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    per = float(sys.argv[2]) if len(sys.argv) > 2 else 0.02
    end = time.time() + dur
    print("ts,n_netem_c1,mode_rate_kbit_c1,n_filter_c1", flush=True)
    nxt = time.time()
    while time.time() < end:
        t = time.time()
        try:
            n, mode, nf = snap()
        except Exception as e:                       # 폴러가 런을 죽이지 않게
            print(f"{t:.4f},ERR,{type(e).__name__},", flush=True)
            n, mode, nf = -1, "", -1
        print(f"{t:.4f},{n},{mode},{nf}", flush=True)
        nxt += per
        d = nxt - time.time()
        if d > 0:
            time.sleep(d)
        else:
            nxt = time.time()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""§1-6 flush 실효 지연 실측 (.43 에서 실행).

front_access.log 를 5 ms 폴링으로 증분 tail 하면서, 각 줄의
  raw   = (읽힌 시각) − START_TIME(필드1, epoch.µs)
  corr  = raw − 필드16(DS_RX_BEG:DS_TX_END, µs)   # 요청 처리시간 보정
을 잰다. 같은 호스트 시계라 클럭 오프셋 없음. 측정 분해능 = 폴링 5 ms.
현재 기동 옵션 --file-flush-interval-msec 100 의 실효값을 보는 것이 목적
— **설정은 바꾸지 않는다** (2단계 입력 전용).

사용: python3 flush_probe.py <측정초> <출력csv>
stdout 에 n/p50/p95/p99/max 요약을 찍는다.
"""
import os
import sys
import time

LOG = "/var/log/envoy/front_access.log"
POLL_S = 0.005


def pctl(xs, q):
    if not xs:
        return None
    return xs[int(round(q * (len(xs) - 1)))]


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    out_csv = sys.argv[2] if len(sys.argv) > 2 else "/var/tmp/flush_meas.csv"
    st = os.stat(LOG)
    off = st.st_size
    partial = ""
    t_end = time.time() + dur
    raws, corrs = [], []
    f = open(out_csv, "w")
    f.write("recv_ts,start_ts,dur_us,raw_ms,corr_ms\n")
    n_bad = 0
    while time.time() < t_end:
        st = os.stat(LOG)
        if st.st_size <= off:
            time.sleep(POLL_S)
            continue
        with open(LOG, "rb") as fh:
            fh.seek(off)
            data = fh.read(min(st.st_size - off, 8 << 20))
        off += len(data)
        now = time.time()
        text = partial + data.decode("utf-8", "replace")
        lines = text.split("\n")
        partial = lines.pop()
        for ln in lines:
            p = ln.split(",")
            if len(p) < 18:
                n_bad += 1
                continue
            try:
                ts0 = float(p[0])
                du = int(p[15]) if p[15].strip().isdigit() else 0
            except ValueError:
                n_bad += 1
                continue
            raw = (now - ts0) * 1000.0
            corr = raw - du / 1000.0
            raws.append(raw)
            corrs.append(corr)
            f.write(f"{now:.6f},{ts0:.6f},{du},{raw:.3f},{corr:.3f}\n")
        time.sleep(POLL_S)
    f.close()
    raws.sort()
    corrs.sort()
    print(f"n={len(raws)} bad={n_bad} poll_ms={POLL_S*1000:g}")
    for name, xs in (("raw", raws), ("corr", corrs)):
        print(f"{name}: p50={pctl(xs, .5):.1f} p95={pctl(xs, .95):.1f} "
              f"p99={pctl(xs, .99):.1f} max={pctl(xs, 1.0):.1f} [ms]"
              if xs else f"{name}: 표본 없음")


if __name__ == "__main__":
    main()

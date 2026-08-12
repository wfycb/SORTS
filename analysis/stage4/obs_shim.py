#!/usr/bin/env python3
"""STAGE4 관측 감쇠 shim — 미러 인터페이스 데몬 (결정·관측 코드 무수정).

원리: 컨트롤러 `read_rates(cfg["iface"])` 는 지정 인터페이스의 netem `rate`
속성만 읽는다(설정값 관측 — I-16/STAGE5_INPUT 에 문서화된 oracle 채널).
이 데몬이 **소스(ogstun)의 상태를 읽어 감쇠 변환 후 미러 인터페이스**
(기본 obsshim0)에 tc 로 재게시하면, 컨트롤러는 iface 설정만 미러로 바꿔
감쇠된 관측을 받는다 — `sorts_ctl.py`·`obs.py` 무수정, 결정 로직 무접촉,
**입력값만** 변형(지시 §2.1-2).

factor (전부 독립 토글, 전부 off = identity 미러):
  --delay D          D초 전의 소스 상태를 게시 (지연 링버퍼)
  --average          밴드를 전 코호트 평균 단일값으로 (TS 23.288 §6.9 지역
                     평균 모사). 무제한 코호트는 UNLIM_KBIT 로 캡핑해 평균.
                     평균값을 전 코호트에 동일 게시.
  --discretize B     임계 경계 리스트(kbit, 쉼표). 보고값 = 구간 대표값,
                     경계 교차 시에만 갱신 (임계 교차 보고 모사)
  --noise EPS --noise-seed S   상대 가우시안 오차 (시드 고정, 재현성)

게시 규약: 미러에 코호트당 leaf **1개** (classid N*0x1000, htb+netem).
read_rates 는 최빈값을 취하므로 1개면 충분. 무제한 = netem 에서 rate 제거.
변경 시에만 tc replace (정상 상태 비용 0 — apply 관례와 동일).
정지: pidfile (--pidfile), I-15 준수. 로그: stdout (게시 이벤트만).
"""
import argparse
import random
import re
import subprocess
import time

NETEM_RE = re.compile(
    r"qdisc netem [0-9a-f]+: parent 1:([0-9a-f]+) .*?rate (\d+(?:\.\d+)?)([KMG]?)bit",
    re.IGNORECASE)
MULT = {"": 0.001, "K": 1.0, "M": 1000.0, "G": 1000000.0}
UNLIM_KBIT = 20000.0        # 평균 계산 시 무제한의 캡 (현 밴드 최상단 20 Mbps)


def read_src(iface):
    """소스 상태: {cohort_idx: rate_kbit or None(무제한/부재)} — read_rates 동일 정규식."""
    try:
        out = subprocess.run(["tc", "qdisc", "show", "dev", iface],
                             capture_output=True, text=True, timeout=2).stdout
    except Exception:
        return {}
    seen = {}
    for m in NETEM_RE.finditer(out):
        cls = int(m.group(1), 16) & 0xF000
        kbit = float(m.group(2)) * MULT[m.group(3).upper()]
        d = seen.setdefault(cls >> 12, {})
        d[kbit] = d.get(kbit, 0) + 1
    return {coh: max(d, key=d.get) for coh, d in seen.items()}


def tc(args_):
    subprocess.run(["tc"] + args_, capture_output=True, timeout=2)


def ensure_tree(dst, n_coh):
    tc(["qdisc", "del", "dev", dst, "root"])
    tc(["qdisc", "add", "dev", dst, "root", "handle", "1:", "htb", "default", "999"])
    tc(["class", "add", "dev", dst, "parent", "1:", "classid", "1:999",
        "htb", "rate", "10000mbit"])
    for c in range(1, n_coh + 1):
        cid = f"{c:x}000"
        tc(["class", "add", "dev", dst, "parent", "1:", "classid", f"1:{cid}",
            "htb", "rate", "10000mbit"])


def publish(dst, coh, rate_kbit):
    cid = f"{coh:x}000"
    if rate_kbit is None:
        # 함정(실측): 무옵션 `replace ... netem` 은 change 의미로 동작해
        # 기존 rate 를 유지한다 — del + add 로 확실히 제거한다.
        tc(["qdisc", "del", "dev", dst, "parent", f"1:{cid}"])
        tc(["qdisc", "add", "dev", dst, "parent", f"1:{cid}",
            "handle", f"{cid}:", "netem"])
    else:
        tc(["qdisc", "replace", "dev", dst, "parent", f"1:{cid}",
            "handle", f"{cid}:", "netem", "rate", f"{int(round(rate_kbit))}kbit"])


class Discretizer:
    def __init__(self, bounds):
        self.bounds = sorted(bounds)            # kbit 오름차순
        self.last_bin = {}

    def bin_of(self, v):
        if v is None:
            return None                          # 무제한은 별도 상태
        for i, b in enumerate(self.bounds):
            if v < b:
                return i
        return len(self.bounds)

    def rep(self, i, v):
        # 구간 대표값: 경계 기하 중심(최하 구간은 최저 경계의 2/3, 최상은 캡)
        bs = self.bounds
        if i == 0:
            return bs[0] * 2 / 3
        if i == len(bs):
            return None if v is None else UNLIM_KBIT
        return (bs[i - 1] * bs[i]) ** 0.5

    def apply(self, coh, v):
        b = self.bin_of(v)
        if self.last_bin.get(coh) == b:
            return "KEEP"                        # 경계 미교차 — 갱신 없음
        self.last_bin[coh] = b
        return None if b is None else self.rep(b, v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="ogstun")
    ap.add_argument("--dst", default="obsshim0")
    ap.add_argument("--n-cohorts", type=int, default=6)
    ap.add_argument("--poll", type=float, default=0.02)
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--average", action="store_true")
    ap.add_argument("--discretize", default="")
    ap.add_argument("--noise", type=float, default=0.0)
    ap.add_argument("--noise-seed", type=int, default=0)
    ap.add_argument("--pidfile", default="/var/tmp/obs_shim.pid")
    a = ap.parse_args()

    open(a.pidfile, "w").write(str(__import__("os").getpid()))
    rng = random.Random(a.noise_seed)
    disc = Discretizer([float(x) for x in a.discretize.split(",")]) \
        if a.discretize else None
    ensure_tree(a.dst, a.n_cohorts)
    buf = []                                    # (ts, state) 링버퍼 (delay)
    cur = {}                                    # 현재 게시 상태
    print(f"obs_shim start src={a.src} dst={a.dst} n={a.n_cohorts} "
          f"delay={a.delay} avg={a.average} disc={a.discretize!r} "
          f"noise={a.noise}@seed{a.noise_seed}", flush=True)
    nxt = time.time()
    while True:
        now = time.time()
        st = read_src(a.src)
        buf.append((now, st))
        cut = now - max(a.delay, 0) - 5.0
        while len(buf) > 2 and buf[0][0] < cut:
            buf.pop(0)
        # delay: now-D 시점 이전의 마지막 상태
        tgt = now - a.delay
        eff = {}
        for ts, s in buf:
            if ts <= tgt:
                eff = s
            else:
                break
        # 전 코호트 뷰 구성 (부재 = 무제한 = None)
        view = {c: eff.get(c) for c in range(1, a.n_cohorts + 1)}
        if a.average:
            vals = [v if v is not None else UNLIM_KBIT for v in view.values()]
            avg = sum(vals) / len(vals)
            view = {c: avg for c in view}
        if a.noise > 0:
            view = {c: (None if v is None else
                        max(1.0, v * (1 + rng.gauss(0, a.noise))))
                    for c, v in view.items()}
        for c, v in view.items():
            if disc is not None:
                dv = disc.apply(c, v)
                if dv == "KEEP":
                    continue
                v = dv
            key = None if v is None else int(round(v))
            if cur.get(c, "unset") != key:
                publish(a.dst, c, v)
                cur[c] = key
                print(f"{now:.3f} publish c{c} -> {key}", flush=True)
        nxt += a.poll
        d = nxt - time.time()
        if d > 0:
            time.sleep(d)
        else:
            nxt = time.time()


if __name__ == "__main__":
    main()

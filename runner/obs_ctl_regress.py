#!/usr/bin/env python3
"""스위치 off 회귀 테스트 (작업 1 Phase 2, 지시 §3.1).

est_resp_bytes=false, est_f_c=false 인 신판 sorts_ctl 이 Phase 0 구판과
**같은 입력에서 같은 라이브 결정**을 내는지 확인한다.

방법: 구판(백업본)과 신판을 각각 서브프로세스로 짧게 돌린다.
  - read_rates 를 결정적 시퀀스로 몽키패치 (호출 횟수 기반, 시간 무관)
  - Controller.apply 를 no-op(0.0ms) 으로 몽키패치 (Envoy 불필요, .40 실행)
  - SIGALRM -> SIGTERM 으로 정상 종료
비교: decisions.csv 의 **기존 12열** (ts 제외 — 벽시계라 실행마다 다르다).
신판은 열이 뒤에 더 있으므로 앞 12열만 자른다. 행 수는 종료 타이밍에 따라
1~2행 다를 수 있어 공통 prefix 를 비교한다 (최소 행 수 요구로 방어).

decisions.csv 를 바이트 동일로 비교하지 않는 이유: Phase 2 가 열을 뒤에
추가했고(지시 §3.3) ts 는 벽시계다. 거동 동일성의 정의는 "동일 입력 ->
동일 라이브 결정(라우팅·slack·d_acc)"이고 그것이 앞 12열이다.
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

EXP = os.path.dirname(os.path.abspath(__file__))
MIN_ROWS = 60          # 5 tick x 6 유닛 x 2 = 최소한 이만큼은 비교해야 의미 있다

DRIVER = r"""
import importlib.util, os, signal, sys
sys.path.insert(0, os.getcwd())          # exp/ 의 obs.py 를 찾게 (cwd=EXP)
mod_path, cfg_path, out_csv, run_s = sys.argv[1:5]
spec = importlib.util.spec_from_file_location("ctl_mod", mod_path)
m = importlib.util.module_from_spec(spec)
sys.modules["ctl_mod"] = m
spec.loader.exec_module(m)

# 결정적 rate 시퀀스 (호출 횟수 기반). None 상태도 포함해 fail-open 경로까지 비교.
SEQ = [None, 20000, 20000, 4500, 4500, 2300, 2300, 1600, 1600, 2300, 4500, 20000]
calls = [0]
def fake_read_rates(iface):
    i = min(calls[0], len(SEQ) - 1)
    calls[0] += 1
    r = SEQ[i]
    return {} if r is None else {0x1000: float(r), 0x2000: float(max(r // 2, 1600))}
m.read_rates = fake_read_rates
m.Controller.apply = lambda self, cohort, klass, site: 0.0

signal.signal(signal.SIGALRM, lambda *_: os.kill(os.getpid(), signal.SIGTERM))
signal.setitimer(signal.ITIMER_REAL, float(run_s))
m.run(cfg_path, out_csv)
"""


def run_one(mod_path, cfg_path, out_csv, run_s=1.6):
    d = tempfile.mkdtemp(prefix="ctl_regress_")
    drv = os.path.join(d, "driver.py")
    open(drv, "w").write(DRIVER)
    r = subprocess.run([sys.executable, drv, mod_path, cfg_path, out_csv,
                        str(run_s)], capture_output=True, text=True, timeout=60,
                       cwd=EXP)
    shutil.rmtree(d, ignore_errors=True)
    if not os.path.exists(out_csv):
        raise SystemExit("{} 실행 실패:\n{}\n{}".format(mod_path, r.stdout, r.stderr))
    return r


def load12(path):
    rows = list(csv.reader(open(path)))
    return [r[1:12] for r in rows[1:]]      # ts(0) 제외, 기존 12열의 나머지 11개


def main():
    old_mod = sys.argv[1] if len(sys.argv) > 1 else None
    if old_mod is None:
        baks = sorted(f for f in os.listdir(EXP)
                      if f.startswith("sorts_ctl.py.") and f.endswith(".bak"))
        if not baks:
            raise SystemExit("구판 백업(sorts_ctl.py.*.bak)이 없다")
        old_mod = os.path.join(EXP, baks[-1])
    new_mod = os.path.join(EXP, "sorts_ctl.py")

    # 테스트 cfg: 주기만 0.1s 로 (짧게 여러 tick). 스위치는 기본(off).
    cfg = yaml.safe_load(open(os.path.join(EXP, "sorts.yaml")))
    assert not cfg.get("est_resp_bytes") and not cfg.get("est_f_c"), \
        "회귀 테스트는 스위치 off 상태의 sorts.yaml 에서 돌려야 한다"
    cfg["t_ctrl_s"] = 0.1
    cfg["obs_log_path"] = "/nonexistent/front_access.log"   # .40 에는 로그가 없다
    d = tempfile.mkdtemp(prefix="ctl_regress_out_")
    cfg_path = os.path.join(d, "cfg.yaml")
    yaml.safe_dump(cfg, open(cfg_path, "w"), allow_unicode=True)

    out_old = os.path.join(d, "decisions_old.csv")
    out_new = os.path.join(d, "decisions_new.csv")
    # importlib 은 .py 확장자만 로더를 잡는다 — 백업본을 임시 .py 로 복사.
    old_py = os.path.join(d, "sorts_ctl_old.py")
    shutil.copy(old_mod, old_py)
    # [작업 B] 작업 A 이후 sorts_ctl 은 자기 디렉터리의 envoy_keys.json 을
    # 읽는다 — 임시 디렉터리로 복사한 구판 옆에도 놓아 준다.
    shutil.copy(os.path.join(EXP, "envoy_keys.json"),
                os.path.join(d, "envoy_keys.json"))
    print("구판:", old_mod)
    run_one(old_py, cfg_path, out_old)
    print("신판:", new_mod)
    run_one(new_mod, cfg_path, out_new)

    a, b = load12(out_old), load12(out_new)
    n = min(len(a), len(b))
    print("행 수: 구판 {} / 신판 {} / 비교 {}".format(len(a), len(b), n))
    if n < MIN_ROWS:
        raise SystemExit("비교 행이 {} < {} — 실행 시간을 늘려라".format(n, MIN_ROWS))
    bad = [(i, a[i], b[i]) for i in range(n) if a[i] != b[i]]
    if bad:
        print("★ 불일치 {}건 (최대 5개 표시):".format(len(bad)))
        for i, ra, rb in bad[:5]:
            print("  행{}:\n    구={}\n    신={}".format(i, ra, rb))
        print("회귀 실패")
        shutil.rmtree(d, ignore_errors=True)
        return 1
    # 신판 부가 확인: obs_state.csv 가 생기고 shadow 열이 채워졌는지
    obs_csv = os.path.join(d, "obs_state_new.csv")
    n_obs = 0
    if os.path.exists(obs_csv):
        n_obs = sum(1 for _ in open(obs_csv)) - 1
    rows_new = list(csv.DictReader(open(out_new)))
    sample = rows_new[len(rows_new) // 2]
    print("회귀 통과: 앞 12열(ts 제외) {}행 동일".format(n))
    print("신판 obs_state 행수: {} / shadow 샘플: const={} bytesonly={} fconly={} "
          "(라이브 chosen={})".format(
              n_obs, sample["chosen_site_const"],
              sample["chosen_site_bytesonly"], sample["chosen_site_fconly"],
              sample["chosen_site"]))
    print("샘플 행(신판, 축약):", {k: sample[k] for k in
          ("cohort", "class", "observed_rate_kbit", "chosen_site",
           "chosen_site_const", "resp_bytes_est", "resp_bytes_src",
           "obs_update_ms", "backlog_bytes")})
    shutil.rmtree(d, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

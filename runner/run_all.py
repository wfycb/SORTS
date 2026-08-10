#!/usr/bin/env python3
"""SORTS D단계 실험 러너 (지시서 v5 STEP P3).

매니페스트에 적힌 런을 하나씩 실행하고 런마다 독립 디렉터리에 원자료와
요약을 남긴다. 판정/해석은 하지 않는다 — 숫자만 낸다.

무인 2시간 실행이 전제이므로 다음을 지킨다.
  - --resume: DONE 마커가 있는 런은 건너뛴다.
  - 사전조건 실패는 그 런만 SKIPPED 로 남기고 다음으로 간다 (전체 중단 금지).
  - 런 사이에 교란 해제 / 가중치 원복 / reserve 초기화를 항상 한다.
  - progress.json 을 하트비트로 갱신한다.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import subprocess
import sys
import time

import yaml

# ------------------------------------------------------------------ 상수
LOADGEN = "192.168.0.12"
ENVOY = "192.168.0.43"
SITES = {"192.168.0.3": "S1", "192.168.0.2": "S2", "192.168.0.40": "S3"}
ENVOY_LOG = "/var/log/envoy/front_access.log"
COHORT_MAP = "/run/tb-cohort.map"
PIN = "taskset -c 6-15"
# sudo 는 .43 의 tb-radio2.sh 하나뿐이고 /etc/sudoers.d/tb-exp 로 NOPASSWD 등록돼
# 있다. 비밀번호는 코드·환경파일 어디에도 두지 않는다. sudo -n 이 실패하면
# 무인 실행 중에 프롬프트에서 조용히 멈추므로 사전조건에서 확인한다.

SLO_MS = {"reserve": 35.0, "search": 45.0, "recommend": 35.0}
EXPECT_BYTES = {"reserve": 36, "search": 4474, "recommend": 200}
D = "inDate=2015-04-09&outDate=2015-04-10"
MIX = (
    f"reserve=1:/reservation?{D}&hotelId=1&customerName=dexp"
    f"&username=Cornell_30&password=0000000000&number=1,"
    f"search=1.5:/hotels?{D}&lat=37.7867&lon=-122.4112,"
    f"recommend=2:/recommendations?require=dis&lat=37.7867&lon=-122.4112"
)
BANDS = {"poor": "rate 2300kbit"}          # 본실험 A 의 radio 교란 (§0.3 Poor)
# 경로 지연 baseline (tb-netem.sh 가 걸어둔 것). 재부팅마다 날아가므로 런마다 확인.
# v10: S2 목표 10 -> 15ms (주입 14.720ms, tc 표시 14.7ms). 계단 극단 계층 복원.
NETEM_EXPECT = ("delay 181us", "delay 14.7ms", "delay 24.7ms")
# I-3(2026-08-07): NETEM_EXPECT 는 qdisc 가 살아 있는지만 본다. 그것만으로는
# 못 잡는 사고가 있다 — qdisc 도 u32 필터도 멀쩡한데 DHCP 로 사이트 IP 가
# 바뀌어 어느 필터에도 안 걸리는 경우다. 그러면 d_net 이 조용히 0 이 되고
# 에러는 하나도 안 난다. 그래서 실제로 패킷이 그 밴드를 지나는지 카운터로
# 확인한다. RTT 임계는 쓰지 않는다: S1 주입값은 181 µs 라 기저 LAN 지터와
# 마진이 없고, D_NET_MS 는 애초에 netem 주입값이 아니라 10KB ping 왕복
# 실측이라 임계 기준으로 쓸 수 없다 (tb-netem.sh 주석).
NETEM_PROBE_N = 20                  # 사이트당 ping 수. .43 에서 -i 0.01 로 ~0.2s
# 판정: delta >= NETEM_PROBE_MIN_RATIO * N. '==' 를 쓰지 않는 이유가 둘 있다.
#  (1) ping 유실 여유.
#  (2) 같은 밴드는 dst 가 그 사이트인 모든 IP 패킷이 지난다. 측정 중 ssh·
#      Envoy 헬스체크 등이 섞이면 delta 가 N 보다 커진다. 상한은 두지 않는다.
NETEM_PROBE_MIN_RATIO = 0.9
S1_KNEE_RPS = 400.0                        # S1 혼합 무릎 (N2 캘리브레이션)
# f_c = 업스트림 왕복(필드18) − d_net. tb-stress.sh 캘리브레이션과 같은 관례를
# 쓴다(10KB ping 실측 RTT). 참조선 여유 판정(개정 A §3.5)이 이 값으로 이뤄진다.
# v10: S2 10 -> 15 (10KB ping 실측 15.021). S1/S3 는 종전 값 유지.
D_NET_MS = {"S1": 2.0, "S2": 15.0, "S3": 25.006}
# 스케줄 소화가 늦어질 때 허용하는 추가 시간. bl_loc 축소판 달성률 30% 기준으로
# 360s 스케줄이 최악 1200s 까지 늘 수 있어 그보다 크게 잡는다.
DRAIN_BUDGET_S = 1500
# 개정 A §3.1: 구간 절단은 벽시계 마크 기준으로 한다. 교란 경계 ±2초는
# 전이 구간으로 버린다 — 셰이핑 적용·해제가 ssh+tc 를 거쳐 즉시가 아니다.
GUARD_S = 2.0


def host_clock_offset(host, n=5):
    """host 시계 − 러너(.40) 시계 [초]. ssh RTT 최소 표본으로 추정한다.

    ssh 세션 수립이 비대칭이라 수십 ms 편향이 남지만, 절단이 ±2초 전이 구간을
    버리므로 무시할 수 있다. 추정치와 그때의 RTT 를 marks.json 에 남겨 나중에
    오차를 감사할 수 있게 한다.
    """
    best = None
    for _ in range(n):
        t0 = time.time()
        r = out(f"ssh {host} 'date +%s.%N'", 30)
        t1 = time.time()
        try:
            rt = float(r)
        except ValueError:
            continue
        if best is None or (t1 - t0) < best[1]:
            best = (rt - (t0 + t1) / 2, t1 - t0)
    return best if best else (0.0, None)


def build_sections(marks, t0_12, duration, d12, ds, de):
    """pre/during/post 를 **.12(부하 생성기) 시계의 절대 구간**으로 만든다.

    행 필터는 end_ts(완료 벽시계)로 하므로 같은 시계여야 한다. scheduled_ts 로
    자르면 생성기가 스케줄에서 밀린 만큼 창이 실제 교란과 어긋난다 — 축소판
    server 런에서 실제 교란 종료가 scheduled t≈43 인데 창은 50 이었다.
    """
    hi = t0_12 + duration
    st = next((m for m in marks if m.get("phase") == "start"), None)
    en = next((m for m in reversed(marks) if m.get("phase") == "end"), None)
    if not st or not en:            # 교란 없는 런 — 명목 구간 (비교용)
        return {"pre": (t0_12, t0_12 + ds), "during": (t0_12 + ds, t0_12 + de),
                "post": (t0_12 + de, hi)}
    return {"pre": (t0_12, st["t_issue"] + d12 - GUARD_S),
            "during": (st["t_done"] + d12 + GUARD_S,
                       en["t_issue"] + d12 - GUARD_S),
            "post": (en["t_done"] + d12 + GUARD_S, hi)}
RAMP_HI, RAMP_LO, RAMP_STEPS = 20000, 1600, 12   # kbit (§4.1 본실험 B)


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def sh(cmd, timeout=300):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def out(cmd, timeout=300):
    return sh(cmd, timeout)[1]


# ------------------------------------------------------------------ 상태 제어
# v10 T2: route 가 코호트x클래스 6개 + 폴백으로 분리됐다. 정책 설정은 7개
# prefix 전부에 같은 가중치를 써야 한다. 구 키(routing.site_weights)는 이제
# 어떤 route 도 읽지 않는다 — 옛 헬퍼를 부르면 조용히 무시되므로 남기지 않는다.
# [작업 A] prefix/클러스터 키 목록은 하드코딩하지 않는다 — gen_envoy_v10.py 가
# config 렌더와 같은 실행에서 뱉는 envoy_keys.json 이 단일 출처다.
# 사람이 목록을 손으로 맞추는 구조는 7-prefix 무시 사고(조용한 무시)의 재발이다.

def load_envoy_keys():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "envoy_keys.json")
    try:
        k = json.load(open(p))
    except OSError as e:
        raise SystemExit(f"envoy_keys.json 없음({e}) — gen_envoy_v10.py 를 먼저 돌려라")
    for key in ("route_prefixes", "cluster_keys", "cluster_sites"):
        if key not in k:
            raise SystemExit(f"envoy_keys.json 에 {key} 없음 — 구버전 산출물. 재생성하라")
    return k


ENVOY_KEYS = load_envoy_keys()
ROUTE_PREFIXES = ENVOY_KEYS["route_prefixes"]
CLUSTER_KEYS = ENVOY_KEYS["cluster_keys"]
# healthy 엔드포인트 기대치 = 클러스터별 엔드포인트 수의 합 (v10 12 -> 작업 A 21)
EXPECT_HEALTHY_EP = sum(len(v) for v in ENVOY_KEYS["cluster_sites"].values())


def set_policy(policy):
    # sorts_reactive 는 초기상태 site_s3 로 두고 컨트롤러가 c*_ 키를 움직인다.
    base = "site_s3" if policy == "sorts_reactive" else policy
    q = "&".join(f"routing.{p}.{k}={100 if k == base else 0}"
                 for p in ROUTE_PREFIXES for k in CLUSTER_KEYS)
    out(f"ssh {ENVOY} \"curl -s -X POST 'http://127.0.0.1:9901/runtime_modify?{q}'\"", 60)


def cohort_ips():
    txt = out(f"ssh {LOADGEN} 'cat {COHORT_MAP}'", 60)
    m = {}
    for line in txt.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            p = line.split("\t")
            if len(p) == 3:
                m[int(p[0])] = (p[1], p[2])
    return m


def radio(spec1, spec2, ips):
    # v2 = 커넥션(=가상 사용자) 단위 셰이핑. v1(코호트 전체를 netem 하나에)은
    # 코호트당 700 rps 의 하향 8.89 Mbit/s 가 밴드 rate 를 넘어 코호트 전체가
    # 붕괴했다 (완료율 700 -> 163/s). 되돌리지 마라.
    c1, c2 = ips[1][1], ips[2][1]
    base = (f"ssh {ENVOY} \"C1_IP={c1} C2_IP={c2} "
            f"sudo -n /usr/local/sbin/tb-radio2.sh ")
    if spec1 is None:
        return out(base + 'clear"', 90)
    return out(base + f"apply '{spec1}' '{spec2}'\"", 90)


def stress(on):
    return out(f"/usr/local/sbin/tb-stress.sh {'start 0' if on else 'stop'}", 90)


# ------------------------------------------------------------------ SORTS 컨트롤러
# v10 T3. 컨트롤러는 .43 에서 돈다 (runtime_modify 로컬 + ogstun 관측).
# pkill 패턴에 [.] 를 쓰는 이유는 kill_loadgens 와 같다 — pkill -f 는 자기를
# 실은 ssh 명령줄도 훑으므로, 리터럴 'sorts_ctl.py' 가 든 원격 명령줄과
# 한 세션에서 부르면 세션 셸부터 죽는다 (2026-08-05 실측). 기동과 회수를
# 반드시 별도 ssh 호출로 나눈다.

def sorts_start(rid):
    out(f"ssh {ENVOY} \"rm -f /var/tmp/decisions_{rid}.csv /var/tmp/obs_state_{rid}.csv; "
        f"nohup python3 ~/sorts_ctl.py --config ~/sorts.yaml "
        f"--out /var/tmp/decisions_{rid}.csv "
        f"> /var/tmp/sorts_{rid}.log 2>&1 & disown\" ", 60)
    time.sleep(1.5)
    if not out(f"ssh {ENVOY} \"pgrep -f 'sorts_ctl[.]py' || true\"", 60):
        raise RuntimeError("SORTS 컨트롤러 기동 실패")


def sorts_stop():
    out(f"ssh {ENVOY} \"pkill -f 'sorts_ctl[.]py'\"", 60)
    for _ in range(10):
        if not out(f"ssh {ENVOY} \"pgrep -f 'sorts_ctl[.]py' || true\"", 60):
            return True
        time.sleep(0.5)
    out(f"ssh {ENVOY} \"pkill -9 -f 'sorts_ctl[.]py'\"", 60)
    return False


def kill_loadgens():
    """부하 생성기 잔재 회수.

    사이트가 포화하면 tb-load 는 스케줄된 요청을 다 낼 때까지 안 끝난다
    (coordinated omission 보정을 위해 뒤처져도 스케줄을 재설정하지 않는다).
    그래서 런이 예산을 넘겨 죽거나 서비스가 정지되면 .12 에 부하 생성기가
    그대로 남아 다음 런을 오염시킨다. 실측으로 확인된 경로다.

    정규식에 [.] 를 쓰는 이유: pkill -f 는 자기 자신의 명령줄도 훑는다.
    'tb-load.py' 로 쓰면 pkill 을 실은 ssh 명령줄이 스스로에 매칭돼 자기를
    죽인다. 'tb-load[.]py' 는 자기 명령줄과 매칭되지 않는다.
    """
    out(f"ssh {LOADGEN} \"pkill -f 'tb-load[.]py.*dexp_'\"", 60)
    for _ in range(20):
        if not out(f"ssh {LOADGEN} \"pgrep -f 'tb-load[.]py' || true\"", 60):
            return True
        time.sleep(0.5)
    out(f"ssh {LOADGEN} \"pkill -9 -f 'tb-load[.]py.*dexp_'\"", 60)
    return False


def cleanup_all(ips):
    """교란 해제 + 부하생성기 회수 + 컨트롤러 회수 + 가중치 원복. 몇 번 불러도 안전하다."""
    try:
        kill_loadgens()
    except Exception as e:
        log(f"  [cleanup] 부하생성기 회수 실패: {e}")
    try:
        # 남으면 이후 런의 가중치를 계속 덮어쓴다 — 고아 부하생성기와 같은 사고
        sorts_stop()
    except Exception as e:
        log(f"  [cleanup] SORTS 컨트롤러 회수 실패: {e}")
    try:
        radio(None, None, ips)
    except Exception as e:
        log(f"  [cleanup] radio clear 실패: {e}")
    try:
        stress(False)
    except Exception as e:
        log(f"  [cleanup] stress stop 실패: {e}")
    try:
        set_policy("site_s3")
    except Exception as e:
        log(f"  [cleanup] 가중치 원복 실패: {e}")


# ------------------------------------------------------------------ 사전조건
def precheck(ips):
    bad = []
    for ip in SITES:
        n = out(f"ssh -o ConnectTimeout=8 {ip} 'docker ps -q | wc -l'", 60)
        if n != "24":
            bad.append(f"{SITES[ip]}({ip}) 컨테이너 {n}/24")
    ready = out(f"ssh {ENVOY} 'curl -s http://127.0.0.1:9901/ready'", 60)
    if ready != "LIVE":
        bad.append(f"Envoy ready={ready!r}")
    # 경로 지연(§0.2 d_net 2/10/25ms)은 테스트베드 상시 baseline 인데 재부팅마다
    # 조용히 사라진다. 없어도 에러가 안 나므로 런마다 확인한다.
    q = out(f"ssh {ENVOY} 'tc qdisc show dev eno1'", 60)
    miss = [d for d in NETEM_EXPECT if d not in q]
    if miss:
        bad.append(f"경로 netem 누락 {miss} (tb-netem.sh apply 필요)")
    # 무선 셰이핑 잔재 확인 — 런 시작 시 ogstun 은 항상 깨끗해야 한다.
    og = out(f"ssh {ENVOY} 'tc qdisc show dev ogstun'", 60)
    if "htb" in og or "netem" in og:
        bad.append("ogstun 에 무선 셰이핑 잔재 있음")
    lg = out(f"ssh {LOADGEN} \"pgrep -f 'tb-load[.]py' || true\"", 60)
    if lg:
        bad.append(f"부하 생성기 잔재 있음 (pid {lg.split()})")
    sc = out(f"ssh {ENVOY} \"pgrep -f 'sorts_ctl[.]py' || true\"", 60)
    if sc:
        bad.append(f"SORTS 컨트롤러 잔재 있음 (pid {sc.split()})")
    if out("/usr/local/sbin/tb-stress.sh status", 60) != "stopped":
        bad.append("S3 에 stress-ng 잔재 있음")
    # NOPASSWD 가 풀리면 sudo 가 프롬프트에서 조용히 멈춰 무인 실행이 죽는다.
    rc, _, _ = sh(f"ssh {ENVOY} \"C1_IP=0.0.0.0 C2_IP=0.0.0.0 "
                  f"sudo -n /usr/local/sbin/tb-radio2.sh show\" >/dev/null 2>&1", 60)
    if rc != 0:
        bad.append("'.43' sudo -n 실패 (NOPASSWD 미등록)")
    cl_json = out(f"ssh {ENVOY} \"curl -s 'http://127.0.0.1:9901/clusters?format=json'\"", 60)
    try:
        cl = json.loads(cl_json)["cluster_statuses"]
        n_ep = sum(1 for c in cl for h in c.get("host_statuses", [])
                   if h["health_status"]["eds_health_status"] == "HEALTHY")
        names = sorted(c["name"] for c in cl)
    except (ValueError, KeyError) as e:
        n_ep, names = -1, []
        bad.append(f"Envoy /clusters 파싱 실패: {e}")
    if n_ep != EXPECT_HEALTHY_EP:
        bad.append(f"Envoy healthy 엔드포인트 {n_ep}/{EXPECT_HEALTHY_EP}")
    # [작업 A] 배포된 envoy.yaml 의 실제 클러스터 집합과 키 목록 단일 출처
    # (envoy_keys.json)가 어긋나면 중단 — check_deploy 와 같은 부류의 검사다.
    # 어긋난 채 돌면 runtime_modify 가 없는 클러스터 키를 조용히 무시한다.
    if names and names != sorted(CLUSTER_KEYS):
        bad.append(f"클러스터 집합 불일치: envoy={names} keys={sorted(CLUSTER_KEYS)}")
    for c in (1, 2):
        if c not in ips:
            bad.append(f"코호트맵에 {c} 없음")
            continue
        iface, addr = ips[c]
        actual = out(f"ssh {LOADGEN} \"ip -4 -o addr show {iface} | awk '{{print \\$4}}' "
                     f"| cut -d/ -f1\"", 60)
        if actual != addr:
            bad.append(f"코호트{c} 맵={addr} 실제={actual!r}")
    bad += check_deploy()
    bad += check_netem_binding()
    return bad


# 컨트롤러 소스는 .40(여기)에 있고 실행은 .43 이다. scp 배포가 수작업이라
# .40 만 고치고 배포를 잊으면 구버전이 조용히 돈다 — 7-prefix 키 사고와
# 같은 부류(조용한 무시)다. md5 로 막는다.
# [Phase 4 §2.2] sorts.yaml 만 예외로 do_run 이 렌더-배포한다: 템플릿 렌더
# -> scp -> check_deploy 재검증 -> 렌더 산출물 재파싱값을 메타에 기록.
# 사후 분석은 파일명 규약이 아니라 그 기록으로 arm 을 안다. 코드 파일
# (sorts_ctl.py/obs.py)의 자동 배포는 여전히 하지 않는다.
DEPLOY_FILES = {"sorts_ctl.py": "~/sorts_ctl.py",
                "sorts.yaml": "~/sorts.yaml",
                "obs.py": "~/obs.py",
                # [작업 A] 키 목록 단일 출처 — sorts_ctl/obs 가 .43 에서 읽는다
                "envoy_keys.json": "~/envoy_keys.json"}
ARM_KEYS = ("est_resp_bytes", "est_f_c", "window_s", "subset_policy",
            "capacity_check", "soft_assign", "c_eff")
SUBSET_POLICIES = ("strict_far", "far_tier", "all_feasible")


def render_deploy_ctl(run):
    """Phase 4 §2.2: arm 스펙 -> sorts.yaml 렌더 -> .43 배포 -> 유효값 회수.

    반환 (bad, eff). bad 가 비어야 성공. eff["effective"] 는 렌더 산출물을
    **다시 파싱**한 값이다 — 스펙이 아니라 실제 배포물이 진실이다.
    """
    d = os.path.dirname(os.path.abspath(__file__))
    tmpl_p = os.path.join(d, "sorts.yaml.tmpl")
    out_p = os.path.join(d, "sorts.yaml")
    # subset_policy 기본은 strict_far — 구 manifest(Phase 4 등)를 그대로 돌리면
    # 기존 단일 사이트 거동이 재현된다. 작업 A 런은 manifest 에 명시한다.
    want = {"est_resp_bytes": bool(run.get("est_resp_bytes", False)),
            "est_f_c": bool(run.get("est_f_c", False)),
            "window_s": float(run.get("window_s", 2.0)),
            "subset_policy": str(run.get("subset_policy", "strict_far")),
            # [작업 B] 기본 off = 작업 A 거동 (회귀 경계)
            "capacity_check": bool(run.get("capacity_check", False)),
            # [작업 B2] 기본 off = 작업 B 거동 (회귀 경계). EXPECTANT 한정
            # 손실 배분 — capacity_check off 면 컨트롤러가 무효 처리한다.
            "soft_assign": bool(run.get("soft_assign", False)),
            # [작업 B3] 밴드 의존 유효 용량. 기본 off = B2 거동 (회귀 경계).
            "c_eff": bool(run.get("c_eff", False))}
    if want["subset_policy"] not in SUBSET_POLICIES:
        return [f"subset_policy 미지값 {want['subset_policy']!r} "
                f"(허용: {SUBSET_POLICIES})"], None
    try:
        txt = open(tmpl_p).read()
    except OSError as e:
        return [f"템플릿 읽기 실패 {tmpl_p}: {e}"], None
    for tok, val in (("%EST_RESP_BYTES%", "true" if want["est_resp_bytes"] else "false"),
                     ("%EST_F_C%", "true" if want["est_f_c"] else "false"),
                     ("%WINDOW_S%", f"{want['window_s']:.1f}"),
                     ("%SUBSET_POLICY%", want["subset_policy"]),
                     ("%CAPACITY_CHECK%", "true" if want["capacity_check"] else "false"),
                     ("%SOFT_ASSIGN%", "true" if want["soft_assign"] else "false"),
                     ("%C_EFF%", "true" if want["c_eff"] else "false")):
        if tok not in txt:
            return [f"템플릿에 토큰 {tok} 없음"], None
        txt = txt.replace(tok, val)
    open(out_p, "w").write(txt)
    rc, _, _ = sh(f"scp -q {out_p} {ENVOY}:~/sorts.yaml", 60)
    if rc != 0:
        return ["sorts.yaml scp 실패"], None
    bad = check_deploy()
    if bad:
        return bad, None
    got = yaml.safe_load(open(out_p))
    eff = {k: got.get(k) for k in ARM_KEYS}
    if (bool(eff["est_resp_bytes"]) != want["est_resp_bytes"]
            or bool(eff["est_f_c"]) != want["est_f_c"]
            or abs(float(eff["window_s"]) - want["window_s"]) > 1e-9
            or str(eff["subset_policy"]) != want["subset_policy"]
            or bool(eff["capacity_check"]) != want["capacity_check"]
            or bool(eff["soft_assign"]) != want["soft_assign"]
            or bool(eff["c_eff"]) != want["c_eff"]):
        return [f"렌더 유효값 불일치: want={want} got={eff}"], None
    md5 = out(f"md5sum {out_p} | cut -d' ' -f1", 60)
    return [], {"requested": want, "effective": eff, "sorts_yaml_md5": md5}


# Phase 4 §2 대비 구간 상수 — s6 런 S3_cpu2_load80_rps800_site_s3 의 2s 윈도
# p95 분포 (analysis/phase4_band_choice.md). 스트레스 강도·도착률을 바꾸면
# 재계산할 것.
STRESSED_FC_LO = "search=10.904,reserve=3.542,recommend=3.709"
OBS_FC_NORMAL_HI = "search=4.875,reserve=1.637,recommend=1.66"


def band_margin_check(run, rundir):
    """Phase 4 §2.4: 런 시작 시 갈림존/대비 자동 확인. 경고는 기록만 하고
    중단하지 않는다 (여유 소멸 자체가 결과다). 반환: 경고 여부."""
    d = os.path.dirname(os.path.abspath(__file__))
    exe = os.path.join(d, "obs_band_margin.py")
    cfg = os.path.join(d, "sorts.yaml")          # 방금 렌더된 산출물 기준
    cmds = [[sys.executable, exe, "--config", cfg]]
    sb = run.get("standing_band_kbit")
    if sb:
        cmds.append([sys.executable, exe, "--config", cfg,
                     "--stressed-fc-lo", STRESSED_FC_LO,
                     "--obs-fc-normal-hi", OBS_FC_NORMAL_HI,
                     "--band", str(int(sb))])
    warn = False
    with open(os.path.join(rundir, "band_margin.txt"), "w") as f:
        for cmd in cmds:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            f.write(f"$ {' '.join(cmd)}\nrc={r.returncode}\n{r.stdout}{r.stderr}\n")
            if r.returncode != 0:
                warn = True
    if warn:
        log("  경고: band margin 검사 경고 있음 (band_margin.txt) — 기록 후 진행")
    return warn


def postcheck():
    """Phase 4 §2.3: 런 종료 시 netem 결합 재확인만 한다.

    MAC pre/postcheck 는 뺐다 — I-3 원인이 공유기 교체(1회성)로 밝혀져
    위험이 하향됐다 (ISSUES.md I-3). 이 postcheck 는 IP 문제와 무관하게
    무인 실행의 피해 범위를 런 1개로 제한하는 값이 있어 남긴다.
    실패해도 배치는 계속하고, 그 런만 SUSPECT 마커를 받는다."""
    return check_netem_binding()


# ---------------------------------------------- [작업 B2 §5] 파일 잠금 가드
# 작업 B 에서 "배치 실행 중 tmpl 편집"으로 SKIPPED 6건이 났다. check_deploy 가
# 막았지만 다시 나올 실수라서, 배치 시작 시 렌더 소스·배포 원본·매니페스트의
# md5 를 스냅샷하고 런 시작마다 대조한다. 변했으면 그 런을 실패(SKIPPED,
# 사유 guard)로 처리한다 — 렌더 산출물(sorts.yaml)은 런마다 바뀌는 게 정상이라
# 가드 대상이 아니다.
GUARD_SRC = ("sorts.yaml.tmpl", "sorts_ctl.py", "obs.py", "envoy_keys.json",
             "run_all.py")
_GUARD = {}          # 파일 경로 -> 배치 시작 시 md5


def guard_snapshot(manifest_path, outdir):
    d = os.path.dirname(os.path.abspath(__file__))
    _GUARD.clear()
    for name in GUARD_SRC:
        p = os.path.join(d, name)
        _GUARD[p] = out(f"md5sum {p} | cut -d' ' -f1", 60)
    mp = os.path.abspath(manifest_path)
    _GUARD[mp] = out(f"md5sum {mp} | cut -d' ' -f1", 60)
    json.dump({"ts": time.time(), "md5": _GUARD},
              open(os.path.join(outdir, "guard_md5.json"), "w"),
              ensure_ascii=False, indent=1)
    log(f"파일 잠금 가드: {len(_GUARD)}개 스냅샷")


def guard_check():
    bad = []
    for p, want in _GUARD.items():
        got = out(f"md5sum {p} 2>/dev/null | cut -d' ' -f1", 60)
        if got != want:
            bad.append(f"guard: {os.path.basename(p)} 이 배치 시작 후 변경됨 "
                       f"(스냅샷 {want[:8]} -> 현재 {got[:8] if got else '없음'}) "
                       f"— 배치 실행 중 편집 금지 (작업 B SKIPPED 6건의 재발 방지)")
    return bad


def thermal_snapshot(rundir):
    """[작업 B2 §2.2] 런 시작 시 각 호스트의 온도/주파수/부하 스냅샷.

    일중 드리프트(+4.4%p) 진단용 계측 — 판정은 하지 않고 기록만 한다.
    가용한 것만 읽고 없으면 빈 값으로 남긴다 (§2.2: 없으면 없다고 보고).
    """
    hosts = {"S1": "192.168.0.3", "S2": "192.168.0.2", "S3": None,
             "envoy": ENVOY, "loadgen": LOADGEN}
    cmd = ("for z in /sys/class/thermal/thermal_zone*; do "
           "[ -e $z/temp ] && echo therm:$(cat $z/type 2>/dev/null):"
           "$(cat $z/temp 2>/dev/null); done; "
           "echo freq:$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq "
           "2>/dev/null); echo load:$(cat /proc/loadavg)")
    snap = {"ts": time.time()}
    for name, ip in hosts.items():
        try:
            txt = out(cmd, 30) if ip is None else \
                out(f"ssh -o ConnectTimeout=8 {ip} '{cmd}'", 60)
        except Exception as e:
            txt = f"error:{e}"
        snap[name] = txt.splitlines()
    json.dump(snap, open(os.path.join(rundir, "thermal.json"), "w"),
              ensure_ascii=False, indent=1)


def check_deploy():
    bad = []
    local_dir = os.path.dirname(os.path.abspath(__file__))
    for name, remote in DEPLOY_FILES.items():
        local = os.path.join(local_dir, name)
        if not os.path.exists(local):
            bad.append(f"배포원본 없음 {local}")
            continue
        lmd5 = out(f"md5sum {local} | cut -d' ' -f1", 60)
        rmd5 = out(f"ssh {ENVOY} \"md5sum {remote} 2>/dev/null | cut -d' ' -f1\"", 60)
        if not rmd5:
            bad.append(f"{name} 미배포 (.43 {remote} 없음) -> scp {local} {ENVOY}:{remote}")
        elif lmd5 != rmd5:
            bad.append(f"{name} 배포 불일치 .40={lmd5[:8]} .43={rmd5[:8]} "
                       f"-> scp {local} {ENVOY}:{remote}")
    return bad


# ------------------------------------------------- netem 결합 검증 (I-3)
def _ip_to_hex(ip):
    """192.168.0.3 -> 'c0a80003'. u32 필터가 출력하는 표기와 맞춘다."""
    return "".join(f"{int(o):02x}" for o in ip.split("."))


def _parse_filter_map(txt):
    """`tc filter show dev eno1 parent 1:` -> {dst IP hex: flowid}.

    IP 를 코드에 박지 않는다. 하드코딩이 I-3 사고의 원인이었으니 검사 코드에
    같은 실수를 들이지 않는다. 매치 오프셋 16(=IP 헤더의 목적지 주소)과
    마스크 /32 인 것만 취해서 출발지 매치(오프셋 12)를 잘못 집지 않게 한다.
    """
    m, cur = {}, None
    for ln in txt.splitlines():
        fm = re.search(r"flowid\s+(\d+:\d+)", ln)
        if fm:
            cur = fm.group(1)
        mm = re.search(r"match\s+([0-9a-f]{8})/([0-9a-f]{8})\s+at\s+(\d+)", ln)
        if mm and cur and mm.group(2) == "ffffffff" and mm.group(3) == "16":
            m[mm.group(1)] = cur
    return m


def _parse_class_pkts(txt):
    """`tc -s class show dev eno1` -> {flowid: 누적 Sent 패킷수}."""
    d, fid = {}, None
    for ln in txt.splitlines():
        cm = re.match(r"class\s+\S+\s+(\d+:\d+)", ln.strip())
        if cm:
            fid = cm.group(1)
            continue
        sm = re.search(r"Sent\s+\d+\s+bytes\s+(\d+)\s+pkt", ln)
        if sm and fid:
            d[fid] = int(sm.group(1))
            fid = None
    return d


# ---------------------------------------------------------------- 작업 C
# I-8 규명 결과: tb-radio2.sh v2 는 ogstun egress 를 **dst 포트 하위 6비트**로
# 코호트당 64 버킷에 해싱하고 버킷마다 독립 netem 을 매단다. 커널 배정 포트를
# 쓰면 커넥션 16개가 64칸에 무작위로 떨어져 매 런 평균 1.9쌍이 같은 버킷을
# 공유했다(= 복권). 생성기에서 소스 포트를 고정해 충돌을 결정적으로 0 으로
# 만들고, **여기서 실측으로 확인한다** (산술 추론 금지 — 조용히 깨지는 부류다).
PORT_BASE_LO, PORT_BASE_HI = 20032, 31552   # 64 배수, ephemeral(32768~) 아래
PORT_BUCKET_MOD = 64


def port_base_for_run():
    slots = (PORT_BASE_HI - PORT_BASE_LO) // PORT_BUCKET_MOD + 1
    return PORT_BASE_LO + PORT_BUCKET_MOD * (int(time.time() // 60) % slots)


def _parse_ogstun_classes(txt):
    """'tc -s class show dev ogstun' -> {코호트: {버킷: 패킷수}}.

    classid 1:1xxx = 코호트1, 1:2xxx = 코호트2 (tb-radio2.sh setup_cohort).
    """
    out_ = {}
    cid = None
    for line in txt.splitlines():
        m = re.match(r"\s*class htb 1:([0-9a-f]+)", line)
        if m:
            cid = int(m.group(1), 16)
            continue
        m = re.search(r"Sent \d+ bytes (\d+) pkt", line)
        if m and cid is not None:
            coh, bucket = cid >> 12, cid & (PORT_BUCKET_MOD - 1)
            if coh in (1, 2):
                out_.setdefault(coh, {})[bucket] = int(m.group(1))
            cid = None
    return out_


def probe_buckets(rundir, tag, banded, expect_conns, port_base):
    """교란 창 한가운데서 실측: (1) 실제 소스 포트, (2) 버킷별 패킷 카운터.

    banded = 밴드가 걸린 코호트 집합. 그 코호트는 활성 버킷 수가 커넥션 수와
    **정확히 같아야** 한다. 적으면 충돌이 남아 있는 것이다 (I-8 재발).
    진단용으로 ogstun/호스트 자원 스냅샷도 같이 남긴다 (양 코호트 창의
    미규명 상위 병목 후보 좁히기 — TASKC_REPORT §6).
    """
    bad = []
    ss = out(f"ssh {LOADGEN} \"ss -tn state established '( dport = :8080 )' "
             f"| grep -o '10\\.46\\.0\\.[0-9]*:[0-9]*' || true\"", 60)
    ports = {}
    for tok in ss.split():
        ip, p = tok.rsplit(":", 1)
        ports.setdefault(ip, []).append(int(p))
    cls_txt = out(f"ssh {ENVOY} 'tc -s class show dev ogstun'", 120)
    buckets = _parse_ogstun_classes(cls_txt)

    lines = [f"=== probe {tag} (banded={sorted(banded)}, "
             f"port_base={port_base}, expect={expect_conns}) ==="]
    for ip in sorted(ports):
        ps = sorted(ports[ip])
        bk = sorted({p % PORT_BUCKET_MOD for p in ps})
        lines.append(f"ss {ip}: 커넥션 {len(ps)} 포트 {ps[0]}~{ps[-1]} "
                     f"distinct_ports={len(set(ps))} distinct_buckets={len(bk)}")
        if len(bk) != len(set(ps)):
            bad.append(f"{tag}: {ip} 포트 {len(set(ps))}개가 버킷 {len(bk)}칸에 "
                       f"— 충돌 잔존")
    # 파서가 빈 결과를 내면 아래 루프가 0회 돌아 **조용히 통과**한다.
    # 밴드가 걸린 코호트는 반드시 나와야 한다 — 없으면 그것부터 실패다.
    for coh in sorted(banded):
        if coh not in buckets:
            bad.append(f"{tag}: 코호트{coh} 클래스가 tc 출력에 없음 "
                       f"(파싱 실패이거나 밴드 미적용) — 검사 무효")
    for coh in sorted(buckets):
        act = {b: n for b, n in buckets[coh].items() if n > 0}
        lines.append(f"tc 코호트{coh}: 활성 버킷 {len(act)} "
                     f"pkt합 {sum(act.values())} 버킷 {sorted(act)}")
        if coh in banded and len(act) != expect_conns:
            bad.append(f"{tag}: 코호트{coh} 활성 버킷 {len(act)} != "
                       f"커넥션 {expect_conns} — 버킷 충돌 잔존 (I-8 재발)")
    diag = out(f"ssh {ENVOY} 'echo ---qdisc---; tc -s qdisc show dev ogstun "
               f"| head -40; echo ---link---; ip -s link show ogstun; "
               f"echo ---load---; cat /proc/loadavg; echo ---vmstat---; "
               f"vmstat 1 2 | tail -2; echo ---top---; "
               f"top -bn1 | head -12'", 120)
    with open(os.path.join(rundir, "bucket_probe.txt"), "a") as f:
        f.write("\n".join(lines) + "\n" + cls_txt + "\n--- diag ---\n"
                + diag + "\n\n")
    for ln in lines:
        log("  " + ln)
    if bad:
        for b in bad:
            log(f"  ★ 버킷 검사 실패: {b}")
    return bad


def check_netem_binding():
    """사이트별로 ping 을 쏘고 해당 netem 밴드의 패킷 카운터가 증가하는지 본다.

    잡는 것: 사이트 IP 가 바뀌어 u32 필터에 안 걸리는 상태(=d_net 이 조용히 0).
    NETEM_EXPECT 의 qdisc 존재 확인과는 잡는 사고가 다르므로 둘 다 남긴다.
    RTT 는 기록만 하고 판정에는 쓰지 않는다 — 나중에 netem 값이 바뀌었을 때
    SKIPPED 로그만 보고도 참고할 수 있게 남겨 두는 것이다.
    """
    bad = []
    # SITES(IP 출처)와 sorts.yaml 의 d_net_ms(사이트 목록)가 어긋나면 조용히
    # 일부 사이트를 안 보게 된다. 사이트가 늘 때 놓치기 쉬우므로 먼저 막는다.
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sorts.yaml")
    try:
        yaml_sites = set(yaml.safe_load(open(cfg_path))["d_net_ms"])
    except Exception as e:
        return [f"sorts.yaml d_net_ms 읽기 실패 ({type(e).__name__}: {e}) "
                f"-> {cfg_path} 확인"]
    if yaml_sites != set(SITES.values()):
        bad.append(f"사이트 키 불일치 run_all.SITES={sorted(SITES.values())} "
                   f"sorts.yaml d_net_ms={sorted(yaml_sites)} "
                   f"-> 양쪽을 맞춰라. 어긋난 채로 두면 빠진 사이트는 netem "
                   f"검증을 건너뛴다")

    fmap = _parse_filter_map(out(f"ssh {ENVOY} 'tc filter show dev eno1 parent 1:'", 60))
    if not fmap:
        return bad + ["eno1 에 dst-IP u32 필터가 하나도 없음 -> "
                      "'sudo /usr/local/sbin/tb-netem.sh apply' 후 "
                      "'tc filter show dev eno1 parent 1:' 로 확인"]

    for ip, name in SITES.items():
        hexv = _ip_to_hex(ip)
        fid = fmap.get(hexv)
        if fid is None:
            bad.append(f"{name}({ip}) netem 필터 미결합: dst {hexv} 를 매치하는 "
                       f"필터 없음 (현재 매치 {sorted(fmap)}) -> 사이트 IP 가 "
                       f"바뀌었는지 'ip -br a' 로 확인. 바뀌었으면 IP 를 되돌려라 "
                       f"(tb-netem.sh 가 IP 하드코딩이다). d_net 이 0 으로 도는 "
                       f"무에러 실패다")
            continue
        # 스냅샷·ping·스냅샷을 한 ssh 안에서 돌린다. 사이 간격이 벌어지면
        # 다른 트래픽이 섞일 여지가 커진다.
        txt = out(f"ssh {ENVOY} 'tc -s class show dev eno1; echo ===PING===; "
                  f"ping -c {NETEM_PROBE_N} -i 0.01 -W 1 {ip}; echo ===AFTER===; "
                  f"tc -s class show dev eno1'", 120)
        parts = txt.split("===PING===")
        if len(parts) != 2 or "===AFTER===" not in parts[1]:
            bad.append(f"{name}({ip}) netem 검증 출력 파싱 실패 -> .43 에서 "
                       f"'tc -s class show dev eno1' 와 ping 이 되는지 직접 확인")
            continue
        pre = _parse_class_pkts(parts[0])
        ping_txt, post_txt = parts[1].split("===AFTER===", 1)
        post = _parse_class_pkts(post_txt)

        rx = re.search(r"(\d+) received", ping_txt)
        n_rx = int(rx.group(1)) if rx else 0
        if n_rx == 0:
            bad.append(f"{name}({ip}) ping 무응답 ({NETEM_PROBE_N} 발사, 0 수신) "
                       f"-> 호스트가 떠 있는지, IP 가 맞는지 확인")
            continue
        rtt = re.search(r"rtt [^=]*= ([\d.]+)/([\d.]+)/([\d.]+)", ping_txt)
        rtt_s = f" rtt min/avg/max={rtt.group(1)}/{rtt.group(2)}/{rtt.group(3)}ms" \
            if rtt else ""

        if fid not in pre or fid not in post:
            bad.append(f"{name}({ip}) flowid {fid} 클래스 카운터를 못 읽음 -> "
                       f".43 에서 'tc -s class show dev eno1' 출력 형식 확인")
            continue
        delta = post[fid] - pre[fid]
        need = NETEM_PROBE_MIN_RATIO * NETEM_PROBE_N
        # RTT 는 판정에 쓰지 않는다. 기록만 한다.
        log(f"    netem {name}({ip}) flowid={fid} delta={delta}/{NETEM_PROBE_N}"
            f"{rtt_s}")
        if delta < need:
            bad.append(f"{name}({ip}) netem 밴드 미통과: 기대 flowid={fid} "
                       f"delta={delta} < {need:g} (ping {NETEM_PROBE_N} 발사, "
                       f"{n_rx} 수신){rtt_s} -> 필터는 있는데 패킷이 그 밴드로 "
                       f"안 간다. 'tc filter show dev eno1 parent 1:' 의 매치 "
                       f"IP 와 실제 사이트 IP 가 같은지, prio 가 더 높은 다른 "
                       f"필터가 먼저 잡아가는지 확인")
    return bad


# ------------------------------------------------------------------ 요약
def pctl(xs, q):
    if not xs:
        return None
    return round(xs[int(round(q * (len(xs) - 1)))], 3)


def summarize(rundir, run, t_meas, hostmap, sections):
    rows = []
    for c in (1, 2):
        f = os.path.join(rundir, f"load_c{c}.csv")
        if not os.path.exists(f):
            continue
        for r in csv.DictReader(open(f)):
            if r["warmup"] != "0":
                continue
            r["cohort"] = c
            rows.append(r)

    # 응답 바이트 판정은 두 관례가 의도적으로 다르다 (ISSUES.md I-5):
    #  - 여기(위반율 회계): EXPECT_BYTES ±10% 대역. search 4474/4632 둘 다 통과.
    #  - obs.py(관측기): 바이트를 게이트로 쓰지 않는다 — resp_bytes 추정이
    #    목적인데 기대값으로 입력을 거르면 순환이다 (obs.py:48).
    # 같은 코드로 통일하지 말 것.
    def is_valid(r):
        if r["status"] != "200":
            return False
        e = EXPECT_BYTES.get(r["ep"])
        if e is None:
            return False
        b = int(r["bytes_recv"])
        return abs(b - e) <= (e * 0.10 if e > 1000 else 0)

    # 클럭 정합 진단: Envoy START_TIME(.43) - 부하생성기 send_ts(.12)
    offs = []
    for r in rows[::997]:
        st = hostmap.get(r["request_id"], (None, None, None))[1]
        if st is not None:
            offs.append((st - float(r["send_ts"])) * 1000.0)
    offs.sort()

    res = {
        "run_id": run["run_id"], "policy": run["policy"], "disturb": run["disturb"],
        "target_total_rps": run.get("total_rps"),
        "slo_ms": SLO_MS,
        "clock_offset_est_ms": {"n": len(offs), "p50": pctl(offs, .5),
                                "p05": pctl(offs, .05), "p95": pctl(offs, .95)},
        "join_rate": None,
        "sections": {},
    }
    joined = sum(1 for r in rows if r["request_id"] in hostmap)
    res["join_rate"] = round(joined / len(rows), 6) if rows else None

    for name, (a, b) in sections.items():
        # 개정 A §3.1: end_ts(완료 벽시계) 로 자른다. scheduled_ts 로 자르면
        # 생성기가 스케줄에서 밀린 만큼 창이 실제 교란 구간과 어긋난다.
        sub = [r for r in rows if a <= float(r["end_ts"]) < b]
        ok = [r for r in sub if is_valid(r)]
        sec = {"window_abs_12": [round(a, 3), round(b, 3)],
               "window_rel_s": [round(a - t_meas, 3), round(b - t_meas, 3)],
               "n": len(sub), "n_valid": len(ok),
               "byte_deviation_rate": round(1 - len(ok) / len(sub), 6) if sub else None}
        # 창이 절대 벽시계이므로 완료 건수 ÷ 창 길이가 곧 달성률이다.
        sec["achieved_rps"] = round(len(ok) / (b - a), 2) if b > a else None
        # 엔드포인트별
        sec["by_endpoint"] = {}
        for ep in sorted(EXPECT_BYTES):
            v = sorted(float(r["corrected_ms"]) for r in ok if r["ep"] == ep)
            # service_ms 도 같이 낸다. corrected 는 생성기 스케줄 밀림까지
            # 포함하므로 SLO 판정에는 맞지만, §0.3 의 d_acc(직렬화 시간)와
            # 비교할 수 있는 것은 service 쪽이다. 두 지표는 서로 다른 것을
            # 재므로 섞어 쓰면 안 된다.
            sv = sorted(float(r["service_ms"]) for r in ok if r["ep"] == ep)
            n_all = sum(1 for r in sub if r["ep"] == ep)
            if not n_all:
                continue
            viol = sum(1 for r in sub if r["ep"] == ep
                       and (not is_valid(r) or float(r["corrected_ms"]) > SLO_MS[ep]))
            sec["by_endpoint"][ep] = {
                "n": n_all, "n_valid": len(v),
                "corrected_p50": pctl(v, .50), "corrected_p95": pctl(v, .95),
                "corrected_p99": pctl(v, .99),
                "service_p50": pctl(sv, .50), "service_p95": pctl(sv, .95),
                "service_p99": pctl(sv, .99),
                "slo_violation_rate": round(viol / n_all, 6),
            }
        # 코호트별
        sec["by_cohort"] = {}
        for c in (1, 2):
            csub = [r for r in sub if r["cohort"] == c]
            if not csub:
                continue
            viol = sum(1 for r in csub if not is_valid(r)
                       or float(r["corrected_ms"]) > SLO_MS[r["ep"]])
            sec["by_cohort"][str(c)] = {"n": len(csub),
                                        "slo_violation_rate": round(viol / len(csub), 6)}
            sec["by_cohort"][str(c)]["by_endpoint"] = {}
            for ep in sorted(EXPECT_BYTES):
                v = sorted(float(r["corrected_ms"]) for r in csub
                           if r["ep"] == ep and is_valid(r))
                n_all = sum(1 for r in csub if r["ep"] == ep)
                if not n_all:
                    continue
                vio = sum(1 for r in csub if r["ep"] == ep and (not is_valid(r)
                          or float(r["corrected_ms"]) > SLO_MS[ep]))
                sv = sorted(float(r["service_ms"]) for r in csub
                            if r["ep"] == ep and is_valid(r))
                sec["by_cohort"][str(c)]["by_endpoint"][ep] = {
                    "n": n_all, "corrected_p50": pctl(v, .50),
                    "corrected_p95": pctl(v, .95), "corrected_p99": pctl(v, .99),
                    "service_p50": pctl(sv, .50), "service_p95": pctl(sv, .95),
                    "slo_violation_rate": round(vio / n_all, 6)}
        # 사이트 분배
        dist = {"S1": 0, "S2": 0, "S3": 0, "unjoined": 0}
        for r in sub:
            h = hostmap.get(r["request_id"], (None, None, None))[0]
            dist[SITES.get(h, "unjoined") if h else "unjoined"] += 1
        tot = sum(dist[k] for k in ("S1", "S2", "S3"))
        sec["site_distribution"] = dist
        sec["site_share"] = ({k: round(dist[k] / tot, 6) for k in ("S1", "S2", "S3")}
                             if tot else None)
        sec["s1_share"] = round(dist["S1"] / tot, 6) if tot else None
        # S1 몫과 무릎 대비 비. 어떤 정책이 언제부터 S1 을 과적재해 붕괴
        # 상태였는지를 런 요약만 보고 알 수 있게 한다 (판정은 하지 않는다).
        # 무릎(400 rps)은 **용량** 한계이므로 비교 대상은 S1 으로 들어가는
        # 유입이어야 한다. 완료 건수로 재면 포화한 S1 은 정의상 용량까지만
        # 완료하므로 무릎비가 1 을 넘을 수 없어 "과적재"를 못 본다.
        # 유입 추정 = 목표 도착률 × S1 분배 몫 (개정 A §2.5 의 계산과 같다).
        win = max(b - a, 1e-9)
        tgt = run.get("total_rps")
        # 사이트별 f_c (서버 처리시간). 참조선에 여유가 있는지를 보는 지표다.
        fc = {}
        for r in ok:
            h = hostmap.get(r["request_id"], (None, None, None))
            site = SITES.get(h[0]) if h[0] else None
            if site is None or h[2] is None:
                continue
            fc.setdefault(site, {}).setdefault(r["ep"], []).append(
                h[2] / 1000.0 - D_NET_MS[site])
        sec["fc_ms"] = {st: {ep: {"n": len(v), "p50": pctl(sorted(v), .50),
                                  "p95": pctl(sorted(v), .95)}
                             for ep, v in eps.items()}
                        for st, eps in sorted(fc.items())}
        sec["s1_completed_rps"] = round(dist["S1"] / win, 2)
        sec["s1_share_rps"] = (round(tgt * sec["s1_share"], 2)
                               if tgt and sec["s1_share"] is not None
                               else sec["s1_completed_rps"])
        sec["s1_knee_rps"] = S1_KNEE_RPS
        sec["s1_knee_ratio"] = round(sec["s1_share_rps"] / S1_KNEE_RPS, 3)
        res["sections"][name] = sec

    # S1 점유율 1초 시계열 (대표 그림)
    ts_path = os.path.join(rundir, "s1_share_ts.csv")
    buckets = {}
    for r in rows:
        t = int(float(r["end_ts"]) - t_meas)      # 완료 벽시계 기준 (§3.1)
        h = hostmap.get(r["request_id"], (None, None, None))[0]
        s = SITES.get(h) if h else None
        d = buckets.setdefault(t, {"S1": 0, "S2": 0, "S3": 0, "unjoined": 0})
        d[s if s else "unjoined"] += 1
    with open(ts_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_rel_s", "n_S1", "n_S2", "n_S3", "n_unjoined", "s1_share"])
        for t in sorted(buckets):
            d = buckets[t]
            tot = d["S1"] + d["S2"] + d["S3"]
            w.writerow([t, d["S1"], d["S2"], d["S3"], d["unjoined"],
                        f"{d['S1'] / tot:.6f}" if tot else ""])
    return res


def load_hostmap(slice_path):
    """request_id -> (upstream_host_ip, envoy_start_ts, upstream_rt_us)

    필드 18 = COMMON_DURATION(US_TX_BEG:US_RX_END:us) = 업스트림 왕복(us).
    여기서 d_net 을 빼면 f_c(서버 처리시간)다 — §0.2 와 tb-stress.sh
    캘리브레이션이 쓴 것과 같은 정의.
    """
    hm = {}
    op = gzip.open if slice_path.endswith(".gz") else open
    with op(slice_path, "rt", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 11:
                continue
            try:
                us = int(p[17]) if len(p) > 17 and p[17].strip().isdigit() else None
                hm[p[1]] = (p[10].split(":")[0], float(p[0]), us)
            except ValueError:
                continue
    return hm


# ------------------------------------------------------------------ 런 실행
def do_run(run, outdir, ips):
    rid = run["run_id"]
    rundir = os.path.join(outdir, rid)
    os.makedirs(rundir, exist_ok=True)
    warmup = run["warmup"]
    duration = run["duration"]
    conns = run["connections"]
    rps_per = run["rps_per_connection"]
    # [야간배치 20260810] 용량 스윕은 단일 클래스 부하가 필요하다. 런에 mix 가
    # 없으면 종전 MIX 그대로라 기존 manifest 의 거동은 변하지 않는다.
    mix = run.get("mix", MIX)

    # [작업 B2 §5] 파일 잠금 가드 — 렌더보다 먼저. 변경 감지 시 이 런 실패.
    bad = guard_check()
    if bad:
        log(f"  가드 실패 -> SKIPPED: {bad}")
        json.dump({"run_id": rid, "status": "SKIPPED", "reasons": bad,
                   "ts": time.time()}, open(os.path.join(rundir, "SKIPPED"), "w"),
                  ensure_ascii=False, indent=1)
        return "SKIPPED"
    # Phase 4 §2.2: arm 렌더-배포가 사전조건보다 먼저다 — precheck 의
    # check_deploy 가 렌더 산출물 기준으로 재검증하게 된다.
    bad, arm_eff = render_deploy_ctl(run)
    if not bad:
        bad = precheck(ips)
    if bad:
        log(f"  사전조건 실패 -> SKIPPED: {bad}")
        json.dump({"run_id": rid, "status": "SKIPPED", "reasons": bad,
                   "ts": time.time()}, open(os.path.join(rundir, "SKIPPED"), "w"),
                  ensure_ascii=False, indent=1)
        return "SKIPPED"
    log(f"  arm 유효값: {arm_eff['effective']}"
        + (f" 밴드={run.get('standing_band_kbit')}kbit(c1 상시)"
           if run.get("standing_band_kbit") else ""))
    band_warn = band_margin_check(run, rundir)
    thermal_snapshot(rundir)                # [작업 B2 §2.2] 드리프트 계측

    log("  reserve 초기화")
    out("bash /home/user/exp/reserve_reset.sh", 300)
    set_policy(run["policy"])
    if run["policy"] == "sorts_reactive":
        log("  SORTS 컨트롤러 기동 (.43)")
        sorts_start(rid)

    # Phase 4: server 축 상시 밴드 (c1 만, c2 무제한). 교란이 아니라 런 전체의
    # 상시 조건이므로 워밍업 전에 걸고 종료 시 해제한다. 마크는 d43 계산 후
    # marks 에 phase="other" 로 합류시킨다 (build_sections 는 start/end 만 본다).
    standing_mark = None
    sb = run.get("standing_band_kbit")
    if sb:
        # [작업 B3] standing_band_both: 양 코호트 동일 상시 밴드 —
        # C_eff(S1, band) 측정이 검증 조건(both 창)과 같은 밴드 상태를 쓴다.
        both = bool(run.get("standing_band_both", False))
        spec2 = f"rate {int(sb)}kbit" if both else "none"
        t_i = time.time()
        radio(f"rate {int(sb)}kbit", spec2, ips)
        t_d = time.time()
        standing_mark = {"what": "standing_band", "phase": "other",
                         "spec": f"rate {int(sb)}kbit "
                                 + ("c1+c2" if both else "c1"),
                         "t_issue": t_i,
                         "t_done": t_d, "apply_lat_s": round(t_d - t_i, 3)}
        log(f"  상시 밴드 적용: {'c1+c2' if both else 'c1'} "
            f"rate {int(sb)}kbit")

    off0 = int(out(f"ssh {ENVOY} 'stat -c %s {ENVOY_LOG}'", 60) or 0)

    # [작업 C] 소스 포트 고정 — I-8 버킷 충돌 제거. 런마다 64의 배수로 회전해
    # TIME_WAIT 을 피하되, 64 배수라 하위 6비트(=버킷)는 보존된다.
    port_base = port_base_for_run()
    log(f"  소스 포트 고정 base={port_base} "
        f"(커넥션 {conns}개 -> 서로 다른 버킷 {conns}칸)")

    procs, logs = [], []
    for c in (1, 2):
        lf = os.path.join(rundir, f"loadgen_c{c}.log")
        cmd = (f'ssh {LOADGEN} "{PIN} python3 ~/tb-load.py --host {ENVOY} --port 8080 '
               f"--cohort {c} --mix '{mix}' --connections {conns} "
               f'--rps-per-connection {rps_per} --warmup {warmup} --duration {duration} '
               f'--port-base {port_base} '
               f'--csv /var/tmp/dexp_{rid}_c{c}.csv --label {rid}-c{c}"')
        procs.append(subprocess.Popen(cmd, shell=True, stdout=open(lf, "w"),
                                      stderr=subprocess.STDOUT))
        logs.append(lf)

    # tb-load 가 찍는 start=<epoch> 를 읽어 본측정 시작을 정확히 잡는다.
    t_start = None
    for _ in range(200):
        try:
            for line in open(logs[0]):
                if line.startswith("start="):
                    t_start = float(line.split()[0].split("=")[1])
                    break
        except OSError:
            pass
        if t_start:
            break
        time.sleep(0.1)
    if t_start is None:
        t_start = time.time() + 1.0
        log("  경고: start= 를 못 읽어 추정값 사용")
    # t_start 는 tb-load 가 찍은 값이므로 **.12 시계**다. 마크는 러너(.40)
    # 시계로 찍히므로 절단 전에 두 시계를 맞춰야 한다.
    d12, rtt12 = host_clock_offset(LOADGEN)
    d43, rtt43 = host_clock_offset(ENVOY)
    t_meas = t_start + warmup            # .12 시계
    log(f"  부하 기동 t_start={t_start:.3f} 본측정={t_meas:.3f} "
        f"(워밍업 {warmup}s + 본측정 {duration}s) "
        f"clock d12={d12 * 1000:+.0f}ms d43={d43 * 1000:+.0f}ms")

    marks = []
    if standing_mark:
        standing_mark["t43_done"] = standing_mark["t_done"] + d43
        marks.append(standing_mark)

    def mark(what, phase, spec, fn):
        """교란 조작의 '지시 시각'과 '적용 완료 시각'을 모두 남긴다.

        ssh+tc 가 즉시가 아니라 두 시각이 다르다. 절단은 보수적으로
        적용완료+GUARD ~ 해제지시-GUARD 를 during 으로 본다.
        """
        t_i = time.time()
        fn()
        t_d = time.time()
        marks.append({"what": what, "phase": phase, "spec": spec,
                      "t_issue": t_i, "t_done": t_d, "t43_done": t_d + d43,
                      "apply_lat_s": round(t_d - t_i, 3)})

    def wait_until(rel):
        # t_meas 는 .12 시계이므로 로컬 시각을 .12 로 옮겨서 비교한다.
        while time.time() + d12 < t_meas + rel:
            time.sleep(0.2)

    dist = run["disturb"]
    probe_bad = []          # [작업 C] 버킷 실측 결과 (교란 창 중 수집)
    if dist == "none":
        # [작업 B §5.1] 부하 스파이크 트리거: c1 에 추가 tb-load 를 잠깐 얹어
        # 초기 요동(관측 f_c 스파이크)을 결정적으로 만든다. 시각·크기 고정.
        # phase="other" 라 절단 창(during=전체)에는 영향 없다.
        sp = run.get("spike_at")
        if sp is not None:
            s_rps = float(run.get("spike_rps", 400))
            s_dur = float(run.get("spike_dur", 4))
            s_conns = int(run.get("spike_conns", 16))
            s_base = port_base + 64          # 본부하(base..base+conns-1)와 분리
            s_cmd = (f'ssh {LOADGEN} "{PIN} python3 ~/tb-load.py --host {ENVOY} '
                     f"--port 8080 --cohort 1 --mix '{mix}' "
                     f'--connections {s_conns} '
                     f'--rps-per-connection {s_rps / s_conns} --warmup 0 '
                     f'--duration {s_dur} --port-base {s_base} '
                     f'--csv /var/tmp/dexp_{rid}_spike.csv --label {rid}-spike"')
            wait_until(float(sp))
            mark("spike", "other", f"c1 +{s_rps:g}rps x {s_dur:g}s",
                 lambda: subprocess.run(s_cmd, shell=True, timeout=s_dur + 120))
            log(f"  t+{sp}s 부하 스파이크 c1 +{s_rps:g}rps {s_dur:g}s (동기 완료)")
    elif dist == "radio":
        # [야간배치 20260810] band_spec 으로 밴드를 런 단위로 바꿀 수 있다.
        # 기본은 종전 poor(2300kbit) — 기존 manifest 거동 불변.
        bspec = run.get("band_spec", BANDS["poor"])
        wait_until(run["disturb_start"])
        mark("radio_on", "start", bspec,
             lambda: radio(bspec, "none", ips))
        log(f"  t+{run['disturb_start']}s 무선 교란 주입 (코호트1 Poor)")
        wait_until((run["disturb_start"] + run["disturb_end"]) / 2)
        probe_bad += probe_buckets(rundir, "during", {1}, conns, port_base)
        wait_until(run["disturb_end"])
        mark("radio_off", "end", None, lambda: radio(None, None, ips))
        log(f"  t+{run['disturb_end']}s 무선 교란 해제")
    elif dist == "seq_extreme":
        # v12 STEP B: 두 코호트 순차 열화. 코호트1 먼저 극단(1600kbit), 잠시 뒤
        # 코호트2 도 극단 -> 두 코호트가 동시에 엣지를 필요로 하는 상태를 만든다.
        # tb-radio2.sh 는 apply 가 전체 재구축이라 c2 추가 시 c1 spec 을 다시 준다.
        bspec = run.get("band_spec", "rate 1600kbit")
        wait_until(run["c1_start"])
        mark("c1_extreme", "start", bspec,
             lambda: radio(bspec, "none", ips))
        log(f"  t+{run['c1_start']}s 코호트1 극단 밴드")
        wait_until((run["c1_start"] + run["c2_start"]) / 2)
        probe_bad += probe_buckets(rundir, "c1only", {1}, conns, port_base)
        wait_until(run["c2_start"])
        mark("c2_extreme", "other", f"{bspec} x2",
             lambda: radio(bspec, bspec, ips))
        log(f"  t+{run['c2_start']}s 코호트2 극단 밴드 (둘 다)")
        wait_until((run["c2_start"] + run["disturb_end"]) / 2)
        probe_bad += probe_buckets(rundir, "both", {1, 2}, conns, port_base)
        wait_until(run["disturb_end"])
        mark("clear_all", "end", None, lambda: radio(None, None, ips))
        log(f"  t+{run['disturb_end']}s 전체 해제")
    elif dist == "server":
        wait_until(run["disturb_start"])
        mark("stress_on", "start", "tb-stress.sh start", lambda: stress(True))
        log(f"  t+{run['disturb_start']}s 서버 교란 주입 (S3 stress-ng)")
        wait_until(run["disturb_end"])
        mark("stress_off", "end", None, lambda: stress(False))
        log(f"  t+{run['disturb_end']}s 서버 교란 해제")
    elif dist == "ramp":
        # 본측정은 정상 밴드(20 Mbps)에서 시작한다 (§4.1 B "t=60 본측정 시작, 20 Mbps").
        wait_until(0.0)
        mark("ramp_base", "other", f"rate {RAMP_HI}kbit",
             lambda: radio(f"rate {RAMP_HI}kbit", "none", ips))
        t0 = run["ramp_start"]
        step = run["ramp_step_s"]
        rates = [round(RAMP_HI - i * (RAMP_HI - RAMP_LO) / (RAMP_STEPS - 1))
                 for i in range(RAMP_STEPS)]
        for i, kb in enumerate(rates):
            wait_until(t0 + i * step)
            # 램프의 during 은 첫 단계부터 해제까지다.
            mark(f"ramp_{i}", "start" if i == 0 else "other", f"rate {kb}kbit",
                 lambda kb=kb: radio(f"rate {kb}kbit", "none", ips))
            log(f"  t+{t0 + i * step}s 램프 {i + 1}/{RAMP_STEPS} -> {kb} kbit")
        wait_until(run["ramp_clear"])
        mark("ramp_clear", "end", None, lambda: radio(None, None, ips))
        log(f"  t+{run['ramp_clear']}s 램프 해제")
    else:
        raise ValueError(f"알 수 없는 교란 {dist}")

    # 부하 종료 대기. 포화 정책(bl_loc 는 S1 에 799 rps 를 실어 무릎 400 의
    # 2배다)에서는 tb-load 가 스케줄을 다 소화하느라 duration 을 크게 넘긴다.
    # 축소판 실측: 80s 스케줄이 167s 걸렸다(달성 39%). 예산을 달성률에서
    # 역산해 넉넉히 잡고, 그래도 넘기면 원격까지 확실히 회수한 뒤 실패시킨다.
    budget = warmup + duration + run.get("drain_budget", DRAIN_BUDGET_S)
    try:
        for p in procs:
            p.wait(timeout=budget)
    except subprocess.TimeoutExpired:
        log(f"  경고: 부하 종료 예산 {budget}s 초과 -> 로컬·원격 강제 회수")
        for p in procs:
            if p.poll() is None:
                p.kill()
        kill_loadgens()
        raise RuntimeError(f"부하 생성기가 예산 {budget}s 안에 끝나지 않음")
    log("  부하 종료")

    if dist == "server":
        stress(False)
    # 상시 밴드(Phase 4)든 무선 교란이든 ogstun 셰이핑은 항상 여기서 걷는다.
    radio(None, None, ips)

    if run["policy"] == "sorts_reactive":
        # 부하 종료 후에 정지한다 — post 구간의 복귀 결정까지 decisions 에 남긴다
        sorts_stop()
        sh(f"scp -q {ENVOY}:/var/tmp/decisions_{rid}.csv "
           f"{os.path.join(rundir, 'decisions.csv')}", 120)
        # Phase 2: 관측 상태 로그 (9행/s, decisions 와 ts 조인). 컨트롤러가
        # decisions_X.csv 옆에 obs_state_X.csv 로 쓴다 (sorts_ctl.obs_state_path).
        sh(f"scp -q {ENVOY}:/var/tmp/obs_state_{rid}.csv "
           f"{os.path.join(rundir, 'obs_state.csv')}", 120)
        sh(f"scp -q {ENVOY}:/var/tmp/sorts_{rid}.log "
           f"{os.path.join(rundir, 'sorts_ctl.log')}", 120)
        sh(f"ssh {ENVOY} 'rm -f /var/tmp/decisions_{rid}.csv "
           f"/var/tmp/obs_state_{rid}.csv /var/tmp/sorts_{rid}.log'", 60)
        log("  SORTS 컨트롤러 정지·decisions/obs_state 회수")

    off1 = int(out(f"ssh {ENVOY} 'stat -c %s {ENVOY_LOG}'", 60) or 0)
    slice_path = os.path.join(rundir, "envoy_access.log.gz")
    log(f"  Envoy 로그 슬라이스 {off1 - off0} 바이트")
    sh(f"ssh {ENVOY} \"tail -c +{off0 + 1} {ENVOY_LOG} | head -c {max(off1 - off0, 0)}\" "
       f"| gzip -1 > {slice_path}", 900)

    for c in (1, 2):
        sh(f"scp -q {LOADGEN}:/var/tmp/dexp_{rid}_c{c}.csv "
           f"{os.path.join(rundir, f'load_c{c}.csv')}", 900)
        sh(f"ssh {LOADGEN} 'rm -f /var/tmp/dexp_{rid}_c{c}.csv'", 60)
    if run.get("spike_at") is not None:
        sh(f"scp -q {LOADGEN}:/var/tmp/dexp_{rid}_spike.csv "
           f"{os.path.join(rundir, 'load_spike.csv')}", 120)
        sh(f"ssh {LOADGEN} 'rm -f /var/tmp/dexp_{rid}_spike.csv'", 60)

    # Phase 4 §2.3: postcheck — 어긋나면 이 런만 SUSPECT, 배치는 계속.
    post_bad = postcheck()
    # [작업 C] 버킷 실측 + 생성기 자체 경고를 postcheck 에 합류시킨다.
    post_bad += probe_bad
    for lf in logs:
        try:
            txt = open(lf).read()
        except OSError:
            continue
        if "PORT_BUCKET_WARN" in txt:
            post_bad.append(f"{os.path.basename(lf)}: 생성기 PORT_BUCKET_WARN "
                            f"— 소스 포트 고정이 깨졌다 (I-8 재발)")
    if post_bad:
        log(f"  postcheck 실패 -> SUSPECT: {post_bad}")
        json.dump({"run_id": rid, "reasons": post_bad, "ts": time.time()},
                  open(os.path.join(rundir, "SUSPECT"), "w"),
                  ensure_ascii=False, indent=1)

    # 개정 A §3.1: 절단은 벽시계 마크 기준. 구간은 .12 시계의 절대 시각이다.
    marks_doc = {
        "run_id": rid, "disturb": dist, "guard_s": GUARD_S,
        "clock": {"runner_host": "192.168.0.40",
                  "d12_s": d12, "d12_probe_rtt_s": rtt12,
                  "d43_s": d43, "d43_probe_rtt_s": rtt43,
                  "note": "d* = 해당 호스트 시계 − 러너(.40) 시계. "
                          "t_meas/end_ts 는 .12, t43_done 은 .43 시계."},
        "t_meas_12": t_meas, "duration": duration, "marks": marks,
    }
    json.dump(marks_doc, open(os.path.join(rundir, "marks.json"), "w"),
              ensure_ascii=False, indent=1)
    sections = build_sections(marks, t_meas, duration, d12,
                              float(run["disturb_start"]), float(run["disturb_end"]))
    json.dump({
        "run_id": rid, "policy": run["policy"], "disturb": dist,
        "t_start": t_start, "t_meas": t_meas, "warmup": warmup, "duration": duration,
        "connections": conns, "rps_per_connection": rps_per,
        "total_rps": run.get("total_rps"), "mix": mix,
        "band_spec": run.get("band_spec"),
        "cohort_map": {str(k): {"iface": v[0], "addr": v[1]} for k, v in ips.items()},
        "marks": marks, "clock": marks_doc["clock"],
        "envoy_log_bytes": [off0, off1],
        "sections_abs_12": {k: list(v) for k, v in sections.items()},
        "sections_rel_s": {k: [round(v[0] - t_meas, 3), round(v[1] - t_meas, 3)]
                           for k, v in sections.items()},
        "cut_basis": "end_ts (완료 벽시계, .12) vs marks.json ±GUARD",
        "host_clocks": "NTP synced (timedatectl NTPSynchronized=yes on all nodes)",
        # Phase 4: 사후 분석은 파일명이 아니라 이 arm 기록을 쓴다 (§2.2-5).
        "arm": arm_eff,
        "standing_band_kbit": run.get("standing_band_kbit"),
        "spike": ({"at": run["spike_at"], "rps": run.get("spike_rps", 400),
                   "dur": run.get("spike_dur", 4),
                   "conns": run.get("spike_conns", 16)}
                  if run.get("spike_at") is not None else None),
        "band_margin_warn": band_warn,
        "postcheck": {"ok": not post_bad, "reasons": post_bad},
        "port_base": port_base,
        "bucket_probe_ok": not probe_bad,
    }, open(os.path.join(rundir, "meta.json"), "w"), ensure_ascii=False, indent=1)

    log("  요약 계산")
    hm = load_hostmap(slice_path)
    res = summarize(rundir, run, t_meas, hm, sections)
    json.dump(res, open(os.path.join(rundir, "summary.json"), "w"),
              ensure_ascii=False, indent=1)
    p = res["sections"]["during"]
    log(f"  조인율={res['join_rate']}  during: 분배={p.get('site_share')} "
        f"달성={p.get('achieved_rps')}/s 이탈={p.get('byte_deviation_rate')}")
    open(os.path.join(rundir, "DONE"), "w").write(f"{time.time():.3f}\n")
    return "DONE_SUSPECT" if post_bad else "DONE"


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    man = json.load(open(args.manifest))
    os.makedirs(args.outdir, exist_ok=True)
    guard_snapshot(args.manifest, args.outdir)      # [작업 B2 §5]
    prog_path = os.path.join(args.outdir, "progress.json")
    runs = man["runs"]
    ips = cohort_ips()
    log(f"코호트 맵: {ips}")

    prog = {"batch_id": man.get("batch_id"), "manifest": args.manifest,
            "outdir": args.outdir, "n_runs": len(runs), "started_ts": time.time(),
            "current": None, "runs": {}}

    def flush():
        prog["heartbeat_ts"] = time.time()
        tmp = prog_path + ".tmp"
        json.dump(prog, open(tmp, "w"), ensure_ascii=False, indent=1)
        os.replace(tmp, prog_path)

    flush()
    for i, run in enumerate(runs, 1):
        rid = run["run_id"]
        rundir = os.path.join(args.outdir, rid)
        if args.resume and os.path.exists(os.path.join(rundir, "DONE")):
            log(f"[{i}/{len(runs)}] {rid} — DONE 마커 있음, 건너뜀")
            prog["runs"][rid] = {"status": "SKIPPED_RESUME"}
            flush()
            continue
        log(f"[{i}/{len(runs)}] {rid}  정책={run['policy']} 교란={run['disturb']}")
        prog["current"] = rid
        prog["runs"][rid] = {"status": "RUNNING", "start_ts": time.time()}
        flush()
        try:
            cleanup_all(ips)
            st = do_run(run, args.outdir, ips)
            prog["runs"][rid].update(status=st, end_ts=time.time())
        except Exception as e:
            log(f"  런 실패: {type(e).__name__}: {e}")
            prog["runs"][rid].update(status="FAILED", error=f"{type(e).__name__}: {e}",
                                     end_ts=time.time())
        finally:
            cleanup_all(ips)
            prog["current"] = None
            flush()
        if i < len(runs):
            rest = run.get("rest", 60)
            log(f"  휴지 {rest}s")
            time.sleep(rest)

    prog["finished_ts"] = time.time()
    flush()
    done = sum(1 for v in prog["runs"].values()
               if v["status"] in ("DONE", "DONE_SUSPECT", "SKIPPED_RESUME"))
    log(f"배치 종료: {done}/{len(runs)} 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())

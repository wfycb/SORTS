#!/usr/bin/env python3
"""SORTS 반응형 컨트롤러 (지시서 v10 STEP T3).

범위: 예측 없음. 히스테리시스 없음. 평활화 없음. 결정 로직만.
실행 위치: .43 (front Envoy 호스트). runtime_modify 가 로컬 호출이 되고
무선 상태 관측도 같은 호스트의 ogstun 에서 한다.

관측 (get_access_state):
    ogstun 의 tc 에 설정된 achievable rate 를 읽는다. tb-radio2.sh v2 는
    HTB leaf(무제한 분류 컨테이너) 마다 netem rate 를 매다므로, 실제 밴드는
    `tc qdisc show dev ogstun` 의 netem 행에 있다. classid 상위비트가
    코호트다 (1:1xxx = c1, 1:2xxx = c2).

    ★ 한계 (논문에 명시할 것):
    - 이것은 실측 처리량이 아니라 **설정된 rate** 다. 링크 이용률 rho≈0.2 라
      실측 처리량은 밴드와 무관하게 같아서(A-3) 관측으로 밴드를 구분할 수
      없고, gNB 가 CQI/MCS 로 보고하는 것도 이용량이 아니라 달성 가능
      대역이므로 이 선택이 모형상 옳다. 나중에 RAN 을 붙이면 이 함수
      안쪽만 교체한다.
    - 이 관측에는 측정 노이즈도 보고 지연도 없다. 현실에는 둘 다 있고
      그만큼 반응이 늦어진다. 이번 구현의 낙관적 요소다.
    - 관측 지연은 제어 주기 T_ctrl=1s 에서만 발생한다. 이 1초분이
      반응형의 잔여 위반이고, 예측이 메워야 할 몫이다.

결정: 매 주기, 코호트x클래스 6유닛 각각
    d_acc = bytes*8 / rate * overhead [ms]
    최원거리(S3)부터 slack = SLO - GB - d_net - f_c - d_acc > 0 인 첫 사이트.
    전부 음수면 S1 + EXPECTANT 로그.

자리만 남긴 것 (이번 범위 밖, v10 §4.3):
    - 용량 검사: 선택 사이트의 유입 합이 안정 용량을 넘는지 확인 후 강등.
      현재 도착률(총 800rps)에서는 어느 사이트도 포화하지 않아 생략.
    - 코호트 간 우선순위 (B_eff 오름차순): 용량이 모자랄 때 어느 코호트를
      먼저 배정할지. 같은 이유로 생략.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request

import yaml

import obs

# [작업 A] 클러스터/prefix 키 목록의 단일 출처 = gen_envoy_v10.py 가 config
# 렌더와 같은 실행에서 뱉는 envoy_keys.json (check_deploy 로 .43 에 배포).
# 여기 하드코딩하면 7-prefix 무시 사고(조용한 무시)가 재발한다.
_KEYS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "envoy_keys.json")
try:
    ENVOY_KEYS = json.load(open(_KEYS_PATH))
except OSError as e:
    raise SystemExit(f"envoy_keys.json 없음({e}) — gen_envoy_v10.py 렌더 산출물을 "
                     f"배포해야 한다 (run_all check_deploy 경로)")
CLUSTER_KEYS = ENVOY_KEYS["cluster_keys"]
SUBSET_CLUSTER_OF = ENVOY_KEYS["subset_cluster_of"]
SUBSET_POLICIES = ("strict_far", "far_tier", "all_feasible")


def fs_key(feasible) -> str:
    """허용 집합 -> 로그 표기 "S2|S3" (사이트명 정렬, 파이프 구분. 작업 A §4)."""
    return "|".join(sorted(feasible))


def cluster_of(feasible) -> str:
    """허용 집합 -> Envoy 클러스터 이름. 단일 원소는 기존 site_s*."""
    return SUBSET_CLUSTER_OF[fs_key(feasible)]


def chosen_site_compat(feasible) -> str:
    """하위 호환 chosen_site 열: 집합 크기 1 이면 그 원소, 2 이상이면 빈 값.

    "집합의 최원거리 원소" 재정의는 철회됐다 — far_tier 의 {S2}<->{S2,S3}
    전환이 기존 스크립트(p4_analyze 등)의 전환 지표에 S2<->S3 로 잘못
    집계되기 때문이다. 기존 스크립트가 무의미한 값을 세는 것보다 눈에 띄게
    비는 것이 낫다. 진동·전환 지표는 feasible_set 열을 읽는 별도 분석기
    (analysis/taskA/)로 계산한다."""
    return feasible[0] if len(feasible) == 1 else ""

NETEM_RE = re.compile(
    r"qdisc netem [0-9a-f]+: parent 1:([0-9a-f]+) .*?rate (\d+(?:\.\d+)?)([KMG]?)bit",
    re.IGNORECASE)
MULT = {"": 0.001, "K": 1.0, "M": 1000.0, "G": 1000000.0}   # -> kbit


def read_rates(iface: str) -> dict[int, float]:
    """classid 상위비트(0x1000/0x2000) -> 설정 rate [kbit]. 없으면 항목 없음.

    apply 가 전체 재구축이라 한 코호트에 64개 leaf 가 있고 전부 같은 rate 다.
    재구축 도중에 표본을 뜨면 일부만 보일 수 있으므로 최빈값을 쓴다.
    """
    try:
        out = subprocess.run(["tc", "qdisc", "show", "dev", iface],
                             capture_output=True, text=True, timeout=2).stdout
    except Exception:
        return {}
    seen: dict[int, dict[float, int]] = {}
    for m in NETEM_RE.finditer(out):
        cls = int(m.group(1), 16) & 0xF000
        kbit = float(m.group(2)) * MULT[m.group(3).upper()]
        d = seen.setdefault(cls, {})
        d[kbit] = d.get(kbit, 0) + 1
    return {cls: max(d, key=d.get) for cls, d in seen.items()}


class Controller:
    def __init__(self, cfg: dict, dry: bool = False):
        self.cfg = cfg
        self.dry = dry
        self.classes = list(cfg["slo_ms"])
        self.current: dict[tuple[str, str], str] = {}   # (cohort,class) -> 클러스터
        self.admin = cfg["envoy_admin"]
        # Phase 2 스위치. 둘 다 off = Phase 0 과 동일한 라이브 결정
        # (decisions.csv 앞 12열이 바이트 단위로 같다 — 회귀 테스트로 확인).
        self.est_bytes = bool(cfg.get("est_resp_bytes", False))
        self.est_fc = bool(cfg.get("est_f_c", False))
        # 작업 A §3.1. 기본 strict_far = 기존 단일 사이트 거동 (회귀 기준선).
        self.subset_policy = str(cfg.get("subset_policy", "strict_far"))
        if self.subset_policy not in SUBSET_POLICIES:
            raise SystemExit(f"subset_policy 미지값 {self.subset_policy!r} "
                             f"(허용: {SUBSET_POLICIES})")
        # [작업 B] 등가 부하 용량 제약. off(기본) = 작업 A 와 결정 단위 동일
        # (회귀 경계). L_eff = Σ w[class]·rps 는 측정 조성 볼록포 안에서만
        # 유효 (analysis/taskB-prep/weight_estimation.md — 외삽 시 브래킷
        # 2점 선측정 필수).
        self.capacity_check = bool(cfg.get("capacity_check", False))
        # HEADROOM 근거: 무릎이 ±20 rps 에서 f_c p95 2~5배로 폭주할 만큼
        # 날카롭다 (S1 search 240→25/260→49/280→118 ms,
        # analysis/night-20260810/capacity_knee.md). 0.9C 에서 멈추지 않으면
        # 예측 오차·관측 지연만으로 무릎을 넘는다. 매직 넘버 아님.
        self.headroom = float(cfg.get("headroom", 0.9))
        self.w_eq = dict(cfg.get("w_eq", {}))
        self.c_eq = dict(cfg.get("c_eq", {}))
        # 집합 내 LEAST_REQUEST 분배 근사 (실측 기반, sorts.yaml 주석 참조).
        # 키 없으면 균등 근사 — 그 사실을 1회 경고.
        self.set_share = {k: dict(v)
                          for k, v in (cfg.get("set_share") or {}).items()}
        # [작업 B2] EXPECTANT 한정 손실 배분 (사전 등록 runs/taskB2-20260810/
        # PROGRESS.md §A~B — 목적함수 (B) 위반 건수 최소화). off(기본) =
        # 작업 B 와 결정 단위 동일 (회귀 경계). capacity_check 가 꺼져 있으면
        # room/planned 의미가 없으므로 무효 처리한다.
        self.soft_assign = bool(cfg.get("soft_assign", False))
        if self.soft_assign and not self.capacity_check:
            print("soft_assign on 인데 capacity_check off — soft 무효", flush=True)
            self.soft_assign = False
        # [작업 B3] 밴드 의존 유효 용량 (T 실측 테이블, 사전 등록
        # runs/taskB3-20260810/PROGRESS.md §A~C). off(기본) = B2 와 결정
        # 단위 동일 (회귀 경계). 무제한(rate None) → c_eq (§B — 무제한
        # 회귀 요구 정합). 미측정 사이트는 밴드 무관 c_eq.
        self.c_eff_on = bool(cfg.get("c_eff", False))
        self.c_eff_band = {site: {int(k): float(v) for k, v in (tab or {}).items()}
                           for site, tab in (cfg.get("c_eff_band") or {}).items()}
        self._ceff_warned = set()
        self._share_warned = set()
        if self.capacity_check:
            for k in self.classes:
                if k not in self.w_eq:
                    raise SystemExit(f"capacity_check on 인데 w_eq[{k}] 없음")
            for s in cfg["site_order"]:
                if s not in self.c_eq:
                    raise SystemExit(f"capacity_check on 인데 c_eq[{s}] 없음")

    # ---------------------------------------------------------- [작업 B] 용량
    def shares_of(self, feasible) -> dict[str, float]:
        """집합 내 사이트별 유입 비중 근사. LEAST_REQUEST 는 등분하지 않는다
        (작업 A 실측: sub_s13 S1 64.6 %, sub_s123 S1 42.7 %) — 실측 기반
        테이블(sorts.yaml set_share)을 쓰고, 없으면 균등 근사 + 경고."""
        if len(feasible) == 1:
            return {feasible[0]: 1.0}
        key = fs_key(feasible)
        tab = self.set_share.get(key)
        if tab:
            tot = sum(tab.values())
            return {s: tab[s] / tot for s in feasible}
        if key not in self._share_warned:
            self._share_warned.add(key)
            print(f"set_share 에 {key} 없음 — 균등 근사 사용", flush=True)
        return {s: 1.0 / len(feasible) for s in feasible}

    def c_of(self, site: str, rate_kbit):
        """[작업 B3] 유닛 관측 밴드 기준 유효 용량. 반환 (C, band_used, extrap).

        off / 무제한(None) / 테이블 없는 사이트 → c_eq (extrap=False).
        테이블 키 정확 일치 → 그 값. 불일치 → **가장 가까운 측정점** 값
        + extrapolated 표기 + 경고 1회 (§3.1 — 조용한 외삽 금지)."""
        if not self.c_eff_on or rate_kbit is None:
            return self.c_eq[site], "", False
        tab = self.c_eff_band.get(site)
        if not tab:
            return self.c_eq[site], "", False
        b = int(rate_kbit)
        if b in tab:
            return tab[b], b, False
        near = min(tab, key=lambda k: abs(k - b))
        if (site, b) not in self._ceff_warned:
            self._ceff_warned.add((site, b))
            print(f"c_eff_band[{site}] 에 {b} 없음 — 최근접 {near} 사용 "
                  f"(extrapolated 기록)", flush=True)
        return tab[near], near, True

    def _cap_room(self, site: str, planned: dict, add: float,
                  cmap: dict | None = None) -> float:
        """여유 = HEADROOM·C − (계획 적재 + 추가분). 음수면 초과.
        [작업 B3] cmap 이 오면 그 사이트별 C(유효 용량)를 쓴다."""
        C = (cmap or self.c_eq)[site]
        return self.headroom * C - planned.get(site, 0.0) - add

    def capacity_filter(self, cand: tuple, planned: dict, r_u: float,
                        cmap: dict | None = None):
        """후보 집합에서 용량 초과 사이트를 반복 제거 (분배 비중은 집합
        크기에 따라 변하므로 제거할 때마다 재계산). 반환 (남은 집합, 제거 목록)."""
        F = list(cand)
        blocked = []
        while F:
            sh = self.shares_of(tuple(F))
            over = [(self._cap_room(s, planned, sh[s] * r_u, cmap), s) for s in F]
            worst = min(over)
            if worst[0] >= 0:
                break
            F.remove(worst[1])
            blocked.append(worst[1])
        return tuple(F), blocked

    def soft_alloc(self, slacks, planned, r_u, cmap=None):
        """[작업 B2] EXPECTANT 유닛의 손실 배분 (목적함수 B, 사전 등록 §B).

        badness = max(0, −slack) 오름차순(동률은 원거리 우선 — 안정 정렬이
        site_order 의 far-first 를 보존한다) 그리디로 사이트별 room =
        HEADROOM·C − planned 까지 충전. 전 room 소진 후 잔여(overflow)는
        엣지 — 기존 EXPECTANT 폴백과 같은 사이트라 "아무것도 안 하는 것"과의
        차이가 room 내 배정뿐이다.

        반환 (alloc: room 내 배정 {site: eq rps}, overflow, objective, pct).
        objective = Σ room 내 배정 중 slack≤0 몫 + overflow [등가 rps]
        — 이 tick 배정의 예측 위반 등가 부하 (S1 room 내 slack>0 몫은 무위반
        예측이라 제외). pct 는 weighted_clusters 용 정수 % (합 100)."""
        order = self.cfg["site_order"]
        edge = order[-1]
        cand = sorted(order, key=lambda s: max(0.0, -slacks[s]))
        alloc = {}
        remaining = r_u
        for s in cand:
            if remaining <= 1e-9:
                break
            room = max(0.0, self.headroom * (cmap or self.c_eq)[s]
                       - planned.get(s, 0.0))
            take = min(room, remaining)
            if take > 1e-9:
                alloc[s] = take
                remaining -= take
        overflow = max(0.0, remaining)
        objective = (sum(v for s, v in alloc.items() if slacks[s] <= 0.0)
                     + overflow)
        total = dict(alloc)
        if overflow > 1e-9:
            total[edge] = total.get(edge, 0.0) + overflow
        # 정수 % 합 100: 내림 후 잔여를 소수부 큰 순으로 배분
        fr = {s: 100.0 * v / r_u for s, v in total.items()}
        pct = {s: int(f) for s, f in fr.items()}
        for s, _ in sorted(fr.items(), key=lambda kv: -(kv[1] - int(kv[1]))):
            if sum(pct.values()) >= 100:
                break
            pct[s] += 1
        if pct and sum(pct.values()) != 100:      # 반올림 잔차 방어
            top = max(pct, key=pct.get)
            pct[top] += 100 - sum(pct.values())
        return alloc, overflow, objective, {s: p for s, p in pct.items() if p > 0}

    def apply_weights(self, cohort, klass, pct):
        """[작업 B2] 사이트 비중 적용 — 기존 weighted_clusters 런타임 키에
        정수 % 를 그대로 쓴다 (새 메커니즘 없음, 지시 §3.3-4). 반환: 지연 [ms]."""
        wmap = {SUBSET_CLUSTER_OF[s]: p for s, p in pct.items()}
        q = "&".join(f"routing.{cohort}_{klass}.{k}={wmap.get(k, 0)}"
                     for k in CLUSTER_KEYS)
        t0 = time.time()
        if not self.dry:
            req = urllib.request.Request(f"{self.admin}/runtime_modify?{q}",
                                         method="POST")
            with urllib.request.urlopen(req, timeout=2) as r:
                r.read()
        return (time.time() - t0) * 1000.0

    def decide_live(self, klass, rate_kbit, nb, fc_by_site, planned, r_u,
                    cmap=None):
        """라이브 결정 = decide() + (켜져 있으면) 용량 제약.

        반환 (feasible, slacks, d_acc, expectant, cap_blocked, blocked_by,
              cap_ok맵). off 이거나 r_u 미관측(0)이면 decide() 와 동일 결정
        — 회귀 경계. shadow 판본(const/bytesonly/fconly)은 용량을 보지
        않는다 (관측 귀속 의미론 유지).
        feasible 이 비면 기존 경로(엣지 EXPECTANT)를 탄다 (§3.3). 그때
        blocked_by 로 원인을 구분한다: slack / capacity / both.
        """
        feas, slacks, d_acc, exp = self.decide(klass, rate_kbit, nb, fc_by_site)
        order = self.cfg["site_order"]
        edge = order[-1]
        cap_ok = {s: (self._cap_room(s, planned, 0.0, cmap) >= 0) for s in order}
        if not self.capacity_check or r_u <= 0.0:
            return feas, slacks, d_acc, exp, [], "", cap_ok
        pol = self.subset_policy
        cap_blocked: list[str] = []
        if pol == "strict_far":
            chosen = None
            for site in order:
                if slacks[site] <= 0:
                    continue
                if self._cap_room(site, planned, r_u, cmap) >= 0:
                    chosen = (site,)
                    break
                cap_blocked.append(site)
            if chosen:
                feas, exp = chosen, False
            else:
                feas, exp = (edge,), True
        elif pol == "far_tier":
            far = tuple(s for s in order[:-1] if slacks[s] > 0)
            F, blk = self.capacity_filter(far, planned, r_u, cmap)
            cap_blocked += blk
            if F:
                feas, exp = F, False
            elif slacks[edge] > 0:
                if self._cap_room(edge, planned, r_u, cmap) >= 0:
                    feas, exp = (edge,), False
                else:
                    cap_blocked.append(edge)
                    feas, exp = (edge,), True
            else:
                feas, exp = (edge,), True
        else:  # all_feasible
            cand = tuple(s for s in order if slacks[s] > 0)
            F, blk = self.capacity_filter(cand, planned, r_u, cmap)
            cap_blocked += blk
            if F:
                feas, exp = F, False
            else:
                feas, exp = (edge,), True
        if exp:
            slack_removed = any(slacks[s] <= 0 for s in order)
            if cap_blocked and slack_removed:
                blocked_by = "both"
            elif cap_blocked:
                blocked_by = "capacity"
            else:
                blocked_by = "slack"
        elif cap_blocked:
            blocked_by = "capacity"
        else:
            blocked_by = ""
        return feas, slacks, d_acc, exp, cap_blocked, blocked_by, cap_ok

    def get_access_state(self, cohort: str, rates: dict[int, float]) -> float | None:
        """코호트의 achievable rate [kbit]. 셰이핑 없으면 None(무제한)."""
        return rates.get(int(self.cfg["cohorts"][cohort]))

    def decide(self, klass: str, rate_kbit: float | None,
               nb: float | None = None,
               fc_by_site: dict | None = None) -> tuple[tuple, dict, float, bool]:
        """(허용 집합 tuple, slack맵, d_acc, expectant).

        [작업 A] 출력이 단일 사이트에서 허용 집합으로 바뀌었다. 집합 내
        분배는 Envoy LEAST_REQUEST(P2C) 에 위임한다 — SORTS 는 LB 를
        대체하지 않고 제약한다. 집합 표기는 fs_key(), 클러스터 매핑은
        cluster_of() 를 쓴다.

        nb/fc_by_site 를 안 주면 프라이어(sorts.yaml)를 쓴다 — Phase 0 거동.
        관측판/shadow 판본은 호출자가 값을 주입한다. 결정 규칙은
        subset_policy 하나로만 갈라진다 (§3.1).
        """
        c = self.cfg
        if nb is None:
            nb = c["resp_bytes"][klass]
        if fc_by_site is None:
            fc_by_site = c["f_c_ms"][klass]
        if rate_kbit is None:
            d_acc = 0.0
        else:
            d_acc = nb * 8.0 / rate_kbit * c["overhead"]
        order = c["site_order"]               # [S3, S2, S1] — d_net 내림차순
        edge = order[-1]                      # S1. 엣지 보존의 대상.
        slacks = {site: (c["slo_ms"][klass] - c["gb_ms"] - c["d_net_ms"][site]
                         - fc_by_site[site] - d_acc)
                  for site in order}
        pol = self.subset_policy
        if pol == "strict_far":
            # 가용한 가장 먼 사이트 하나 — 현행(Phase 0~4)과 동등. 회귀 기준선.
            for site in order:
                if slacks[site] > 0:
                    return (site,), slacks, d_acc, False
            return (edge,), slacks, d_acc, True
        if pol == "far_tier":
            # 원거리 티어(엣지 제외) 중 가용 전부. 엣지는 원거리 전멸 시만 —
            # 엣지 보존(평시 S1 유입 0)이 유지되고, 전환이 {S2}<->{S2,S3} 로
            # 바뀌어 bang-bang 이 비례제어가 된다.
            # far-first 는 순회 순서가 아니라 티어 구분으로 표현된다 —
            # 용량 기반 표현(작업 B)으로 가는 중간 단계다.
            far = tuple(s for s in order[:-1] if slacks[s] > 0)
            if far:
                return far, slacks, d_acc, False
            if slacks[edge] > 0:
                return (edge,), slacks, d_acc, False
            return (edge,), slacks, d_acc, True
        # all_feasible — 가용 전부. 엣지 보존 없음 (ablation 대조군 전용:
        # S1 이 거의 항상 가용이라 엣지가 상시 1/N 을 받게 된다).
        # [자리] 용량 검사·코호트 우선순위(B_eff 오름차순)는 작업 B 범위.
        feas = tuple(s for s in order if slacks[s] > 0)
        if feas:
            return feas, slacks, d_acc, False
        return (edge,), slacks, d_acc, True

    def apply(self, cohort: str, klass: str, cluster: str) -> float:
        """runtime_modify. cluster 에 100, 나머지 키 전부 0. 반환: 적용 지연 [ms]."""
        if cluster not in CLUSTER_KEYS:
            raise ValueError(f"미지 클러스터 {cluster!r} (envoy_keys.json 확인)")
        q = "&".join(f"routing.{cohort}_{klass}.{k}={100 if k == cluster else 0}"
                     for k in CLUSTER_KEYS)
        t0 = time.time()
        if not self.dry:
            req = urllib.request.Request(f"{self.admin}/runtime_modify?{q}",
                                         method="POST")
            with urllib.request.urlopen(req, timeout=2) as r:
                r.read()
        return (time.time() - t0) * 1000.0


def obs_state_path(out_csv: str) -> str:
    """decisions_X.csv -> obs_state_X.csv (같은 디렉터리). run_all 회수 규약."""
    import os
    d, b = os.path.split(out_csv)
    nb = (b.replace("decisions", "obs_state", 1) if b.startswith("decisions")
          else b + ".obs_state.csv")
    return os.path.join(d, nb)


# decisions.csv 스키마: 기존 12열은 Phase 0 과 동일(회귀 경계), 뒤에만 붙인다.
# obs_ctl_regress.py 가 앞 12열을 인덱스 슬라이스로 비교한다 — 순서 불변.
# [작업 A] chosen_site* 열은 집합 크기 1 일 때만 채워진다 (chosen_site_compat).
DEC_HEADER = ["ts", "cohort", "class", "observed_rate_kbit", "d_acc_ms",
              "slack_s1", "slack_s2", "slack_s3", "chosen_site",
              "changed", "apply_latency_ms", "expectant",
              # ---- Phase 2 추가 (관측 + shadow 4방향 귀속) ----
              "resp_bytes_est", "resp_bytes_src", "resp_bytes_n",
              "slack_const_s1", "slack_const_s2", "slack_const_s3",
              "chosen_site_const",
              "slack_bytesonly_s1", "slack_bytesonly_s2", "slack_bytesonly_s3",
              "chosen_site_bytesonly",
              "slack_fconly_s1", "slack_fconly_s2", "slack_fconly_s3",
              "chosen_site_fconly",
              "obs_update_ms", "backlog_bytes",
              # ---- 작업 A 추가 (허용 집합. "S2|S3" 정렬·파이프 구분) ----
              "feasible_set", "subset_cluster",
              "feasible_set_const", "feasible_set_bytesonly",
              "feasible_set_fconly",
              # ---- 작업 B 추가 (용량 제약. off 여도 관측/계획값은 기록) ----
              "unit_rate_rps", "l_eff_s1", "l_eff_s2", "l_eff_s3",
              "cap_ok_s1", "cap_ok_s2", "cap_ok_s3",
              "blocked_by", "cap_blocked_sites",
              # ---- 작업 B2 추가 (EXPECTANT 손실 배분. off 면 0/빈 값) ----
              "soft_applied", "carry_s1", "carry_s2", "carry_s3",
              "soft_overflow_eq", "soft_objective_eq", "soft_weights",
              # ---- 작업 B3 추가 (밴드 의존 유효 용량. off 면 c_eq/빈 값) ----
              "c_eff_s1", "band_used", "c_eff_extrapolated"]
# obs_state.csv: tick x site x class = 9행/s. decisions 와 ts 로 조인한다.
OBS_HEADER = ["ts", "site", "class", "n", "src", "fill_ratio",
              "mean_ms", "p95_ms", "stale_ms", "prior_ms", "last_obs_ms",
              "value_ms"]


def run(cfg_path: str, out_csv: str):
    cfg = yaml.safe_load(open(cfg_path))
    ctl = Controller(cfg)
    # 관측 계층. 스위치가 둘 다 꺼져 있어도 shadow 귀속(4방향)과 obs_state
    # 로그를 위해 항상 돌린다. 라이브 결정에 관측이 들어가는지는 스위치가
    # 정한다.
    ob = obs.init(cfg, log_path=cfg.get("obs_log_path", obs.DEFAULT_LOG_PATH),
                  window_s=cfg.get("window_s", obs.DEFAULT_WINDOW_S))
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))

    f = open(out_csv, "w", newline="", buffering=1)
    w = csv.writer(f)
    w.writerow(DEC_HEADER)
    fo = open(obs_state_path(out_csv), "w", newline="", buffering=1)
    wo = csv.writer(fo)
    wo.writerow(OBS_HEADER)
    print(f"sorts_ctl 시작 T_ctrl={cfg['t_ctrl_s']}s est_resp_bytes={ctl.est_bytes} "
          f"est_f_c={ctl.est_fc} subset_policy={ctl.subset_policy} -> {out_csv}",
          flush=True)

    period = float(cfg["t_ctrl_s"])
    next_at = time.time()
    while not stop:
        dt_obs = obs.update()
        rates = read_rates(cfg["iface"])
        now = time.time()

        # obs_state: tick 당 9행 (사이트x클래스)
        for (s, c), e in sorted(ob._cache_fc.items()):
            wo.writerow([f"{now:.3f}", s, c, e["n"], e["src"],
                         round(e["fill"], 4),
                         "" if e["mean"] is None else round(e["mean"], 4),
                         "" if e["p95"] is None else round(e["p95"], 4),
                         "" if e["stale_ms"] is None else round(e["stale_ms"], 1),
                         round(e["prior"], 4),
                         "" if e["last_obs"] is None else round(e["last_obs"], 4),
                         round(e["value"], 4)])

        # [작업 B] 유닛(코호트x클래스) 도착률. 코호트 IP 매핑: 관측된 XFF
        # 주소를 오름차순 정렬해 코호트 순서(c1, c2)에 대응시킨다 —
        # UE 는 순차 기동이라 c1 이 항상 낮은 주소를 받는 것이 현행 관례
        # (tb-cohort.map 이력: .2/.3, .6/.7). 라벨이 뒤집혀도 검증 조건은
        # 코호트 대칭이라 L_eff 합이 변하지 않는다 (보고서 명시).
        unit_rates = ob.get_unit_rates()
        ips = sorted({ip for (ip, _k) in unit_rates})
        cohort_ip = {c: (ips[i] if i < len(ips) else None)
                     for i, c in enumerate(sorted(cfg["cohorts"]))}
        # 이 tick 의 계획 적재 [search-등가 rps]. 유닛을 고정 순서로 처리하며
        # 자기 결정의 예측 부하를 누적한다 — 관측 되먹임 없는 (b) 설계.
        planned = {s: 0.0 for s in cfg["site_order"]}
        for cohort in sorted(cfg["cohorts"]):
            rate = ctl.get_access_state(cohort, rates)
            for klass in ctl.classes:
                nb_p = float(cfg["resp_bytes"][klass])
                fc_p = cfg["f_c_ms"][klass]
                nb_o = ob.get_resp_size(klass)
                fc_o = {s: ob.get_site_state(s, klass) for s in obs.SITES}
                eb = ob._cache_bytes[klass]

                # 라이브 결정 = 스위치 구성. 둘 다 off 면 const 와 동일.
                # [작업 B] 용량 검사 입력은 등가 부하 r_eq = w[class]·rps.
                raw_rps = unit_rates.get((cohort_ip.get(cohort), klass), 0.0)
                r_eq = ctl.w_eq.get(klass, 0.0) * raw_rps
                # [작업 B3] 유닛 관측 밴드 기준 유효 용량 (off 면 c_eq 그대로)
                cinfo = {s: ctl.c_of(s, rate) for s in cfg["site_order"]}
                cmap = {s: cinfo[s][0] for s in cfg["site_order"]}
                feas, slacks, d_acc, exp, cap_blk, blocked_by, cap_ok = \
                    ctl.decide_live(
                        klass, rate,
                        nb=nb_o if ctl.est_bytes else nb_p,
                        fc_by_site=fc_o if ctl.est_fc else fc_p,
                        planned=planned, r_u=r_eq, cmap=cmap)
                # [작업 B2] EXPECTANT 한정 손실 배분. off 면 아래 분기 전체가
                # 죽어 기존 경로 그대로다 (회귀 경계 — soft_* 열은 0/빈 값).
                soft_applied = 0
                s_alloc, s_over, s_obj, s_pct = {}, 0.0, 0.0, {}
                if exp and ctl.soft_assign and r_eq > 0.0:
                    s_alloc, s_over, s_obj, s_pct = ctl.soft_alloc(
                        slacks, planned, r_eq, cmap)
                    soft_applied = 1
                # 계획 적재 누적 — on/off 무관하게 기록용으로 항상 한다.
                if soft_applied:
                    edge_site = cfg["site_order"][-1]
                    for s, v in s_alloc.items():
                        planned[s] += v
                    if s_over > 0.0:
                        planned[edge_site] += s_over
                else:
                    for s, sh in ctl.shares_of(feas).items():
                        planned[s] += sh * r_eq
                # shadow 4방향 귀속: "동일 관측 상태에서 각 판본이 내렸을 판정"
                # 이지 "그 판본이 했을 일"이 아니다 — 폐루프에서 궤적은 갈린다.
                # 비교는 집합 동일성(feasible_set_* 열)으로 한다.
                f_const, sl_c, _, _ = ctl.decide(klass, rate, nb_p, fc_p)
                f_bo, sl_b, _, _ = ctl.decide(klass, rate, nb_o, fc_p)
                f_fo, sl_f, _, _ = ctl.decide(klass, rate, nb_p, fc_o)

                if soft_applied:
                    # 비중 상태 문자열이 결정 상태 — 같으면 재적용하지 않는다.
                    # 정상 경로 복귀 시 cluster 명과 달라 자동으로 재적용된다.
                    cluster = "W:" + "|".join(
                        f"{s}:{s_pct[s]}" for s in sorted(s_pct))
                    log_feas = tuple(sorted(s_pct))
                else:
                    cluster = cluster_of(feas)
                    log_feas = feas
                prev = ctl.current.get((cohort, klass))
                changed = cluster != prev
                lat = ""
                if changed:
                    try:
                        lat = round(ctl.apply_weights(cohort, klass, s_pct)
                                    if soft_applied
                                    else ctl.apply(cohort, klass, cluster), 2)
                        ctl.current[(cohort, klass)] = cluster
                    except Exception as e:
                        print(f"apply 실패 {cohort}/{klass}: {e}", flush=True)
                        changed = False
                w.writerow([f"{now:.3f}", cohort, klass,
                            "" if rate is None else int(rate),
                            round(d_acc, 3),
                            round(slacks["S1"], 2), round(slacks["S2"], 2),
                            round(slacks["S3"], 2), chosen_site_compat(log_feas),
                            int(changed), lat,
                            int(exp),
                            round(nb_o, 2), eb["src"], eb["n"],
                            round(sl_c["S1"], 2), round(sl_c["S2"], 2),
                            round(sl_c["S3"], 2), chosen_site_compat(f_const),
                            round(sl_b["S1"], 2), round(sl_b["S2"], 2),
                            round(sl_b["S3"], 2), chosen_site_compat(f_bo),
                            round(sl_f["S1"], 2), round(sl_f["S2"], 2),
                            round(sl_f["S3"], 2), chosen_site_compat(f_fo),
                            round(dt_obs, 3), ob.backlog_bytes,
                            fs_key(log_feas), cluster,
                            fs_key(f_const), fs_key(f_bo), fs_key(f_fo),
                            round(raw_rps, 1),
                            round(planned["S1"], 1), round(planned["S2"], 1),
                            round(planned["S3"], 1),
                            int(cap_ok["S1"]), int(cap_ok["S2"]),
                            int(cap_ok["S3"]),
                            blocked_by, "|".join(cap_blk),
                            soft_applied,
                            round(s_alloc.get("S1", 0.0), 1),
                            round(s_alloc.get("S2", 0.0), 1),
                            round(s_alloc.get("S3", 0.0), 1),
                            round(s_over, 1), round(s_obj, 1),
                            "|".join(f"{s}:{s_pct[s]}"
                                     for s in sorted(s_pct)),
                            round(cmap["S1"], 1), cinfo["S1"][1],
                            int(cinfo["S1"][2])])
        next_at += period
        dt = next_at - time.time()
        if dt > 0:
            time.sleep(dt)
        else:
            next_at = time.time()
    f.close()
    fo.close()
    print("sorts_ctl 종료", flush=True)


def selftest(cfg_path: str) -> int:
    """단위 검증 (v10 §4.6 + 작업 A §3.1): rate -> 기대 허용 집합.

    세 정책 전부 검사한다. cfg 의 subset_policy 와 무관하게 정책별
    Controller 를 따로 만든다 (렌더 상태에 좌우되지 않게). Envoy 호출 없음.
    """
    base = yaml.safe_load(open(cfg_path))
    # (rate_kbit, class) -> 정책별 기대 집합. §0.1 계단의 집합 확장.
    # search: d_acc(kbit)=4474*8/rate*1.1 -> 20000:1.97 / 4500:8.75 /
    #         2300:17.12 / 1600:24.61 ms. reserve/recommend 는 d_acc 미미.
    E = {}
    for kb in (20000, 4500, 2300, 1600):
        for k in ("reserve", "recommend"):
            E[(kb, k)] = {"strict_far": "S3", "far_tier": "S2|S3",
                          "all_feasible": "S1|S2|S3"}
    E[(20000, "search")] = {"strict_far": "S3", "far_tier": "S2|S3",
                            "all_feasible": "S1|S2|S3"}
    E[(4500, "search")] = {"strict_far": "S3", "far_tier": "S2|S3",
                           "all_feasible": "S1|S2|S3"}
    E[(2300, "search")] = {"strict_far": "S2", "far_tier": "S2",
                           "all_feasible": "S1|S2"}
    E[(1600, "search")] = {"strict_far": "S1", "far_tier": "S1",
                           "all_feasible": "S1"}
    bad = 0
    for pol in SUBSET_POLICIES:
        cfg = dict(base)
        cfg["subset_policy"] = pol
        ctl = Controller(cfg, dry=True)
        print(f"--- subset_policy={pol} ---")
        print(f"{'rate':>7s} {'class':10s} {'d_acc':>7s} "
              f"{'slack_S3':>9s} {'slack_S2':>9s} {'slack_S1':>9s} "
              f"{'집합':>9s} {'클러스터':>9s} {'기대':>9s}")
        for kb in (20000, 4500, 2300, 1600):
            for k in ("reserve", "search", "recommend"):
                feas, sl, d_acc, exp = ctl.decide(k, float(kb))
                got, want = fs_key(feas), E[(kb, k)][pol]
                mark = "" if got == want else "  ★불일치"
                bad += got != want
                print(f"{kb:7d} {k:10s} {d_acc:7.3f} {sl['S3']:9.2f} "
                      f"{sl['S2']:9.2f} {sl['S1']:9.2f} {got:>9s} "
                      f"{cluster_of(feas):>9s} {want:>9s}{mark}")
        # 셰이핑 없음(fail-open): d_acc=0
        feas, _, _, _ = ctl.decide("search", None)
        want = {"strict_far": "S3", "far_tier": "S2|S3",
                "all_feasible": "S1|S2|S3"}[pol]
        got = fs_key(feas)
        print(f"{'없음':>7s} {'search':10s} {'0.000':>7s} {'':>29s} "
              f"{got:>9s} {cluster_of(feas):>9s} {want:>9s}"
              + ("" if got == want else "  ★불일치"))
        bad += got != want
    # 매핑 전수 검사: 정책이 낼 수 있는 모든 집합이 클러스터로 매핑되는가
    for key in ("S1", "S2", "S3", "S1|S2", "S1|S3", "S2|S3", "S1|S2|S3"):
        if key not in SUBSET_CLUSTER_OF:
            print(f"★ SUBSET_CLUSTER_OF 에 {key} 없음")
            bad += 1
    bad += selftest_capacity(base)
    bad += selftest_soft(base)
    bad += selftest_ceff(base)
    print("단위 검증:", "통과" if bad == 0 else f"실패 {bad}건")
    return 1 if bad else 0


def selftest_ceff(base) -> int:
    """[작업 B3] 밴드 의존 유효 용량 단위 검증 (사전 등록 §B~C). Envoy 없음.

    합성 테이블 c_eff_band[S1] = {1600: 105.4, 2300: 161.7}, c_eq[S1]=279."""
    cfg = dict(base)
    cfg.update(capacity_check=True, soft_assign=True, c_eff=True, headroom=0.9,
               w_eq={"search": 1.0, "reserve": 0.278, "recommend": 0.178},
               c_eq={"S1": 279.0, "S2": 515.0, "S3": 832.0},
               c_eff_band={"S1": {1600: 105.4, 2300: 161.7}})
    ctl = Controller(cfg, dry=True)
    bad = 0

    def case(name, site, rate, want_c, want_band, want_ex):
        nonlocal bad
        c, b, ex = ctl.c_of(site, rate)
        got, want = (round(c, 1), b, ex), (want_c, want_band, want_ex)
        ok = got == want
        bad += not ok
        print(f"ceff {name:32s} got={got} want={want}"
              + ("" if ok else "  ★불일치"))

    # 1) 무제한 → c_eq (§B — 무제한 회귀 정합)
    case("무제한->c_eq", "S1", None, 279.0, "", False)
    # 2) 측정 밴드 정확 일치
    case("1600 일치", "S1", 1600.0, 105.4, 1600, False)
    case("2300 일치", "S1", 2300.0, 161.7, 2300, False)
    # 3) 미측정 밴드 → 최근접 + extrapolated (조용한 외삽 금지)
    case("2000 최근접->2300", "S1", 2000.0, 161.7, 2300, True)
    case("1200 최근접->1600", "S1", 1200.0, 105.4, 1600, True)
    # 4) 미측정 사이트 → 밴드 무관 c_eq
    case("S2 미측정->c_eq", "S2", 1600.0, 515.0, "", False)
    # 5) 스위치 off → c_eq (B2 회귀 경계)
    cfg2 = dict(cfg)
    cfg2["c_eff"] = False
    ctl2 = Controller(cfg2, dry=True)
    c, b, ex = ctl2.c_of("S1", 1600.0)
    ok = (c, b, ex) == (279.0, "", False)
    print(f"ceff {'off->c_eq':32s} got={(c, b, ex)}"
          + ("" if ok else "  ★불일치"))
    bad += not ok
    # 6) room 이 C_eff 로 좁아지는지: 1600 밴드 search, planned S1=60 →
    #    c_eq room 191.1 vs C_eff room 34.9 < r_u 100 → S1 용량 차단.
    #    (1600 에서 slack 은 S1 만 양수 — selftest 계단 참조.)
    cmap = {s: ctl.c_of(s, 1600.0)[0] for s in ("S1", "S2", "S3")}
    feas, sl, _, exp, blk, bby, _ = ctl.decide_live(
        "search", 1600.0, None, None, {"S1": 60.0, "S2": 0.0, "S3": 0.0},
        100.0, cmap=cmap)
    ok = exp and "S1" in blk and bby == "both"
    print(f"ceff {'1600 room축소->차단':32s} got=({fs_key(feas)},{exp},{bby!r},"
          f"blk={blk})" + ("" if ok else "  ★불일치"))
    bad += not ok
    # 7) 같은 조건 cmap=c_eq(off 상당)면 S1 채택 (대조)
    feas, sl, _, exp, blk, bby, _ = ctl.decide_live(
        "search", 1600.0, None, None, {"S1": 60.0, "S2": 0.0, "S3": 0.0},
        100.0, cmap=None)
    ok = fs_key(feas) == "S1" and not exp and not blk
    print(f"ceff {'대조: c_eq room->S1':32s} got=({fs_key(feas)},{exp},{bby!r})"
          + ("" if ok else "  ★불일치"))
    bad += not ok
    # 8) soft room 도 C_eff 기준: 1600 밴드, S1 slack +6.49 만 양수,
    #    planned 0 → S1 94.9 채우고 잔여는 S2 (badness 3.89)
    alloc, over, obj, pct = ctl.soft_alloc(
        {"S1": 6.49, "S2": -3.89, "S3": -13.63},
        {"S1": 0.0, "S2": 0.0, "S3": 0.0}, 150.0, cmap)
    ok = (round(alloc.get("S1", 0), 1) == 94.9
          and round(alloc.get("S2", 0), 1) == 55.1 and over == 0.0)
    print(f"ceff {'soft room=C_eff':32s} got={{S1:{alloc.get('S1', 0):.1f},"
          f"S2:{alloc.get('S2', 0):.1f}}},of={over}"
          + ("" if ok else "  ★불일치"))
    bad += not ok
    return bad


def selftest_soft(base) -> int:
    """[작업 B2] EXPECTANT 손실 배분 단위 검증 (사전 등록 §B 규칙). Envoy 없음.

    합성 파라미터는 selftest_capacity 와 동일 (C = 279/515/832, headroom .9
    -> room 상한 251.1/463.5/748.8)."""
    cfg = dict(base)
    cfg.update(capacity_check=True, soft_assign=True, headroom=0.9,
               w_eq={"search": 1.0, "reserve": 0.278, "recommend": 0.178},
               c_eq={"S1": 279.0, "S2": 515.0, "S3": 832.0})
    ctl = Controller(cfg, dry=True)
    bad = 0

    def case(name, slacks, planned, r_u, want_alloc, want_over, want_obj):
        nonlocal bad
        alloc, over, obj, pct = ctl.soft_alloc(slacks, dict(planned), r_u)
        got = ({s: round(v, 1) for s, v in alloc.items()},
               round(over, 1), round(obj, 1))
        want = (want_alloc, want_over, want_obj)
        ok = got == want and (not pct or sum(pct.values()) == 100)
        bad += not ok
        print(f"soft {name:32s} got={got} pct={pct} want={want}"
              + ("" if ok else "  ★불일치"))

    # 1) S1 만 양슬랙 (room 51.1): S1 먼저, 잔여는 badness 최소 S2 로 이월
    case("S1양슬랙+S2이월", {"S1": 6.5, "S2": -3.9, "S3": -13.6},
         {"S1": 200.0, "S2": 0.0, "S3": 0.0}, 100.0,
         {"S1": 51.1, "S2": 48.9}, 0.0, 48.9)
    # 2) 전부 양슬랙: 원거리 우선 (S3 room 으로 전량, badness 동률 안정 정렬)
    case("전부양슬랙->원거리", {"S1": 10.0, "S2": 10.0, "S3": 10.0},
         {"S1": 0.0, "S2": 0.0, "S3": 0.0}, 100.0, {"S3": 100.0}, 0.0, 0.0)
    # 3) 전 room 소진 -> overflow 는 엣지 (기존 EXPECTANT 와 동일 사이트)
    case("room소진->엣지overflow", {"S1": -50.0, "S2": -3.9, "S3": -13.6},
         {"S1": 251.1, "S2": 463.5, "S3": 748.8}, 100.0, {}, 100.0, 100.0)
    # 4) badness 순서: S1 관측 오염(-50)이면 S2 가 먼저
    case("S1오염->S2우선", {"S1": -50.0, "S2": -3.9, "S3": -13.6},
         {"S1": 0.0, "S2": 400.0, "S3": 700.0}, 100.0,
         {"S2": 63.5, "S3": 36.5}, 0.0, 100.0)
    # 5) soft off 회귀 경계: cfg off 면 Controller 가 soft_assign=False
    cfg2 = dict(cfg)
    cfg2["soft_assign"] = False
    ok = not Controller(cfg2, dry=True).soft_assign
    print(f"soft {'off 스위치':32s} soft_assign={not ok!s:5s}"
          + ("" if ok else "  ★불일치"))
    bad += not ok
    # 6) capacity off 면 soft 무효
    cfg3 = dict(base)
    cfg3.update(capacity_check=False, soft_assign=True)
    ok = not Controller(cfg3, dry=True).soft_assign
    print(f"soft {'capacity off->무효':32s} soft_assign={not ok!s:5s}"
          + ("" if ok else "  ★불일치"))
    bad += not ok
    return bad


def selftest_capacity(base) -> int:
    """[작업 B] 용량 제약 단위 검증. Envoy 호출 없음.

    합성 파라미터: w=(1, .278, .178), C=(S1 279, S2 520, S3 832), headroom .9
    -> 상한 (251.1, 468, 748.8). 클린(rate=None, d_acc=0) search 로 검사.
    """
    cfg = dict(base)
    cfg.update(capacity_check=True, headroom=0.9,
               w_eq={"search": 1.0, "reserve": 0.278, "recommend": 0.178},
               c_eq={"S1": 279.0, "S2": 520.0, "S3": 832.0},
               set_share={"S2|S3": {"S2": 0.58, "S3": 0.42}})
    bad = 0

    def case(name, pol, planned, r_eq, want_set, want_exp, want_blocked):
        nonlocal bad
        cfg["subset_policy"] = pol
        ctl = Controller(cfg, dry=True)
        feas, sl, _, exp, blk, bby, _ = ctl.decide_live(
            "search", None, None, None, dict(planned), r_eq)
        got = (fs_key(feas), exp, bby)
        want = (want_set, want_exp, want_blocked)
        mark = "" if got == want else "  ★불일치"
        bad += got != want
        print(f"cap {name:34s} got={got} want={want}{mark}")

    # 1) strict: S3 용량 차단 -> S2 로 (blocked_by=capacity)
    case("strict S3막힘->S2", "strict_far", {"S1": 0, "S2": 0, "S3": 700},
         100.0, "S2", False, "capacity")
    # 2) strict: 여유 충분 -> S3 그대로, 표기 없음
    case("strict 여유->S3", "strict_far", {"S1": 0, "S2": 0, "S3": 0},
         100.0, "S3", False, "")
    # 3) strict: 전 사이트 용량 차단 -> 엣지 EXPECTANT (slack 은 전부 양수
    #    -> blocked_by=capacity)
    case("strict 전부막힘->EXPECT", "strict_far",
         {"S1": 250, "S2": 460, "S3": 745}, 100.0, "S1", True, "capacity")
    # 4) far_tier: S2 만 차단 -> {S3} (집합 축소)
    case("far S2막힘->{S3}", "far_tier", {"S1": 0, "S2": 440, "S3": 0},
         100.0, "S3", False, "capacity")
    # 5) far_tier: 티어 전부 차단, 엣지 여유 -> S1 비상개방(비 EXPECTANT)
    case("far 티어막힘->S1", "far_tier", {"S1": 0, "S2": 460, "S3": 745},
         100.0, "S1", False, "capacity")
    # 6) far_tier: 전부 차단 -> 엣지 EXPECTANT
    case("far 전부막힘->EXPECT", "far_tier",
         {"S1": 250, "S2": 460, "S3": 745}, 100.0, "S1", True, "capacity")
    # 7) off 회귀: 같은 상황에서 off 면 decide() 와 동일 (S3, 표기 없음)
    cfg["capacity_check"] = False
    ctl = Controller(cfg, dry=True)
    feas, _, _, exp, blk, bby, _ = ctl.decide_live(
        "search", None, None, None, {"S1": 250, "S2": 460, "S3": 745}, 100.0)
    f0, _, _, e0 = ctl.decide("search", None)
    ok = feas == f0 and exp == e0 and blk == [] and bby == ""
    print(f"cap {'off==decide()':34s} got=({fs_key(feas)},{exp},{bby!r}) "
          + ("" if ok else "  ★불일치"))
    bad += not ok
    cfg["capacity_check"] = True
    # 8) r_eq=0 (도착률 미관측): on 이어도 decide() 동일
    ctl = Controller(cfg, dry=True)
    feas, _, _, exp, blk, bby, _ = ctl.decide_live(
        "search", None, None, None, {"S1": 250, "S2": 460, "S3": 745}, 0.0)
    ok = fs_key(feas) == fs_key(f0) and not blk and bby == ""
    print(f"cap {'r_eq=0==decide()':34s} got=({fs_key(feas)},{exp},{bby!r}) "
          + ("" if ok else "  ★불일치"))
    bad += not ok
    return bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sorts.yaml")
    ap.add_argument("--out", default="decisions.csv")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest(a.config))
    run(a.config, a.out)

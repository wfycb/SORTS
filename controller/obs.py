#!/usr/bin/env python3
"""SORTS 관측 계층 (작업 1 Phase 1).

front Envoy access log 를 증분 tail 해서 두 가지를 추정한다.

    get_resp_size(cls)        클래스별 응답 바이트   (d_acc 항의 입력)
    get_site_state(site, cls) 사이트x클래스 f_c [ms] (slack 항의 입력)

이 두 함수는 sorts_ctl.get_access_state() 와 **같은 급의 격리 경계**다.
나중에 소스를 바꿀 때 이 파일 안쪽만 교체한다.

정의 (Phase 0 실측으로 확정, 재확인 불필요)
--------------------------------------------
f_c = 필드18 COMMON_DURATION(US_TX_BEG:US_RX_END:us)/1000 - 1x d_net[site]

  netem 은 .43 eno1 **egress 편도**에만 걸려 있다 (dst-IP u32 필터:
  .3->1:2 181us, .2->1:3 14.7ms, .40->1:4 24.7ms). 복귀 경로엔 없다.
  그래서 왕복이 아니라 1x 를 뺀다. 2x 를 빼면 전 사이트에서 음수가 된다
  (Phase 0 실측: S3 recommend p50 25.577ms - 2x25 = -24.4ms).
  calib_stress.py:8 / run_all.py:454 / s6_calib.py:8 과 같은 정의다.

  ★ 음수를 클램프하지 않는다. d_net(S1)=2.0 은 물리 편도(181us)보다
    크므로 S1 의 f_c 는 절반쯤 음수로 나온다 (Phase 0: recommend 56.4%).
    그러나 결정식이 더하는 d_net 과 여기서 빼는 d_net 이 같은 값이라
    d_net + f_c = 관측 RTT 로 상쇄되어 slack 은 정확하다. 0 으로 자르면
    이 자기정합성이 깨지고 S1 이 부당하게 비관적이 된다. 자르지 마라.
    (d_net(S1) 모델 불일치 자체는 ISSUES.md 에 별건으로 기록했다.)

통계량 (D3)
-----------
f_c 프라이어(sorts.yaml)는 **무부하 service p95** 다 (s5_calc.py:5).
그래서 관측도 p95 로 추정한다. 평균으로 바꾸면 프라이어 대비 0.25~0.71ms
낮아지는데(Phase 0 실측), 그 하락은 "부하가 늘었다"는 신호가 아니라
"통계량을 바꿨다"는 교란변수라 귀속이 불가능해진다. 통계량은 맞추고
부하 조건 차이만 남긴다.

  - EWMA 를 쓰지 않는다. 지수가중은 분위수에 정의되지 않는다.
    시간 윈도(WINDOW_S) 안의 표본으로 직접 분위수를 낸다.
    스무딩 파라미터는 윈도 길이 하나뿐이다 (alpha 없음).
  - resp_bytes 는 평균이다. 프라이어가 단일 고정값이고 실측 분포가
    2값 이산(search 4474/4632)이라 분위수가 의미를 갖지 않는다.
  - 평균과 p95 를 **둘 다** 계산해 snapshot() 으로 노출한다. 결정에
    쓰는 것은 p95 지만, 통계량 선택을 재실행 없이 사후 분석할 수 있어야
    한다.

입력 필터 (D1)
--------------
하드 배제 = 관측에서 제외. 기대 바이트 집합은 **게이트로 쓰지 않는다** —
resp_bytes 를 추정하는 것이 목적인데 기대 바이트로 입력을 거르면 순환이고,
search 4632 가 처음 나타났을 때 정상 응답을 실패로 버렸을 것이다.
집합 밖 바이트는 관측에 **포함**하고 카운터로만 센다.

러너의 성공 판정(응답 바이트)은 위반율 회계용이고 이것과 별개다.
같은 코드로 묶지 않는다.

Python 3.10.12 (.43) 호환. from __future__ import annotations 사용.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import deque

# ------------------------------------------------------------------ 로그 스키마
# /etc/envoy/envoy.yaml 의 text_format_source (gen_envoy_v10.py:141) 18필드.
# 파서가 인덱스로 읽으므로 새 필드는 반드시 뒤에만 붙인다.
N_FIELDS = 18
F_START_TIME = 0        # %START_TIME(%s.%6f)%        epoch.us
F_PATH = 3              # %REQ(X-ENVOY-ORIGINAL-PATH?:PATH)%
F_RESPONSE_CODE = 4     # %RESPONSE_CODE%
F_RESPONSE_FLAGS = 5    # %RESPONSE_FLAGS%
F_UPSTREAM_CLUSTER = 9  # %UPSTREAM_CLUSTER%
F_UPSTREAM_HOST = 10    # %UPSTREAM_HOST%             IP:port
F_BYTES_SENT = 14       # %BYTES_SENT%
F_US_RT_US = 17         # %COMMON_DURATION(US_TX_BEG:US_RX_END:us)%
F_XFF = 11              # %REQ(X-FORWARDED-FOR)%       UE(코호트) 주소

OK_RESPONSE_CODE = "200"
OK_RESPONSE_FLAGS = "-"

# 클래스 = 경로 prefix (gen_envoy_v10.py:25-32 UNITS 와 같은 규약)
PATH_PREFIX_CLASS = (
    ("/hotels", "search"),
    ("/reservation", "reserve"),
    ("/recommendations", "recommend"),
)
# [작업 A] 사이트 판별 규칙. 클러스터 집합의 단일 출처는 gen_envoy_v10.py 가
# config 렌더와 같은 실행에서 뱉는 envoy_keys.json 이다 (여기 하드코딩하면
# 7-prefix 무시 사고의 재발). 규칙:
#   필드10 ∈ SORTS 클러스터 명시 집합(site_s1/s2/s3 + sub_*) 만 관측에 포함.
#     - 단일 사이트 클러스터(site_s*): 필드10 이 사이트를 준다.
#     - 부분집합 클러스터(sub_*): 필드10 은 사이트를 식별하지 못한다 —
#       필드11(UPSTREAM_HOST) IP 로 판별한다 (아카이브 936만 요청 중
#       필드11 미상 0건, Phase 0). IP 가 클러스터의 허용 사이트 밖이면
#       순도 위반 — 카운터(n_subset_mismatch)로 세고 관측에는 포함한다
#       (실제로 그 사이트로 간 트래픽의 실측이다).
#   비교군(bl_*)·fallback·'-' 는 배제 유지 — "site_ 접두" 같은 규칙이 아니라
#   명시 집합 검사라서 bl_* 가 IP 폴백으로 새 들어올 일이 없다.
_KEYS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "envoy_keys.json")
try:
    _EK = json.load(open(_KEYS_PATH))
except OSError as _e:
    raise SystemExit(f"envoy_keys.json 없음({_e}) — gen_envoy_v10.py 렌더 산출물을 "
                     f"이 디렉터리에 배포해야 한다")
# SORTS 클러스터 -> 허용 사이트 tuple (명시 집합 필터의 원본)
SORTS_CLUSTER_SITES = {c: tuple(_EK["cluster_sites"][c])
                       for c in _EK["sorts_clusters"]}
# 하위 호환 이름 유지 (단일 사이트 클러스터만)
CLUSTER_SITE = {c: s[0] for c, s in SORTS_CLUSTER_SITES.items() if len(s) == 1}
# 필드11 IP -> 사이트. sub_* 클러스터의 사이트 판별에 라이브로도 쓴다 (작업 A).
# 무조건 IP 폴백(allow_host_fallback)은 여전히 리플레이 전용이다 — 라이브에서
# 켜면 비교군(bl_*) 런의 꼬리가 관측에 섞인다.
HOST_IP_SITE = {ip: s for s, ip in _EK["site_ip"].items()}

SITES = ("S1", "S2", "S3")
CLASSES = ("reserve", "search", "recommend")

# ------------------------------------------------------------------ 기본 파라미터
DEFAULT_LOG_PATH = "/var/log/envoy/front_access.log"
# 윈도 2.0s: 셀당 수백 rps 라 p95 표본이 충분하면서, 작업 2 에서 제어주기를
# 25~50ms 로 내려도 윈도는 그대로 둘 수 있다 (윈도와 주기는 독립이다).
DEFAULT_WINDOW_S = 2.0
# n_min 100: p95 를 100표본으로 내면 상위 5개 안에서 고르는 셈이다.
DEFAULT_N_MIN_FC = 100
# 바이트는 평균이라 표본이 훨씬 덜 필요하다.
DEFAULT_N_MIN_BYTES = 20
# 스테일: 트래픽을 안 보낸 사이트의 f_c 는 늙는다. 초과하면 프라이어 복귀.
# 관측 없는 사이트에 임의 페널티/보너스를 주지 않는다 — 프라이어 복귀만.
DEFAULT_STALE_TTL_S = 2.0
DEFAULT_FC_QUANTILE = 0.95
# 한 주기에 읽을 최대 바이트. 1400rps x ~200B = ~280KB/s 이므로 8MB 는
# 30초 밀림까지 흡수한다. 밀리면 backlog_bytes 로 드러난다.
DEFAULT_MAX_READ_BYTES = 8 * 1024 * 1024

SRC_PRIOR = "prior"
SRC_OBS = "obs"
# 표본 수는 충분한데 윈도가 아직 시간으로 안 찬 상태 (값은 프라이어를 쓴다).
# 런 첫 1초의 콜드 스타트 꼬리가 p95 를 오염시키는 사건(Phase 1 §6.1 사건 B,
# D6 t=1.0 의 +54.2ms 계단)을 막는다. n_min(표본 수 축)과 이 게이트(시간 축)는
# 같은 조건의 두 투영이다.
SRC_PRIOR_FILL = "prior_fill"
# 윈도 내용의 시간 폭(last-first)이 WINDOW_S 의 이 비율 이상이어야 obs 로
# 전환한다. 1.0 으로 두지 않는 이유: prune 경계(cutoff = now - span) 때문에
# 첫 표본은 항상 cutoff 보다 조금 뒤, 마지막 표본은 now 보다 조금 앞이라
# 정상 상태에서도 내용 폭이 WINDOW_S 에 못 미친다. 0.8 은 그 여유다.
FILL_RATIO = 0.8
# 방어(Phase 1 결함 2 부류): prune 이 망가지면 윈도가 무한 성장해 주기 비용이
# 조용히 치솟는다. 셀당 도착률이 이 값을 넘는 것은 물리적으로 불가능하므로
# (테스트베드 총 부하 ~수천 rps), 초과 = 버그다. 걸려도 죽이지 않는다 —
# 실험 중단이 더 비싸다. 크게 로그하고 snapshot 플래그만 남긴다.
WINDOW_SANITY_RPS = 10000

# 소프트 이상 판정용 (게이트 아님, 카운터 전용). Phase 0 실측 936만 요청 기준.
KNOWN_BYTES = {
    "reserve": frozenset((36,)),
    "recommend": frozenset((200,)),
    "search": frozenset((4474, 4632)),
}


def pctl(sorted_xs, q):
    """run_all.py:286 과 **같은** 분위수 관례. 기존 분석과 축을 맞춘다."""
    if not sorted_xs:
        return None
    return sorted_xs[int(round(q * (len(sorted_xs) - 1)))]


def class_of(path):
    """경로 prefix -> 클래스. 해당 없으면 None."""
    for prefix, klass in PATH_PREFIX_CLASS:
        if path.startswith(prefix):
            return klass
    return None


def site_of(cluster, host, allow_host_fallback=False):
    """(필드10, 필드11) -> 사이트. 비교군(bl_*)/fallback/'-' 이면 None (기본).

    [작업 A] SORTS 클러스터 명시 집합 검사. 단일 사이트 클러스터는 필드10,
    부분집합 클러스터(sub_*)는 필드11 IP 로 판별한다. 집합 밖 IP 여도 실제
    도달 사이트를 반환한다 — 순도 위반 여부는 subset_mismatch() 로 따로 센다.

    allow_host_fallback 은 오프라인 리플레이 전용이다. 라이브에서 켜면
    비교군 런의 꼬리가 관측에 섞인다.
    """
    sites = SORTS_CLUSTER_SITES.get(cluster)
    if sites is not None:
        if len(sites) == 1:
            return sites[0]
        return HOST_IP_SITE.get(host.split(":")[0])
    if allow_host_fallback:
        return HOST_IP_SITE.get(host.split(":")[0])
    return None


def subset_mismatch(cluster, site):
    """순도 위반 판정: SORTS 클러스터인데 실제 도달 사이트가 허용 집합 밖."""
    sites = SORTS_CLUSTER_SITES.get(cluster)
    return sites is not None and site is not None and site not in sites


class _Window:
    """고정 시간 윈도. (ts, value) 를 담고 오래된 것을 왼쪽에서 버린다.

    deque 라 prune 이 O(버린 개수) 다. 분위수만 O(n log n) 인데 셀당 주기당
    한 번만 계산한다 (Observer._recompute 가 캐시한다).
    """

    __slots__ = ("span_s", "q", "last_ts")

    def __init__(self, span_s):
        self.span_s = span_s
        self.q = deque()
        self.last_ts = None

    def add(self, ts, value):
        self.q.append((ts, value))
        if self.last_ts is None or ts > self.last_ts:
            self.last_ts = ts

    def prune(self, now):
        cutoff = now - self.span_s
        q = self.q
        while q and q[0][0] < cutoff:
            q.popleft()

    def stats(self, quantile):
        """(n, mean, p_q). 비었으면 (0, None, None)."""
        n = len(self.q)
        if n == 0:
            return 0, None, None
        vals = sorted(v for _, v in self.q)
        return n, sum(vals) / n, pctl(vals, quantile)

    def fill_span(self):
        """현재 내용의 시간 폭 [s] (prune 후 첫 표본 ~ 마지막 표본)."""
        if len(self.q) < 2:
            return 0.0
        return self.q[-1][0] - self.q[0][0]


class Observer:
    """access log 증분 tail + 윈도 통계. 상태를 전부 여기 가둔다."""

    def __init__(self, cfg, log_path=DEFAULT_LOG_PATH, window_s=DEFAULT_WINDOW_S,
                 n_min_fc=DEFAULT_N_MIN_FC, n_min_bytes=DEFAULT_N_MIN_BYTES,
                 stale_ttl_s=DEFAULT_STALE_TTL_S, fc_quantile=DEFAULT_FC_QUANTILE,
                 max_read_bytes=DEFAULT_MAX_READ_BYTES, start_at_end=True,
                 allow_host_fallback=False):
        # 프라이어는 전부 sorts.yaml 에서 온다. 여기에 상수를 박지 않는다.
        self.prior_bytes = dict(cfg["resp_bytes"])
        self.prior_fc = {k: dict(v) for k, v in cfg["f_c_ms"].items()}
        self.d_net = dict(cfg["d_net_ms"])

        self.log_path = log_path
        self.window_s = float(window_s)
        self.n_min_fc = int(n_min_fc)
        self.n_min_bytes = int(n_min_bytes)
        self.stale_ttl_s = float(stale_ttl_s)
        self.fc_quantile = float(fc_quantile)
        self.max_read_bytes = int(max_read_bytes)
        self.start_at_end = bool(start_at_end)
        # 라이브는 False 고정. 리플레이에서 비교군 슬라이스를 볼 때만 True.
        self.allow_host_fallback = bool(allow_host_fallback)

        # 내부 상태: 클래스별 3개 + 사이트x클래스 9개
        # [작업 B] 도착률 관측: (XFF ip, class) -> 도착 윈도. 용량 검사의
        # 입력. 총 도착은 라우팅 결정과 무관한 외생량이라 되먹임이 없다
        # (폐루프 생성기의 backlog 조절만 2차 효과로 남는다 — 보고서 명시).
        self.w_arr = {}
        self._cache_rates = {}
        self.w_bytes = {c: _Window(self.window_s) for c in CLASSES}
        self.w_fc = {(s, c): _Window(self.window_s)
                     for s in SITES for c in CLASSES}

        # tail 위치
        self._ino = None
        self._offset = 0
        self._partial = ""
        self.backlog_bytes = 0

        # 캐시 (주기당 1회 갱신). 조회는 O(1).
        self._cache_bytes = {}
        self._cache_fc = {}
        self._now = 0.0

        # 계측/진단
        self.update_ms = deque(maxlen=4096)
        self.last_update_ms = 0.0
        self.n_lines = 0
        self.n_used = 0
        self.n_short = 0            # 필드 수 부족
        self.n_bad_code = 0
        self.n_bad_flags = 0
        self.n_zero_bytes = 0
        self.n_parse_fail = 0
        self.n_other_cluster = 0    # bl_* / fallback / '-'
        self.n_other_class = 0
        self.n_subset_mismatch = 0  # [작업 A] 허용 집합 밖 도달 (§2.3, 0 이어야)
        self.resp_bytes_novel_n = 0
        self.novel_bytes = {}       # (cls, bytes) -> count
        self._novel_warned = set()
        self.n_rotations = 0
        self.warn = []              # 사람이 읽을 경고 (1회성)
        self.window_overflow = {}   # 윈도 폭주 플래그 {셀: 표본수} (방어)
        self._last_obs = {}         # (site,cls) -> obs 였던 마지막 p95

        self._recompute(time.time())

    # -------------------------------------------------------------- 수집
    def ingest_line(self, line, now):
        """로그 한 줄 반영. 관측에 쓰였으면 True.

        하드 배제(D1): 코드!=200, 플래그!='-', BYTES_SENT==0, 필드18 파싱 실패.
        사이트 미상(비교군/fallback)도 제외 — 필드10 ∈ SORTS 클러스터 명시
        집합(site_s* + sub_*, envoy_keys.json) 만. sub_* 는 필드11 IP 판별.
        """
        self.n_lines += 1
        p = line.split(",")
        if len(p) < N_FIELDS:
            self.n_short += 1
            return False
        # [작업 B] 도착 카운트는 코드/바이트 배제 **앞**에서 한다 — 용량
        # 검사의 입력은 '도착'이지 '성공'이 아니다 (과부하 중 비200 제외 시
        # 과소계상). f_c 관측 배제 규칙(D1)에는 영향 없음.
        k_arr = class_of(p[F_PATH])
        if k_arr is not None:
            try:
                self.w_arr.setdefault((p[F_XFF], k_arr),
                                      _Window(self.window_s)
                                      ).add(float(p[F_START_TIME]), 1.0)
            except ValueError:
                pass
        if p[F_RESPONSE_CODE] != OK_RESPONSE_CODE:
            self.n_bad_code += 1
            return False
        if p[F_RESPONSE_FLAGS] != OK_RESPONSE_FLAGS:
            self.n_bad_flags += 1
            return False
        klass = class_of(p[F_PATH])
        if klass is None:
            self.n_other_class += 1
            return False
        site = site_of(p[F_UPSTREAM_CLUSTER], p[F_UPSTREAM_HOST],
                       self.allow_host_fallback)
        if site is None:
            self.n_other_cluster += 1
            return False
        # [작업 A] 순도 카운터: 허용 집합 밖 도달. 0 이어야 한다 (§2.3 정지
        # 조건). 관측에는 포함한다 — 실제로 그 사이트로 간 트래픽의 실측이다.
        if subset_mismatch(p[F_UPSTREAM_CLUSTER], site):
            self.n_subset_mismatch += 1
        try:
            ts = float(p[F_START_TIME])
            nbytes = int(p[F_BYTES_SENT])
            rt_us = int(p[F_US_RT_US])
        except ValueError:
            self.n_parse_fail += 1
            return False
        if nbytes == 0:
            self.n_zero_bytes += 1
            return False

        # 소프트 이상: 배제하지 않고 세기만 한다 (D1).
        known = KNOWN_BYTES.get(klass)
        if known is not None and nbytes not in known:
            self.resp_bytes_novel_n += 1
            key = (klass, nbytes)
            self.novel_bytes[key] = self.novel_bytes.get(key, 0) + 1
            if key not in self._novel_warned:
                self._novel_warned.add(key)
                self.warn.append(
                    "미지 응답 바이트 {}={}B (관측에는 포함). 기대집합={}"
                    .format(klass, nbytes, sorted(known)))

        # f_c = 필드18[ms] - 1x d_net. 클램프 없음.
        fc = rt_us / 1000.0 - self.d_net[site]
        self.w_bytes[klass].add(ts, float(nbytes))
        self.w_fc[(site, klass)].add(ts, fc)
        self.n_used += 1
        return True

    # -------------------------------------------------------------- tail
    def _tail_lines(self):
        """증분 tail. inode 교체 / 크기 감소를 감지하면 처음부터 다시 읽는다.

        로그는 로테이션이 설정돼 있지 않지만(Phase 0 확인), 수동 truncate 나
        파일 교체에 대비한다. 감지되면 재동기하고 n_rotations 를 올린다.

        log_path=None 은 리플레이 모드다 — 파일을 읽지 않고 호출자가
        ingest_line 으로 직접 먹인다.
        """
        if self.log_path is None:
            return []
        try:
            st = os.stat(self.log_path)
        except OSError:
            return []

        if self._ino is None:
            # 최초 1회. 2.9GB 를 처음부터 읽지 않는다 — 끝에 붙는다.
            self._ino = st.st_ino
            self._offset = st.st_size if self.start_at_end else 0
            self._partial = ""
        elif st.st_ino != self._ino or st.st_size < self._offset:
            self._ino = st.st_ino
            self._offset = 0
            self._partial = ""
            self.n_rotations += 1

        avail = st.st_size - self._offset
        self.backlog_bytes = max(avail, 0)
        if avail <= 0:
            return []

        want = min(avail, self.max_read_bytes)
        try:
            with open(self.log_path, "rb") as f:
                f.seek(self._offset)
                data = f.read(want)
        except OSError:
            return []
        if not data:
            return []
        self._offset += len(data)
        self.backlog_bytes = max(st.st_size - self._offset, 0)

        text = self._partial + data.decode("utf-8", "replace")
        lines = text.split("\n")
        # 마지막 조각은 아직 완결되지 않은 줄일 수 있다. 다음 주기로 넘긴다.
        self._partial = lines.pop()
        return lines

    # -------------------------------------------------------------- 갱신
    def _src_of(self, w, n, n_min, now):
        """윈도 상태 -> (src, fill_ratio).

        prior      = 표본 부족 또는 스테일.
        prior_fill = 표본 수는 찼는데 윈도 시간 폭이 아직 FILL_RATIO 미만.
                     런 첫 구간의 콜드 스타트 꼬리를 p95 에 넣지 않기 위한
                     게이트다 (사건 B). 값은 prior 와 같이 프라이어를 쓰지만
                     src 를 구분해 로그에서 원인을 볼 수 있게 한다.
        obs        = 둘 다 통과.
        """
        fill = min(w.fill_span() / self.window_s, 1.0) if self.window_s else 0.0
        fresh = (w.last_ts is not None
                 and (now - w.last_ts) <= self.stale_ttl_s)
        if not (n >= n_min and fresh):
            return SRC_PRIOR, fill
        if fill < FILL_RATIO:
            return SRC_PRIOR_FILL, fill
        return SRC_OBS, fill

    def _sanity(self, name, n):
        """prune 고장 감지 (결함 2 부류). 초과 = 크게 로그 + 플래그, 비치명."""
        cap = WINDOW_SANITY_RPS * self.window_s
        if n > cap and name not in self.window_overflow:
            self.window_overflow[name] = n
            msg = ("★★ 윈도 폭주 의심: {} 표본 {} > 상한 {:.0f} "
                   "(WINDOW_SANITY_RPS {} x window {}s). prune 이 안 돌고 있을 "
                   "수 있다 — 주기 비용이 조용히 오른다. 실험은 계속한다."
                   .format(name, n, cap, WINDOW_SANITY_RPS, self.window_s))
            self.warn.append(msg)
            print(msg, file=sys.stderr, flush=True)

    def _recompute(self, now):
        """윈도 prune + 통계 캐시. 조회 O(1) 로 만든다."""
        self._now = now
        # [작업 B] 도착률 캐시: (ip, class) -> rps = 표본수/윈도폭. 윈도가
        # 덜 찬 초기 1~2 s 는 과소계상된다 — 용량 검사가 그동안 관대해질
        # 뿐이라 안전측이고, 요동하는 실폭 나눗셈보다 안정적이라 이쪽을 택함.
        cr = {}
        for key, w in self.w_arr.items():
            w.prune(now)
            n = len(w.q)
            cr[key] = n / self.window_s if n >= 2 else 0.0
        self._cache_rates = cr
        cb = {}
        for c in CLASSES:
            w = self.w_bytes[c]
            w.prune(now)
            n, mean, p = w.stats(self.fc_quantile)
            self._sanity("bytes|{}".format(c), n)
            stale_ms = (None if w.last_ts is None
                        else max((now - w.last_ts) * 1000.0, 0.0))
            src, fill = self._src_of(w, n, self.n_min_bytes, now)
            use_obs = src == SRC_OBS
            cb[c] = {"n": n, "mean": mean, "p95": p, "stale_ms": stale_ms,
                     "src": src, "fill": fill,
                     "value": mean if use_obs else float(self.prior_bytes[c]),
                     "prior": float(self.prior_bytes[c])}
        self._cache_bytes = cb

        cf = {}
        for s in SITES:
            for c in CLASSES:
                w = self.w_fc[(s, c)]
                w.prune(now)
                n, mean, p = w.stats(self.fc_quantile)
                self._sanity("fc|{}|{}".format(s, c), n)
                stale_ms = (None if w.last_ts is None
                            else max((now - w.last_ts) * 1000.0, 0.0))
                src, fill = self._src_of(w, n, self.n_min_fc, now)
                use_obs = src == SRC_OBS
                prior = float(self.prior_fc[c][s])
                if use_obs:
                    # 스테일 복귀 진단용: obs 였던 마지막 p95 (0-3 계측).
                    self._last_obs[(s, c)] = p
                cf[(s, c)] = {"n": n, "mean": mean, "p95": p,
                              "stale_ms": stale_ms,
                              "src": src, "fill": fill,
                              "value": p if use_obs else prior,
                              "prior": prior,
                              "last_obs": self._last_obs.get((s, c))}
        self._cache_fc = cf

    def update(self, now=None):
        """한 주기분: tail -> ingest -> 통계 갱신. 반환 = 소요 [ms].

        이 소요시간이 작업 2 에서 제어주기를 25~50ms 로 내릴 수 있는지의
        근거다. 호출자가 매 주기 로그에 남겨야 한다.
        """
        t0 = time.perf_counter()
        if now is None:
            now = time.time()
        for line in self._tail_lines():
            if line:
                self.ingest_line(line, now)
        self._recompute(now)
        dt = (time.perf_counter() - t0) * 1000.0
        self.last_update_ms = dt
        self.update_ms.append(dt)
        return dt

    # -------------------------------------------------------------- 조회
    def get_resp_size(self, cls):
        """클래스 응답 바이트 [B]. 관측 부족/스테일이면 프라이어."""
        e = self._cache_bytes.get(cls)
        if e is None:
            return float(self.prior_bytes[cls])
        return float(e["value"])

    def get_unit_rates(self):
        """[작업 B] (XFF ip, class) -> 도착률 [rps]. 용량 검사 입력."""
        return dict(self._cache_rates)

    def get_site_state(self, site, cls):
        """사이트x클래스 f_c [ms]. 관측 부족/스테일이면 프라이어.

        코호트를 구분하지 않는다 — f_c 는 사이트x클래스 속성이라 6유닛의
        관측을 같은 9칸에 합산해 커버리지를 높인다.
        """
        e = self._cache_fc.get((site, cls))
        if e is None:
            return float(self.prior_fc[cls][site])
        return float(e["value"])

    def snapshot(self):
        """진단용 전체 상태. decisions.csv 열(Phase 3)과 보고서가 쓴다."""
        return {
            "now": self._now,
            "bytes": {c: dict(v) for c, v in self._cache_bytes.items()},
            "fc": {"{}|{}".format(s, c): dict(v)
                   for (s, c), v in self._cache_fc.items()},
            "update_ms": self.last_update_ms,
            "backlog_bytes": self.backlog_bytes,
            "n_lines": self.n_lines, "n_used": self.n_used,
            "n_short": self.n_short, "n_bad_code": self.n_bad_code,
            "n_bad_flags": self.n_bad_flags, "n_zero_bytes": self.n_zero_bytes,
            "n_parse_fail": self.n_parse_fail,
            "n_other_cluster": self.n_other_cluster,
            "n_other_class": self.n_other_class,
            "n_subset_mismatch": self.n_subset_mismatch,
            "resp_bytes_novel_n": self.resp_bytes_novel_n,
            "novel_bytes": {"{}:{}".format(k[0], k[1]): v
                            for k, v in self.novel_bytes.items()},
            "n_rotations": self.n_rotations,
            "warn": list(self.warn),
            "window_overflow": dict(self.window_overflow),
        }


# ------------------------------------------------------------------ 모듈 API
# 격리 경계는 아래 두 조회 함수다 (지시 §2.1). init/update 는 생명주기이고
# snapshot 은 진단이다 — tail 기반 관측기라 조회 함수만으로는 구성할 수 없다.
_OBS = None


def init(cfg, **kw):
    """싱글턴 생성. cfg = sorts.yaml 로드 결과."""
    global _OBS
    _OBS = Observer(cfg, **kw)
    return _OBS


def observer():
    return _OBS


def update(now=None):
    return 0.0 if _OBS is None else _OBS.update(now)


def get_resp_size(cls):
    """bytes"""
    if _OBS is None:
        raise RuntimeError("obs.init(cfg) 를 먼저 불러야 한다")
    return _OBS.get_resp_size(cls)


def get_site_state(site, cls):
    """f_c, ms"""
    if _OBS is None:
        raise RuntimeError("obs.init(cfg) 를 먼저 불러야 한다")
    return _OBS.get_site_state(site, cls)


def snapshot():
    return {} if _OBS is None else _OBS.snapshot()

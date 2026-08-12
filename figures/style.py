#!/usr/bin/env python3
"""그림 공통 스타일 — 색·선 스타일·프로파일 (전 그림 공통, 색맹 안전 팔레트).

색은 Okabe-Ito. **선 스타일을 병용**해 흑백 인쇄·색맹 조건에서도 구분된다.
정책 색은 전 그림에서 고정한다 (F1~F5 모두 이 모듈을 쓴다).
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# ---- 정책 (전 그림 고정)
POLICY = {
    "SORTS":      {"color": "#0072B2", "ls": "-",  "marker": "o"},   # blue
    "bl_lr":      {"color": "#E69F00", "ls": "--", "marker": "s"},   # orange
    "bl_loc_pri": {"color": "#009E73", "ls": ":",  "marker": "^"},   # green
}
POLICY_LABEL = {"SORTS": "SORTS", "bl_lr": "bl_lr (least-request)",
                "bl_loc_pri": "bl_loc_pri (locality-first)"}

# ---- 사이트 (분배·slack 공통)
SITE = {
    "S1": {"color": "#D55E00", "ls": "-",  "hatch": ""},    # vermillion (edge)
    "S2": {"color": "#56B4E9", "ls": "--", "hatch": "//"},  # sky blue
    "S3": {"color": "#CC79A7", "ls": ":",  "hatch": ".."},  # reddish purple
}
SITE_LABEL = {"S1": "S1 (edge, d_net 2 ms)", "S2": "S2 (regional, 15 ms)",
              "S3": "S3 (central, 25 ms)"}

ANNOT = "#444444"          # 수직선·주석
SLO_COLOR = "#000000"

PROFILES = {
    # 발표용: 16:9, 큰 폰트, 굵은 선
    "talk": {"figsize_4panel": (13.33, 7.5), "figsize_1panel": (13.33, 5.6),
             "font": 15, "small": 13, "lw": 2.6, "grid_alpha": 0.25, "dpi": 300},
    # 논문용: 단일 컬럼 폭(3.4 in) 기준, 작은 폰트, 가는 선
    "paper": {"figsize_4panel": (3.4, 5.2), "figsize_1panel": (3.4, 2.2),
              "font": 7.5, "small": 6.5, "lw": 1.1, "grid_alpha": 0.3, "dpi": 300},
}


def apply(profile="talk", scale=1.0):
    """scale = 투사 환경별 확대율(발표장에서 글씨가 작을 때 1.2·1.5 로 재방출)."""
    p = dict(PROFILES[profile])
    if scale != 1.0:
        for k in ("font", "small", "lw"):
            p[k] = p[k] * scale
        for k in ("figsize_4panel", "figsize_1panel"):
            p[k] = tuple(v * scale for v in p[k])
    plt.rcParams.update({
        "font.size": p["font"],
        "axes.titlesize": p["font"],
        "axes.labelsize": p["font"],
        "xtick.labelsize": p["small"],
        "ytick.labelsize": p["small"],
        "legend.fontsize": p["small"],
        "lines.linewidth": p["lw"],
        "axes.grid": True,
        "grid.alpha": p["grid_alpha"],
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 110,
        "savefig.bbox": "tight",
        "pdf.fonttype": 42,          # 벡터 텍스트 유지 (편집 가능)
        "ps.fonttype": 42,
    })
    return p


def save(fig, outdir, name, dpi=300):
    """PDF(벡터) + PNG(300 dpi) 동시 방출."""
    import os
    os.makedirs(outdir, exist_ok=True)
    pdf = os.path.join(outdir, f"{name}.pdf")
    png = os.path.join(outdir, f"{name}.png")
    fig.savefig(pdf)
    fig.savefig(png, dpi=dpi)
    plt.close(fig)
    return pdf, png

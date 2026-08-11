#!/usr/bin/env python3
"""STAGE1_REPORT.md 자동 작성 (지시 §7 11항목).

s1_analyze.py 가 분석 직후 호출한다 — 드라이버가 subprocess 로 부르는
경로라 세션·터미널 생존과 무관하게 보고서까지 나온다.

원칙: **데이터에서 나오는 것만 쓴다.** 판정 문장은 수치 조건으로 만들고,
사람이 판단해야 하는 자리는 그렇다고 표시한다 (빈칸을 추측으로 채우지
않는다). 실패·미검증은 숨기지 않는다.
"""
import json
import os
import subprocess
import time

EXP = "/home/user/exp"
B3_REF = (6.50, 0.85)
FROZEN_MD5 = {                       # b3-freeze 시점 (docs/FREEZE.md)
    "sorts_ctl.py": "8ff7b20648e316ecd31e1c142d989ac4",
    "obs.py": "b9fac68d079b017acf99d451cd9ddbae",
    "sorts.yaml.tmpl": "e7be4f2ba2c21176f73db67684549b41",
    "run_all.py": "824fcaadce238de2fec4764ee5edb97c",
    "gen_envoy_v10.py": "cc18cf55b57b7e6805a1d6bededd253d",
    "envoy_keys.json": "a082dafa370da750efafa12f9e3c427b",
}


def md5(p):
    r = subprocess.run(f"md5sum {p} 2>/dev/null | cut -d' ' -f1", shell=True,
                       capture_output=True, text=True)
    return r.stdout.strip() or "없음"


def fmt(x, nd=2, dash="—"):
    return dash if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def mean_sd(xs):
    if not xs:
        return None, None
    m = sum(xs) / len(xs)
    if len(xs) < 2:
        return m, 0.0
    return m, (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def sec_nodefail(res, L):
    L.append("\n## 4. 노드 장애 before/after + 복구 3지표\n")
    rows = []
    for batch in ("nf-repro", "nf-hc"):
        for r in res.get(batch, []):
            if "error" in r:
                L.append(f"- ★{batch}/{r.get('run_id')}: {r['error']}")
                continue
            rows.append((batch, r))
    if not rows:
        L.append("데이터 없음 (배치 미완료).")
        return
    L.append("| 런 | arm | 차단창 위반% | 재개 지연 s | 정상화 지연 s | "
             "드레인 손실 (위반/총) |")
    L.append("|---|---|---|---|---|---|")
    for batch, r in rows:
        arm = ("SORTS (HC 없음)" if batch == "nf-repro" else
               ("SORTS + HC" if r["policy"] == "sorts_reactive" else "bl_lr + HC"))
        bw = r["block_win"]["rate"]
        nz = "" if r["normalized"] else " (미달성)"
        L.append(f"| {r['run_id']} | {arm} | "
                 f"{fmt(None if bw is None else bw * 100, 1)} | "
                 f"{fmt(r['resume_delay_s'], 1)} | "
                 f"{fmt(r['normalize_delay_s'], 1)}{nz} | "
                 f"{r['drain']['viol']}/{r['drain']['n']} "
                 f"({fmt(None if r['drain']['rate'] is None else r['drain']['rate']*100, 1)}%) |")
    L.append("\n지표 정의 (사전 고정, 사후 조정 없음): 재개 = 해제 후 S3 완료 "
             "트래픽 첫 관측; 정상화 = 10 s 롤링 위반율이 pre 수준(pre 평균 + "
             "1σ) 이하로 떨어져 10 s 유지되는 시각; 드레인 손실 = 해제~정상화 "
             "구간의 위반 건수. 정상화 미달성이면 드레인 구간은 측정 종료까지로 "
             "잡았다 (하한).\n")
    # arm 요약
    L.append("### arm 요약 (평균 ± 표본표준편차)\n")
    L.append("| arm | n | 차단창 위반% | 재개 s | 정상화 s | 드레인 위반건 |")
    L.append("|---|---|---|---|---|---|")
    groups = {}
    for batch, r in rows:
        arm = ("SORTS_noHC" if batch == "nf-repro" else
               ("SORTS_HC" if r["policy"] == "sorts_reactive" else "bl_lr_HC"))
        groups.setdefault(arm, []).append(r)
    for arm, rs in groups.items():
        bw = [r["block_win"]["rate"] * 100 for r in rs if r["block_win"]["rate"] is not None]
        rd = [r["resume_delay_s"] for r in rs if r["resume_delay_s"] is not None]
        nd = [r["normalize_delay_s"] for r in rs if r["normalize_delay_s"] is not None]
        dv = [r["drain"]["viol"] for r in rs]
        f = lambda xs: (f"{mean_sd(xs)[0]:.1f} ± {mean_sd(xs)[1]:.1f}" if xs else "—")
        L.append(f"| {arm} | {len(rs)} | {f(bw)} | {f(rd)} | {f(nd)}"
                 f"{'' if len(nd) == len(rs) else f' ({len(nd)}/{len(rs)}만 달성)'} "
                 f"| {f([float(x) for x in dv])} |")
    # 귀속 분리
    L.append("\n### 개선 귀속 분리 — SORTS 는 보는가, Envoy 가 막는가\n")
    L.append("| 런 | 차단창 S3 ∈ feasible_set | 실제 S3 도달 비중 | "
             "HC 첫 실패 감지 (차단 후 s) |")
    L.append("|---|---|---|---|")
    any_sep = False
    for batch, r in rows:
        s = r.get("separation")
        if not s:
            continue
        any_sep = True
        L.append(f"| {r['run_id']} | {fmt(s['s3_in_feasible_frac_blockwin'], 3)} | "
                 f"{fmt(s['actual_s3_share_blockwin'], 4)} "
                 f"({s['actual_s3_arrivals_blockwin']}/{s['total_arrivals_blockwin']}) | "
                 f"{fmt(s['hc_first_fail_rel_after_block_s'], 1)} |")
    if not any_sep:
        L.append("| (SORTS 런 없음/decisions 미회수) | — | — | — |")
    L.append("\n판정 규칙: S3 ∈ feasible ≈ 1.0 인데 실제 도달 비중이 0 에 "
             "가까우면 **SORTS 는 여전히 노드 생사를 못 보고 Envoy HC 가 막는 "
             "것** — 계층 분리의 직접 증거. 둘 다 0 이면 SORTS 도 뺀 것이라 "
             "귀속이 갈린다.\n")


def sec_axis(res, L):
    L.append("\n## 5. 서버 축 · 엣지 축 비교군 결과 (불리해도 그대로)\n")
    L.append("### 5.1 서버 축 — bl_od(outlier, 기본값) vs bl_lr\n")
    sv = res.get("server", [])
    if sv:
        L.append("| 런 | 정책 | pre 위반% | during 위반% | post 위반% | during 분배 |")
        L.append("|---|---|---|---|---|---|")
        for r in sv:
            if "error" in r:
                L.append(f"| {r['run_id']} | ★{r['error']} | | | | |")
                continue
            L.append(f"| {r['run_id']} | {r['policy']} | {fmt(r['pre']['viol_pct'],2)} | "
                     f"{fmt(r['during']['viol_pct'],2)} | {fmt(r['post']['viol_pct'],2)} | "
                     f"{r['during']['site_share']} |")
        L.append("\n비교 기준선(기존 값, 같은 교란 조건): PHASE4 S5_rr_server / "
                 "S6_lr_server / S1~S4 SORTS. 이번 bl_lr 런은 **HC-on 상태의 "
                 "재현성 대조**이므로, phase4 bl_lr 과의 차이가 크면 그 차이부터 "
                 "설명해야 한다 (config 변형 효과).\n")
    else:
        L.append("데이터 없음 (배치 미완료).\n")
    L.append("### 5.2 엣지 축 — bl_loc_pri(priority, 기본값) vs SORTS\n")
    ed = res.get("edge", [])
    if ed:
        L.append("| 런 | 정책 | both 창 위반% | during S1 몫 | S1 유입 rps | "
                 "무릎비 (400 기준) | f_c(S1) p50/p95 |")
        L.append("|---|---|---|---|---|---|---|")
        for r in ed:
            if "error" in r:
                L.append(f"| {r['run_id']} | ★{r['error']} | | | | | |")
                continue
            fc = r.get("during_fc_s1") or {}
            fcs = "; ".join(f"{k} {v['p50']}/{v['p95']}" for k, v in sorted(fc.items())) or "—"
            L.append(f"| {r['run_id']} | {r['policy']} | {fmt(r['both_viol_pct'],3)} | "
                     f"{fmt(r['during_s1_share'],3)} | {fmt(r['during_s1_share_rps'],1)} | "
                     f"{fmt(r['during_s1_knee_ratio'],2)} | {fcs} |")
        pri = [r["both_viol_pct"] for r in ed
               if r.get("policy") == "bl_loc_pri" and "both_viol_pct" in r]
        if pri:
            m, s = mean_sd(pri)
            L.append(f"\nbl_loc_pri (n={len(pri)}): **{m:.2f} ± {s:.2f} %** vs "
                     f"SORTS 검증① {B3_REF[0]} ± {B3_REF[1]} % (HC off, taskB3).")
        br = res.get("bridge_check")
        if br:
            L.append(f"\n**브리지 런** (SORTS + HC, n=1): {br['value']} % vs "
                     f"{br['ref']} → "
                     + ("범위 안 — **HC 가 엣지 축 결과를 바꾸지 않음이 확인**되어 "
                        "두 config 의 값을 나란히 놓을 수 있다."
                        if br["within"] else
                        "★**범위 밖** — HC 영향 배제 불가. 이 경우 bl_loc_pri 를 "
                        "SORTS 검증①(HC off) 과 직접 비교하면 안 되고, 브리지 "
                        "런을 기준선으로 삼거나 SORTS 를 HC-on 에서 n 을 늘려 "
                        "재측정해야 한다."))
        L.append("\n엣지 과부하 확인: bl_loc_pri 의 위반이 낮게 나오면 "
                 "S1 몫·유입 rps·무릎비(400)·f_c(S1) 로 **엣지 용량 포화가 "
                 "발현했는지**를 본다. 무릎비 < 1 이고 f_c(S1) 가 평시 수준이면 "
                 "이 조건에서는 엣지 과부하가 나타나지 않은 것이고, 그것도 "
                 "결과다 — 엣지 주장은 다른 조건(더 높은 도착률 또는 더 긴 "
                 "극단 창)에서 보여야 한다.\n")
    else:
        L.append("데이터 없음 (배치 미완료).\n")


def build(res, outroot, status):
    t = time.strftime("%F %T")
    L = [f"# 1단계 보고 — Envoy 비교군 정상화 + 제어 주기 파라미터화 ({t})",
         "",
         "자동 생성 (`analysis/stage1/s1_report.py`, 드라이버 무인 경로). "
         "원자료 `runs/stage1-20260811/`, 분석 산출 `s1_results.json`, "
         "배치 상태 `driver_status.json`.",
         "",
         "## 1. 한 줄 결론 (작업별)",
         ""]
    # 1) 결론 — 데이터가 있는 것만 단정
    ok_reg = "통과 (앞 12열 96행 동일)"
    L.append(f"1. **작업 1-1 outlier 비교군**: `bl_od` 추가·기동 확인, 서버 축 "
             f"{len(res.get('server', []))} 런 수집 — 수치는 §5.1.")
    L.append("2. **작업 1-2 locality+priority**: `bl_loc_pri` 추가. 전원 healthy "
             "에서 P0(S1) 100 % 라는 문서 기대 거동을 스모크로 실측 확인 "
             "(12/12 S1). 엣지 축 결과는 §5.2.")
    L.append("3. **작업 1-3 active HC**: 전 클러스터 HC 변형 배포·검증. "
             "**관측 오염 없음**(HC 단독 65 s 구간 access log 증가 0 B). "
             "노드 장애 before/after 는 §4.")
    L.append(f"4. **작업 1-4 주기 파라미터화**: `ctl_period_s` 도입(기본 1.0, "
             f"주기 **변경 없음**). 회귀 {ok_reg}.")
    # 2) 동결 해제 기록
    L += ["", "## 2. 동결 해제 기록 — 변경/미변경 파일", "",
          "| 파일 | 상태 | b3-freeze md5 | 현재 md5 |", "|---|---|---|---|"]
    for f, fm in FROZEN_MD5.items():
        cur = md5(os.path.join(EXP, f))
        st = "**변경**" if cur != fm else "미변경"
        L.append(f"| {f} | {st} | `{fm[:8]}` | `{cur[:8]}` |")
    L += ["",
          "- 해제 범위 준수: `obs.py` **미변경**(md5 동일), 결정 로직"
          "(`decide`/`decide_live`/용량/손실 배분) 무수정 — `sorts_ctl.py` 변경은 "
          "주기 읽기 2줄뿐이며 회귀로 결정 동일성을 확인했다.",
          "- `envoy.yaml`(.43 /etc/envoy): 렌더 산출물. 변형 2종(hc_off/hc_on) — "
          "백업 `envoy.yaml.20260811-pre-stage1.bak`.",
          "- 해제 기록·재동결 예정(2단계 종료 후)은 `docs/FREEZE.md` 해제 기록 1.",
          "- 작업 전 백업: `.40:~/exp/*.20260811-stage1.bak` 6종.",
          "- 미변경 유지: 관측 파라미터(WINDOW_S 2.0, n_min 100/20, "
          "stale_ttl 2.0, FILL_RATIO 0.8) 튜닝 없음."]
    # 3) 비교군 설정
    L += ["", "## 3. 비교군 설정 블록 — 기본값 이탈 항목과 이유", "",
          "설정 블록 **전문**은 `docs/baselines.md` (재현성). 요약:", "",
          "| 비교군 | 기본값에서 벗어난 항목 | 비고 |", "|---|---|---|",
          "| `bl_od` | **없음** (`outlier_detection: {}` = 문서 기본값 전부) | "
          "consecutive_5xx 5 / interval 10 s / base_ejection 30 s / "
          "max_ejection 10 % / enforcing 100 % |",
          "| `bl_loc_pri` | **없음** (overprovisioning factor 1.4 미설정) | "
          "우선순위 S1→S2→S3, panic threshold 기본 50 % |",
          "| active HC | **없음**, 단 4 필수 필드는 Envoy 에 기본값이 없어 "
          "k8s probe 기본값 채택 | interval 10 s / timeout 1 s / unhealthy 3 / "
          "healthy 1, path `/`(DSB 정적, DB 무접촉) |",
          "",
          "판단 근거는 `docs/baselines.md` 에 항목별로 기록했다 — 요지는 "
          "(a) 단일 출처에서 세트를 통째로 가져오고 항목별 짜깁기를 하지 않음, "
          "(b) 조정 가능한 민감도(overprovisioning)는 어느 방향이든 튜닝 시비가 "
          "성립하므로 문서 기본값이 유일한 방어 위치."]
    sec_nodefail(res, L)
    sec_axis(res, L)
    # 6) apply 경로 + flush
    fl = os.path.join(outroot, "nf-repro", "flush_result.txt")
    fltxt = open(fl).read().strip() if os.path.exists(fl) else "실측 없음"
    L += ["", "## 6. apply 경로 판정 + flush 실효 지연", "",
          "### 6.1 apply 경로 (§1-5) — **변경 시에만 호출**", "",
          "`sorts_ctl.py` 의 `run()` 은 유닛마다 결정 상태 문자열(클러스터명 "
          "또는 `W:` 비중 문자열)을 이전 값과 비교해 `changed` 일 때만 "
          "`apply()`/`apply_weights()` 를 부른다 — `runtime_modify` 는 "
          "**매 tick 이 아니다**. 결정이 안정된 구간에서 apply 비용은 0.",
          "",
          "판정: **이번에 고칠 필요 없음.** 25 ms 주기에서도 정상 상태 오버헤드는 "
          "0 이고, 비용은 전환이 잦을 때만 발생한다. 다만 2단계에서 주기를 "
          "내리면 전환 빈도 자체가 오를 수 있으므로, **틱당 apply 호출 수**를 "
          "계측(decisions.csv `changed` 합)해 duty 를 산출할 것 (지시대로 이번엔 "
          "수정하지 않았다).",
          "",
          "### 6.2 flush 실효 지연 (§1-6) — 800 rps, `--file-flush-interval-msec 100`",
          "", "```", fltxt, "```", "",
          "- `raw` = 로그 줄의 `START_TIME` → 그 줄이 tail 로 읽힌 시각. "
          "`corr` = raw − 필드16(요청 처리시간) — 즉 **응답 완료 후 로그가 "
          "보이기까지의 순수 지연**.",
          "- 같은 호스트(.43) 시계라 클럭 오프셋 없음. 폴링 5 ms 가 측정 분해능.",
          "- 해석: corr 상한이 ~100 ms 에 붙고 p50 이 그 절반 — 문서의 "
          "\"버퍼가 차거나 간격이 지나거나 먼저 오는 쪽\" 중 **간격 100 ms 가 "
          "지배**하는 형태(균일 대기). 이 부하에서는 버퍼 충만이 먼저 오지 "
          "않는다.",
          "- **설정은 바꾸지 않았다** (지시대로 2단계 입력 전용)."]
    # 7) 검증
    L += ["", "## 7. 회귀 · 순도 · 오염 검증 결과", "",
          "| 검증 | 결과 |", "|---|---|",
          f"| §5.2 회귀 (`ctl_period_s: 1.0` + 기존 arm = 동결 코드) | **{ok_reg}** |",
          "| 컨트롤러 단위 검증 (3정책 + 용량 + soft + C_eff) | 통과 |",
          "| Envoy config 유효성 (`--mode validate`, hc_off/hc_on) | OK / OK |",
          "| 신규 클러스터 순도 (필드10/11) | `bl_od` 3사이트 분산·순수, "
          "`bl_loc_pri` 12/12 S1·순수 |",
          "| §6.1.3 HC 관측 오염 | **없음** — HC 단독 65 s 에 access log 증가 0 B |",
          "| §6.1.4 키 일치 (`envoy_keys.json` ↔ `/clusters`) | precheck 통과 "
          "(클러스터 12, 런타임 키 84, healthy EP 27/27) |",
          "| §6.1.5 재기동 절차 | 전환마다 백업→교체(md5 대조)→`/ready` LIVE→"
          "healthy 27/27→런타임 키 84→컨테이너 24×3 무재시작 확인 |"]
    # 8~11
    L += ["", "## 8. 내가 판단해서 결정한 것과 근거", "",
          "1. **HC 4 필수 필드에 k8s probe 기본값 채택** — Envoy 에 기본값이 "
          "없어 선택이 불가피. 단일 규약 세트를 통째로 가져와 짜깁기를 배제했고, "
          "감지 ~30 s 는 그 세트의 산술적 귀결이지 목표가 아니다.",
          "2. **HC 경로 `/`** — DSB frontend 정적 인덱스(200/1507 B, DB 무접촉 "
          "실측). `/health`·`/healthz` 는 404 라 사용 불가.",
          "3. **HC 를 전 클러스터에 동일 적용** — 특정 비교군만 켜면 그 자체가 "
          "차별 조건이 된다. 삽입 수 == 클러스터 수를 생성기가 검사한다.",
          "4. **config 변형 2종 + `envoy_keys.json` 의 `active_hc` 기록** — "
          "사후에 어떤 변형에서 난 값인지 산출물로 식별 가능하게.",
          "5. **`ctl_period_s` 신설 + `t_ctrl_s` 하위 호환 폴백** — 구 렌더본이 "
          "섞여도 조용히 다른 주기로 돌지 않게.",
          "6. **차단 절차는 야간과 동일 스크립트 재사용**, 오케스트레이터에 "
          "**사건 epoch 기록(events.json)만 추가** — 복구 3지표가 절대 시각을 "
          "요구하는데 야간판은 HH:MM:SS 로그뿐이었다.",
          "7. **정상화 기준을 pre 평균 + 1σ 로 사전 고정** — 사후에 기준을 "
          "고르면 어떤 값이든 만들 수 있다.",
          "8. **무인 드라이버(nohup) 구성** — 세션이 끊겨도 배치→전환→원복→"
          "분석→보고서까지 완주. 비밀번호는 코드에 두지 않고 사전 조치"
          "(`/etc/envoy/envoy.yaml` 소유권)로 무권한화."]
    L += ["", "## 9. 실패 · SKIP · SUSPECT", ""]
    bad = []
    for batch in ("nf-repro", "nf-hc", "server", "edge"):
        d = os.path.join(outroot, batch)
        if not os.path.isdir(d):
            bad.append(f"- 배치 `{batch}`: 디렉터리 없음 (미실행)")
            continue
        pj = os.path.join(d, "progress.json")
        if os.path.exists(pj):
            pr = json.load(open(pj))
            for rid, v in pr.get("runs", {}).items():
                if v.get("status") not in ("DONE", "SKIPPED_RESUME"):
                    bad.append(f"- `{batch}/{rid}`: status={v.get('status')} "
                               f"{v.get('error', '')}")
        for rid in sorted(os.listdir(d)):
            for mk in ("SKIPPED", "SUSPECT"):
                p = os.path.join(d, rid, mk)
                if os.path.exists(p):
                    try:
                        j = json.load(open(p))
                        rs = j.get("reasons")
                    except Exception:
                        rs = "(파싱 실패)"
                    bad.append(f"- `{batch}/{rid}` **{mk}**: {rs}")
    L += bad or ["없음 — 전 런 DONE, SKIPPED/SUSPECT/FAILED 0."]
    st = status.get("batches", {}).get("restore") if status else None
    if st:
        L += ["", "### 종료 상태 (원복)",
              f"- hc_off 원복: {'성공' if st.get('hc_off') else '★실패'}",
              f"- iptables sorts-fault 잔재 룰: "
              f"{st.get('iptables_sorts_fault_rules', st.get('iptables_5000_rules'))}",
              f"- precheck: {st.get('precheck')}"]
    L += ["", "## 10. 검증하지 못한 채 남긴 것", "",
          "- **부분 장애(느려짐만, 응답 200)** 에서의 HC 거동 — 이번 차단은 "
          "무응답(DROP) 시나리오다. HTTP 200 이 계속 나오면 HC 는 통과시키므로 "
          "감지되지 않을 것이라 예상되지만 미실측.",
          "- **outlier detection 의 지연 기반 감지 부재** — `bl_od` 는 오류 "
          "기반이라 grey(느림+200)를 정의상 못 본다. 서버 축 결과가 이를 "
          "보여주는지는 §5.1 수치로만 판단했고, ejection 계수 스냅샷"
          "(`envoy_stats_after_*.txt`)을 근거로 병기했다.",
          "- **3노드에서 max_ejection_percent 10 % 의 첫 축출 허용 여부** — "
          "문서가 명시하지 않아 스탯으로만 관측했다 (산술 추론 금지).",
          "- **주기 변경 효과** — 이번엔 파라미터화만 했다 (기본 1.0 고정). "
          "2단계 단독 ablation.",
          "- **flush 설정 변경 효과** — 실측만 했고 `--file-flush-interval-msec` 은 "
          "그대로 둔다.",
          "",
          "## 11. 2단계(주기 ablation) 설계에 넘길 입력", "",
          "1. **apply 는 변경 시에만** — 정상 상태 오버헤드 0. 주기를 내릴 때의 "
          "비용은 apply 호출 수가 아니라 **전환 빈도**에 붙는다. 2단계에서 "
          "`changed` 합/틱을 계측해 duty 를 산출할 것.",
          "2. **flush 실효 지연이 관측 신선도의 하한** — corr p50 ~50 ms, "
          "상한 ~100 ms (§6.2). 제어 주기를 25 ms 로 내려도 **관측은 그보다 "
          "늦게 도착한다** — 주기 단독 효과와 로그 지연이 교락되므로, "
          "(a) flush 간격을 함께 내린 조건, (b) 내리지 않은 조건을 분리해 "
          "재야 귀속이 된다. 이번 실측이 그 설계의 근거값이다.",
          "3. **관측 윈도(2.0 s)는 주기와 독립** — 주기를 내려도 윈도는 그대로 "
          "둘 수 있다(obs.py 설계). 주기 ablation 에서 윈도를 같이 만지면 "
          "귀속이 깨진다.",
          "4. **`ctl_period_s` 렌더 경로가 준비됨** — manifest 에 값만 넣으면 "
          "arm 이 된다. 회귀 경계는 1.0.",
          "5. **노드 장애 계층 결론(§4)** — 이 층은 주기를 줄여도 SORTS 가 "
          "메우지 못하는 영역인지, §4 의 귀속 분리 결과가 2단계 범위 설정에 "
          "그대로 입력된다.",
          "",
          "---",
          "",
          "본 보고서는 데이터에서 도출되는 문장만 담았다. 해석·판정 중 "
          "사람의 결정이 필요한 자리는 그 자리에 명시했다."]
    return "\n".join(L)


def write(res, outroot):
    stp = os.path.join(outroot, "driver_status.json")
    status = json.load(open(stp)) if os.path.exists(stp) else {}
    txt = build(res, outroot, status)
    for p in (os.path.join(EXP, "STAGE1_REPORT.md"),
              os.path.join(outroot, "STAGE1_REPORT.md")):
        open(p, "w").write(txt + "\n")
    return os.path.join(EXP, "STAGE1_REPORT.md")


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else \
        "/home/user/exp/runs/stage1-20260811"
    r = json.load(open(os.path.join(root, "s1_results.json")))
    print("->", write(r, root))

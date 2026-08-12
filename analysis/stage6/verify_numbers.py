#!/usr/bin/env python3
"""STAGE6 S6-1 — NUMBERS.md 가 인용한 경로가 실제로 존재하는지 기계 검증.

`runs/...` · `analysis/...` · `docs/...` · `presentation/...` · `*.py|*.json|*.csv|*.md`
형태의 인용을 뽑아 존재를 확인한다. 없으면 그 수치는 "출처를 닫지 못한 것"
이므로 §7 로 옮기고 논문에서 뺀다(지시 §2-2).
"""
import os
import re
import sys

EXP = "/home/user/exp"
REPO = "/home/user/sorts-backup"
DOC = os.path.join(EXP, "NUMBERS.md")
PAT = re.compile(r"`([^`]+)`")
LOOKS_LIKE_PATH = re.compile(
    r"^(runs/|analysis/|docs/|presentation/|~/|[\w./-]+\.(py|json|csv|md|tmpl|yaml))")


def resolve(p):
    p = p.strip().rstrip(".,")
    if p.startswith("~/"):
        p = os.path.expanduser(p)
    cands = [p if os.path.isabs(p) else os.path.join(EXP, p)]
    if p.startswith(("docs/", "presentation/")):
        cands.append(os.path.join(REPO, p))
    if p.startswith("docs/"):                      # docs/X.md ↔ exp/X.md
        cands.append(os.path.join(EXP, os.path.basename(p)))
    return cands


def main():
    txt = open(DOC).read()
    cited, seen = [], set()
    for m in PAT.finditer(txt):
        s = m.group(1)
        if not LOOKS_LIKE_PATH.match(s) or " " in s:
            continue
        base = s.split("(")[0].split("[")[0]
        # 글롭 표기(`runs/x/y_1..3`, `…`) 는 앞부분만 확인
        base = base.replace("…", "").rstrip("/")
        if ".." in base:
            base = base.split("..")[0].rsplit("_", 1)[0]
        if base in seen:
            continue
        seen.add(base)
        cited.append(base)

    missing, ok = [], []
    for c in cited:
        if any(os.path.exists(x) for x in resolve(c)):
            ok.append(c)
        else:
            missing.append(c)
    print(f"인용 경로 {len(cited)}개 — 존재 {len(ok)} / 누락 {len(missing)}\n")
    for m in missing:
        print(f"  ✗ {m}")
    if not missing:
        print("  누락 없음 — 대장의 모든 인용이 파일로 닫힌다.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())

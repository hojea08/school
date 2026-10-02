#!/usr/bin/env python3
"""정답표 PDF에서 정답을 읽어 CSV에 채운다. 두 번째 출처와 맞춰 보고, 눈으로 확인할 표 그림도 만든다.

    python answers.py exams --pdf official_ans.pdf --numbers 9-15 20-22 --csv answers.csv --render sheets
    python answers.py exams --pdf official_ans.pdf --numbers 1-20 --check ans.pdf
    python answers.py 정답표.pdf                      # 한 파일만 읽어 보기

exams/<시험>/<pdf> 를 시험마다 읽는다. 읽는 모양(--format):
  kice   평가원 정답표: (문항 번호, 정답, 배점) 세 칸이 단마다 이어진다. 머리글로 공통·선택과목을 가른다.
  rows   사관학교 답안지: 번호 줄 바로 아래 「정답」 줄.
  pairs  교육청 해설지 첫머리: 「1 ③ 2 ⑤ …」 번호 오른쪽에 정답.
  auto   (기본) 셋 다 읽어 보고 번호를 가장 많이 찾은 것.

글자가 없는 그림 정답표(예: 2025학년도 수능 수학)는 읽지 못한다. --render 그림을 보고 직접 적는다.
읽은 값은 반드시 그림과 한 번 더, 다른 출처와 한 번 더 맞춰 본다(references/verification.md).
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import decode_pua, parse_numbers, same_answer  # noqa: E402

NUM_RE = re.compile(r"[1-9]\d?")
ANS_RE = re.compile(r"[①②③④⑤⓵⓶⓷⓸⓹]|\d{1,4}")
POINTS = {"1", "2", "3", "4"}
CIRCLES = "①②③④⑤⓵⓶⓷⓸⓹"

# 정답표 머리글에 나오는 과목 이름 → CSV에 쓰는 이름
SECTION_NAMES = {
    "공통과목": "공통", "공통": "공통",
    "확률과통계": "확률과 통계", "미적분": "미적분", "기하": "기하",
    "화법과작문": "화법과 작문", "언어와매체": "언어와 매체",
}


class Tok:
    __slots__ = ("t", "x0", "y0", "x1", "y1")

    def __init__(self, t, x0, y0, x1, y1):
        self.t, self.x0, self.y0, self.x1, self.y1 = t, x0, y0, x1, y1

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2


def tokens(page):
    """낱말 단위 글자. 한글 수식 글꼴 숫자를 풀고, 「1③」 「01.」 「③　03.」처럼 붙은 것은 나눈다."""
    out = []
    for x0, y0, x1, y1, w, *_ in page.get_text("words"):
        w = decode_pua(w).replace("\u3000", " ")
        if not w.strip():
            continue
        step = (x1 - x0) / max(1, len(w))
        for m in re.finditer(rf"[{CIRCLES}]|\d+[.)]?|[^\d{CIRCLES}\s]+", w):
            t = m.group()
            if re.fullmatch(r"\d+[.)]", t):
                t = t[:-1].lstrip("0") or "0"   # 「01.」 → 「1」
            out.append(Tok(t, x0 + step * m.start(), y0, x0 + step * m.end(), y1))
    return out


def same_line(a, b):
    return min(a.y1, b.y1) - max(a.y0, b.y0) >= 0.5 * min(a.y1 - a.y0, b.y1 - b.y0)


def runs(toks, max_gap):
    """한 줄로 이어지는 글자 묶음들. 옆 단 글자와 섞이지 않게, 바로 오른쪽 이웃만 따라간다."""
    buckets = {}
    for t in toks:
        buckets.setdefault(int(t.cy // 8), []).append(t)

    def right_of(t):
        best = None
        for k in (int(t.cy // 8) - 1, int(t.cy // 8), int(t.cy // 8) + 1):
            for u in buckets.get(k, ()):
                if u is t or u.x0 < t.x1 - 1 or u.x0 - t.x1 >= max_gap or not same_line(t, u):
                    continue
                if best is None or u.x0 < best.x0:
                    best = u
        return best

    nxt = {id(t): right_of(t) for t in toks}
    has_left = {id(u) for u in nxt.values() if u is not None}
    out = []
    for t in sorted(toks, key=lambda t: (t.cy, t.x0)):
        if id(t) in has_left:
            continue
        run, seen = [t], {id(t)}
        while nxt[id(run[-1])] is not None and id(nxt[id(run[-1])]) not in seen:
            run.append(nxt[id(run[-1])])
            seen.add(id(run[-1]))
        out.append(run)
    return out


def norm_section(text):
    key = re.sub(r"\s|\(.*?\)|과목$", "", text)
    if key in SECTION_NAMES:
        return SECTION_NAMES[key]
    key2 = re.sub(r"\s|\(.*?\)", "", text)
    return SECTION_NAMES.get(key2)


def find_section(text):
    """글자 안에 든 과목 이름(가장 긴 것)."""
    flat = re.sub(r"\s", "", text)
    keys = sorted((k for k in SECTION_NAMES if k in flat), key=len, reverse=True)
    return SECTION_NAMES[keys[0]] if keys else None


def header_lines(page):
    """(글자, Rect) — 표 머리글 후보."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            spans = [s for s in l["spans"] if s["text"].strip()]
            if not spans:
                continue
            names = [norm_section(s["text"]) for s in spans]
            if sum(1 for n in names if n) > 1:   # 한 줄에 과목 이름이 여럿이면 조각마다
                out += [(s["text"], pymupdf.Rect(s["bbox"])) for s in spans]
            else:
                r = pymupdf.Rect(spans[0]["bbox"])
                for s in spans[1:]:
                    r |= s["bbox"]
                out.append(("".join(s["text"] for s in spans), r))
    return out


def cell_range(page, r):
    """머리글 글자가 들어 있는 표 칸의 가로 범위. 칸 선을 못 찾으면 글자 폭."""
    cy = (r.y0 + r.y1) / 2
    cx = (r.x0 + r.x1) / 2
    boxes = [d["rect"] for d in page.get_drawings()
             if d["rect"].width > r.width and 4 < d["rect"].height < 60 and d["rect"].contains((cx, cy))]
    if boxes:
        b = min(boxes, key=lambda b: b.width * b.height)
        return b.x0, b.x1
    vx = [(d["rect"].x0 + d["rect"].x1) / 2 for d in page.get_drawings()
          if d["rect"].width < 2.5 and d["rect"].y0 - 1 <= cy <= d["rect"].y1 + 1]
    left = max([x for x in vx if x <= r.x0 + 1], default=r.x0)
    right = min([x for x in vx if x >= r.x1 - 1], default=r.x1)
    return left, right


def subject_title(page):
    """탐구 정답표의 「( 성공적인 직업생활 ) 과목」."""
    m = re.search(r"\(\s*([^()]+?)\s*\)\s*과목", page.get_text())
    return m.group(1) if m else None


# ── 읽는 방법 세 가지 ─────────────────────────────────────────────────────
# 결과: {"sections": {과목: {번호: {"answer": 답, "points": 배점|None}}}, "bbox": Rect|None}

def parse_kice(page):
    if "배점" not in re.sub(r"\s", "", page.get_text()):
        return None
    triples = []
    for row in runs(tokens(page), 45):
        i = 0
        while i + 2 < len(row):
            a, b, c = row[i], row[i + 1], row[i + 2]
            if NUM_RE.fullmatch(a.t) and ANS_RE.fullmatch(b.t) and c.t in POINTS:
                triples.append((int(a.t), b.t, int(c.t), a, c))
                i += 3
            else:
                i += 1
    if not triples:
        return None
    # 번호 칸의 x로 단을 나눈다
    triples.sort(key=lambda t: t[3].x0)
    groups = []
    for t in triples:
        if groups and t[3].x0 - groups[-1][-1][3].x0 < 15:
            groups[-1].append(t)
        else:
            groups.append([t])
    table_top = min(t[3].y0 for t in triples)
    # 머리글(「공통 과목」 「미적분」 …)이 덮는 칸 범위. 「공통 과목」은 두 단을 덮는다.
    heads = [(norm_section(text), r, cell_range(page, r)) for text, r in header_lines(page)
             if norm_section(text) and r.y1 <= table_top + 2 and table_top - r.y1 < 120]
    sections, bbox = {}, None
    for g in groups:
        gx0, gx1 = min(t[3].x0 for t in g), max(t[4].x1 for t in g)
        gc = (gx0 + gx1) / 2
        over = [(name, r) for name, r, (cx0, cx1) in heads if cx0 - 2 <= gc <= cx1 + 2]
        sec = max(over, key=lambda h: h[1].y1)[0] if over else ""
        for n, ans, pts, a, c in sorted(g, key=lambda t: t[3].y0):
            sections.setdefault(sec, {}).setdefault(n, {"answer": ans, "points": pts})
            r = pymupdf.Rect(a.x0, a.y0, c.x1, c.y1)
            bbox = r if bbox is None else bbox | r
    return {"sections": sections, "bbox": bbox}


def parse_rows(page):
    toks = tokens(page)
    sections, bbox = {}, None
    for row in runs(toks, 80):
        if row[0].t != "정답":
            continue
        answers = [t for t in row[1:] if ANS_RE.fullmatch(t.t)]
        if len(answers) < 5:   # 해설 본문의 「정답 ④」 같은 줄은 표가 아니다
            continue
        # 정답마다 바로 위 칸의 번호
        pairs = []
        for a in answers:
            above = [t for t in toks if NUM_RE.fullmatch(t.t) and abs(t.cx - a.cx) < 15 and 4 < a.cy - t.cy < 60]
            if above:
                pairs.append((max(above, key=lambda t: t.cy), a))
        if len(pairs) < 5:
            continue
        ny = sorted(n.cy for n, _ in pairs)[len(pairs) // 2]
        x0 = min(n.x0 for n, _ in pairs)
        x1 = max(a.x1 for _, a in pairs)
        # 머리글: 번호 줄 위쪽(「공 통(객관식)」, 「확률과통계(객관식)」 …)과 번호 줄 왼쪽
        label = "".join(t.t for t in sorted(toks, key=lambda t: (t.cy, t.x0))
                        if t.t not in ("문항", "번호", "정답")
                        and ((ny - 40 <= t.cy < ny - 4 and x0 - 100 < t.cx < x1 + 100)
                             or (abs(t.cy - ny) < 4 and t.x1 <= x0 + 1)))
        sec = find_section(label) or ""
        for n, a in pairs:
            sections.setdefault(sec, {}).setdefault(int(n.t), {"answer": a.t, "points": None})
            r = pymupdf.Rect(n.x0, n.y0, a.x1, a.y1)
            bbox = r if bbox is None else bbox | r
    return {"sections": sections, "bbox": bbox} if sections else None


def parse_pairs(page):
    cands = []
    for row in runs(tokens(page), 30):
        i = 0
        while i + 1 < len(row):
            a, b = row[i], row[i + 1]
            if NUM_RE.fullmatch(a.t) and ANS_RE.fullmatch(b.t) and 0 <= b.x0 - a.x1 < 30:
                cands.append((int(a.t), b.t, a, b))
                i += 2
            else:
                i += 1
    if not cands:
        return None

    def dist(p, q):
        return abs(q[2].cx - p[2].cx) + 2 * abs(q[2].cy - p[2].cy)

    def step_ok(p, q):
        """표 안에서 다음 칸: 같은 줄 오른쪽, 다음 줄, 또는 다음 단 맨 위."""
        dx, dy = q[2].cx - p[2].cx, q[2].cy - p[2].cy
        if abs(dy) < 4:
            return 0 < dx < 150
        if 0 < dy < 40:
            return True
        return dy < 0 and 0 < dx < 200

    # 1, 2, 3, … 으로 가장 길게 이어지는 사슬. 같은 번호가 또 나오면(선택과목) 사슬을 하나 더.
    used, chains = set(), []
    while True:
        starts = [c for c in cands if id(c) not in used]
        if not starts:
            break
        first_n = min(c[0] for c in starts)
        best = []
        for s in [c for c in starts if c[0] == first_n]:
            chain, cur = [s], s
            while True:
                nxt = [c for c in starts if c[0] == cur[0] + 1 and step_ok(cur, c)]
                if not nxt:
                    break
                c = min(nxt, key=lambda c: dist(cur, c))
                chain.append(c)
                cur = c
            if len(chain) > len(best):
                best = chain
        if len(best) < 5:
            used.update(id(c) for c in starts if c[0] == first_n)
            if all(id(c) in used for c in cands):
                break
            continue
        used.update(id(c) for c in best)
        chains.append(best)
    if not chains:
        return None
    heads = [(norm_section(t), r) for t, r in header_lines(page) if norm_section(t)]
    sections, bbox = {}, None
    for k, chain in enumerate(chains):
        first = chain[0][2]
        near = [(name, r) for name, r in heads
                if r.y1 <= first.y1 + 2 and first.cy - r.y1 < 60 and r.x0 < first.x0 + 120]
        sec = min(near, key=lambda h: first.cy - h[1].y1)[0] if near else ("" if k == 0 else f"묶음{k + 1}")
        for n, ans, a, b in chain:
            sections.setdefault(sec, {}).setdefault(n, {"answer": ans, "points": None})
            r = pymupdf.Rect(a.x0, a.y0, b.x1, b.y1)
            bbox = r if bbox is None else bbox | r
    return {"sections": sections, "bbox": bbox}


PARSERS = {"kice": parse_kice, "rows": parse_rows, "pairs": parse_pairs}


def parse_pdf(path, fmt="auto", page_no=1):
    doc = pymupdf.open(path)
    page = doc[page_no - 1]
    if not page.get_text("words"):
        return {"format": None, "sections": {}, "bbox": None, "page": page, "image_only": True}
    tried = {}
    for name in (PARSERS if fmt == "auto" else [fmt]):
        r = PARSERS[name](page)
        if r:
            tried[name] = r
    if not tried:
        return {"format": None, "sections": {}, "bbox": None, "page": page, "image_only": False}
    order = list(PARSERS)
    name = max(tried, key=lambda k: (sum(len(v) for v in tried[k]["sections"].values()), -order.index(k)))
    r = tried[name]
    r.update(format=name, page=page, image_only=False, subject=subject_title(page))
    return r


def pick(sections, n, want):
    """번호 n의 답. 고른 과목 → 공통 → 과목 없음 → 한 과목에만 있으면 그것."""
    for sec in [want, "공통", ""]:
        if sec is not None and n in sections.get(sec, {}):
            return sections[sec][n]["answer"]
    having = [s for s in sections if n in sections[s]]
    if len(having) == 1:
        return sections[having[0]][n]["answer"]
    return None


def render_table(r, out_png):
    page = r["page"]
    clip = (r["bbox"] + (-30, -110, 30, 20)) & page.rect if r.get("bbox") else page.rect
    zoom = 2.5 if r.get("bbox") else 1.5
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_png))


# ── CSV ─────────────────────────────────────────────────────────────────

def read_csv(path):
    if not Path(path).exists():
        return ["시험"], []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        return list(rd.fieldnames), list(rd)


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in header})


def update_csv(path, exam, values):
    header, rows = read_csv(path)
    for n in sorted(values, key=int):
        if str(n) not in header:
            header.append(str(n))
    row = next((r for r in rows if r.get("시험") == exam), None)
    if row is None:
        row = {"시험": exam}
        if "일차" in header:
            row["일차"] = str(len(rows) + 1)
        rows.append(row)
    for n, v in values.items():
        row[str(n)] = v
    write_csv(path, header, rows)


# ── 실행 ────────────────────────────────────────────────────────────────

def show(r, label):
    if r["image_only"]:
        return f"{label}: 글자 없는 그림 정답표 → --render 그림을 보고 직접 적으세요"
    if not r["sections"]:
        return f"{label}: 정답을 못 찾음"
    parts = []
    for sec, d in r["sections"].items():
        nums = sorted(d)
        parts.append(f"[{sec or '-'}] {nums[0]}~{nums[-1]}번 {len(nums)}개")
    subj = f" ({r['subject']})" if r.get("subject") else ""
    return f"{label}: {r['format']}{subj} " + ", ".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", type=Path, help="exams 폴더 또는 PDF 하나")
    ap.add_argument("--pdf", default="ans.pdf", help="시험 폴더 안 정답 PDF 이름")
    ap.add_argument("--numbers", nargs="+", help="CSV에 넣을 번호, 예: 9-15 20-22")
    ap.add_argument("--section", help="번호가 여러 과목에 있을 때 고를 과목(예: 미적분)")
    ap.add_argument("--format", choices=["auto", *PARSERS], default="auto")
    ap.add_argument("--page", type=int, default=1, help="정답표가 있는 쪽(1부터)")
    ap.add_argument("--only", nargs="*", help="이 시험들만")
    ap.add_argument("--csv", type=Path, help="채울 정답 CSV(시험 열 + 번호 열)")
    ap.add_argument("--check", nargs="*", default=[], help="맞춰 볼 두 번째 출처 PDF 이름(시험 폴더 안)")
    ap.add_argument("--render", type=Path, help="정답표 그림을 여기에 <시험>.png 로")
    ap.add_argument("--json", type=Path, help="읽은 전체 결과를 JSON으로")
    args = ap.parse_args()

    if args.src.is_file():
        r = parse_pdf(args.src, args.format, args.page)
        print(show(r, args.src.name))
        for sec, d in r["sections"].items():
            print(f"  [{sec or '-'}] " + " ".join(f"{n}:{v['answer']}" for n, v in sorted(d.items())))
        if args.render:
            render_table(r, args.render if args.render.suffix else args.render / f"{args.src.stem}.png")
        return

    numbers = parse_numbers(args.numbers) if args.numbers else None
    exams = sorted(d for d in args.src.iterdir() if (d / args.pdf).exists())
    if args.only:
        exams = [d for d in exams if d.name in args.only]
    if not exams:
        raise SystemExit(f"{args.src}/<시험>/{args.pdf} 가 없습니다.")
    dump, bad = {}, 0
    for d in exams:
        r = parse_pdf(d / args.pdf, args.format, args.page)
        print(show(r, d.name))
        if args.render:
            render_table(r, args.render / f"{d.name}.png")
        dump[d.name] = {"format": r["format"], "subject": r.get("subject"),
                        "sections": {s: {str(n): v for n, v in sorted(x.items())}
                                     for s, x in r["sections"].items()}}
        if numbers is None:
            for sec, x in r["sections"].items():
                print(f"  [{sec or '-'}] " + " ".join(f"{n}:{v['answer']}" for n, v in sorted(x.items())))
            continue
        values = {n: pick(r["sections"], n, args.section) for n in numbers}
        missing = [n for n, v in values.items() if v is None]
        print("  " + " ".join(f"{n}:{v or '?'}" for n, v in values.items()))
        if missing:
            print(f"  ✗ 못 읽은 번호 {missing}")
            bad += 1
        for other in args.check:
            path = d / other
            if not path.exists():
                print(f"  · {other} 없음")
                continue
            r2 = parse_pdf(path, "auto", 1)
            diff, unread = [], []
            for n, v in values.items():
                v2 = pick(r2["sections"], n, args.section)
                if v2 is None:
                    unread.append(n)
                elif v is not None and not same_answer(v, v2):
                    diff.append(f"{n}번 {v}≠{v2}")
            if len(unread) == len(values):
                print(f"  · {other}: 정답을 못 읽어 비교 못 함 → 그림으로 확인")
                continue
            state = "일치" if not diff else "✗ 다름 " + ", ".join(diff)
            more = f" (못 읽은 번호 {unread})" if unread else ""
            print(f"  · {other}: {state}{more}")
            if diff:
                bad += 1
        if args.csv:
            update_csv(args.csv, d.name, {n: v for n, v in values.items() if v is not None})
    if args.json:
        args.json.write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.csv:
        print(f"\nCSV에 적음: {args.csv}")
    if bad:
        print(f"확인할 시험 {bad}개 — 위 ✗ 줄을 그림과 맞춰 보세요.")


if __name__ == "__main__":
    main()

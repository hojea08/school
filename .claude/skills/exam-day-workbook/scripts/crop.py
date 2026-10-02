#!/usr/bin/env python3
"""시험지(또는 공식 해설지) PDF에서 문항·지문 묶음·해설을 잘라 PNG로 저장한다.

    python crop.py exams --items 14 15 20 21 22 --out problems          # 수학 낱문항
    python crop.py exams --items auto:1-20 --out problems              # 직업탐구: [6~7] 같은 지문 묶음도 함께
    python crop.py exams --items 14 15 --mode solution --pdf ans.pdf --out solutions   # 공식 해설지

exams/<시험>/<pdf> 를 읽어 <out>/<시험>/<라벨>.png 와 manifest.json 을 쓴다.
라벨은 낱문항이면 「14」, 묶음이면 「4-9」.

· 글자가 텍스트로 들어 있는 PDF만 된다(스캔본은 문항 번호를 못 찾는다).
· 수능처럼 홀수형·짝수형이 한 파일에 있으면 홀수형 쪽만 본다.
· 같은 번호가 선택과목마다 되풀이되면 처음 나온 것을 쓴다. 다른 선택과목은 --section 으로 고른다.
· 1단·2단·3단 쪽 모두 된다(문항 번호가 모이는 왼쪽 여백으로 단을 찾는다).
· PNG에 워크북에 놓일 크기를 적어 둔다(본문 글자 약 10pt, 폭 최대 515pt).
· 자동으로 안 되는 문항은 --override 로 자를 영역을 직접 준다(references/verification.md).
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import item_label, parse_item_spec  # noqa: E402

TARGET_PT = 10.2   # 워크북에서 본문 글자 크기
MAX_W = 515        # 워크북 본문 폭(pt)
Z0 = 4.5           # 원본 1pt당 픽셀
SIZE_KEY = "workbook-size-pt"   # make_workbook.py 가 읽는 PNG 글 조각
INSTRUCTION_GAP = 70  # 묶음 머리글과 첫 문항 사이가 이보다 가까우면 지문 없는 지시문으로 본다

ITEM_RE = re.compile(r"^\s*(\d{1,2})\s*\.(?!\d)")
SET_RE = re.compile(r"^\s*\[\s*(\d{1,2})\s*[~～∼〜\-–]\s*(\d{1,2})\s*\]")
SOLUTION_RE = re.compile(r"^\s*(\d{1,2})\s*\.\s*\[?\s*출제\s*의도")
NOTICE_RE = re.compile(r"확인\s*사항")
# 해설을 이어 붙일 때 여기서 멈춘다(선택과목 머리글 등)
SECTION_RE = re.compile(r"^[\[(<【•■◆\s]*(확률과\s*통계|미적분|기하|화법과\s*작문|언어와\s*매체|선택\s*과목)[\])>】\s•■◆]*$")
HEADER_WORDS = re.compile(r"학년도|학력평가|모의평가|정답|해설|문제지|영역")


# ── 쪽 분석 ──────────────────────────────────────────────────────────────

class Page:
    def __init__(self, doc, pno):
        self.pg = doc[pno]
        self.pno = pno
        self.W, self.H = self.pg.rect.width, self.pg.rect.height
        blocks = self.pg.get_text("dict")["blocks"]
        self.spans = [s for b in blocks for l in b.get("lines", [])
                      for s in l["spans"] if s["text"].strip()]
        self.lines = [("".join(s["text"] for s in l["spans"]), pymupdf.Rect(l["bbox"]))
                      for b in blocks for l in b.get("lines", []) if any(s["text"].strip() for s in l["spans"])]
        self.drawings = self.pg.get_drawings()
        self.images = [pymupdf.Rect(im["bbox"]) for im in self.pg.get_image_info()]
        self.text = self.pg.get_text()
        self.cols = [(0, self.W)]   # 단마다 (x0, x1)
        self.bounds = []            # 단 사이 경계 x

    def col_of(self, x):
        return next((i for i, (a, b) in enumerate(self.cols) if a - 1 <= x < b), len(self.cols) - 1)

    def rows(self, x0, x1, y0=0, y1=None):
        """[x0,x1) 단 안의 글자를 줄 단위로 묶는다. 각 줄 = (y, x, 글자, Rect)."""
        y1 = self.H if y1 is None else y1
        spans = sorted((s for s in self.spans
                        if x0 - 1 <= s["bbox"][0] < x1 and y0 <= s["bbox"][1] < y1),
                       key=lambda s: s["bbox"][1])
        rows = []
        for s in spans:
            if rows and s["bbox"][1] - rows[-1][0]["bbox"][1] < 3:
                rows[-1].append(s)
            else:
                rows.append([s])
        out = []
        for row in rows:
            row.sort(key=lambda s: s["bbox"][0])
            r = pymupdf.Rect(row[0]["bbox"])
            for s in row[1:]:
                r |= s["bbox"]
            out.append((r.y0, r.x0, "".join(s["text"] for s in row), r))
        return out

    def footer_top(self):
        H = self.H
        ys = [s["bbox"][1] - 12 for s in self.spans if s["bbox"][1] > 0.85 * H
              and re.fullmatch(r"\d{1,2}|/|\d{1,2}\s*/\s*\d{1,2}", s["text"].strip())]
        ys += [s["bbox"][1] - 6 for s in self.spans if s["bbox"][1] > 0.75 * H and "저작권" in s["text"]]
        return min(ys) if ys else 0.93 * H

    def header_bottom(self, first_marker_y=None):
        """쪽 머리글 아래. 문항 번호보다 위에 있는 넓은 가로줄, 또는 쪽 맨 위의 제목 상자
        (「2021학년도 4월 … 정답 및 해설」) 중 가장 아래 것."""
        limit = (first_marker_y - 2) if first_marker_y else 0.15 * self.H
        ys = [d["rect"].y1 for d in self.drawings
              if d["rect"].width > 0.6 * self.W and d["rect"].height < 4 and d["rect"].y1 < limit]
        for d in self.drawings:
            r = d["rect"]
            if r.width > 0.6 * self.W and 4 <= r.height < 0.12 * self.H and r.y1 < min(limit, 0.2 * self.H):
                inside = " ".join(s["text"] for s in self.spans if r.contains(pymupdf.Rect(s["bbox"])))
                if HEADER_WORDS.search(inside):
                    ys.append(r.y1)
        return (max(ys) + 2) if ys else 0.05 * self.H

    def notice_top(self, x0, x1, after_y):
        """「※ 확인 사항」 상자 윗변(없으면 None)."""
        for y, x, text, r in self.rows(x0, x1, after_y):
            if not NOTICE_RE.search(text):
                continue
            c = r.tl + (1, 1)
            edges = [d["rect"].y0 for d in self.drawings
                     if (d["rect"].contains(c) and d["rect"].height < 0.3 * self.H)
                     or (d["rect"].height < 1.5 and 0 <= r.y0 - d["rect"].y0 < 15
                         and d["rect"].x0 <= r.x0 and d["rect"].x1 >= r.x1)]
            return min(edges) - 4 if edges else r.y0 - 20
        return None


def form_pages(doc, pages_opt, section):
    """볼 쪽 번호(0부터). 홀수형·짝수형이 함께 있으면 홀수형만."""
    if pages_opt:
        a, b = (pages_opt.split("-") + [None])[:2]
        a, b = int(a) - 1, int(b or a) - 1
        pages = list(range(a, min(b, doc.page_count - 1) + 1))
    else:
        pages = list(range(doc.page_count))
        texts = [re.sub(r"\s", "", doc[i].get_text()) for i in pages]
        if any("홀수형" in t for t in texts):
            first_even = next((i for i, t in enumerate(texts) if "짝수형" in t and "홀수형" not in t), None)
            if first_even is not None:
                pages = pages[:first_even]
    if section:
        want = re.sub(r"\s", "", section)
        pages = [i for i in pages if want in re.sub(r"\s", "", doc[i].get_text("text", clip=pymupdf.Rect(
            0, 0, doc[i].rect.width, doc[i].rect.height * 0.15)))]
    return pages


# ── 문항 번호 찾기 ───────────────────────────────────────────────────────

def detect_columns(pages, mode, marker_re):
    """문항 번호가 모이는 왼쪽 여백 x들로 단을 나눈다(1단·2단·3단 모두).
    단 안에서 들여 쓴 「1.」 같은 것은 가까운 여백에 합친다."""
    W = pages[0].W
    xs = []
    for p in pages:
        for text, r in p.lines:
            t = text.strip()
            hit = marker_re.match(t) if mode == "solution" else (ITEM_RE.match(t) or SET_RE.match(t))
            if hit:
                xs.append(r.x0 * W / p.W)
    groups = []
    for x in sorted(xs):
        if groups and x - groups[-1][-1] <= 10:
            groups[-1].append(x)
        else:
            groups.append([x])
    margins = []
    for g in groups:
        if len(g) < 2:
            continue
        if margins and min(g) - margins[-1] < W / 5:
            continue          # 같은 단 안의 들여쓴 번호
        margins.append(min(g))
    if len(margins) < 2:
        return [(0, W)], []
    bounds = []
    for i in range(len(margins) - 1):
        nxt = margins[i + 1]
        right = sorted(s["bbox"][2] * W / p.W for p in pages for s in p.spans
                       if margins[i] - 8 <= s["bbox"][0] * W / p.W < nxt - 8 and s["bbox"][2] * W / p.W < nxt)
        edge = right[int(len(right) * 0.98)] if right else nxt - 16
        bounds.append(max(edge + 1, (edge + nxt) / 2))
    cols = list(zip([0] + bounds, bounds + [W]))
    return cols, bounds


def find_markers(pages, mode, marker_re):
    """읽는 순서(쪽 → 왼쪽 단 → 오른쪽 단)대로 문항 번호와 묶음 머리글을 찾는다."""
    cols, bounds = detect_columns(pages, mode, marker_re)
    for p in pages:
        k = p.W / pages[0].W
        p.cols = [(a * k, b * k) for a, b in cols]
        p.bounds = [b * k for b in bounds]

    def scan():
        cands = []
        for p in pages:
            for ci, (x0, x1) in enumerate(p.cols):
                for y, x, text, r in p.rows(x0, x1):
                    if mode == "solution":
                        m = marker_re.match(text)
                        if m:
                            cands.append(dict(kind="item", a=int(m.group(1)), b=int(m.group(1)),
                                              page=p, col=ci, y=y, x=x, rect=r))
                        continue
                    m = SET_RE.match(text)
                    if m:
                        cands.append(dict(kind="set", a=int(m.group(1)), b=int(m.group(2)),
                                          page=p, col=ci, y=y, x=x, rect=r))
                        continue
                    m = ITEM_RE.match(text)
                    if m:
                        cands.append(dict(kind="item", a=int(m.group(1)), b=int(m.group(1)),
                                          page=p, col=ci, y=y, x=x, rect=r))
        return cands

    cands = scan()

    # 문항 번호는 단 왼쪽 여백에 붙어 있다. 들여 쓴 「1.」(보기·조건 목록)은 버린다.
    margins = {}
    for c in cands:
        if c["kind"] == "item":
            margins.setdefault(c["col"], []).append(round(c["x"] / 2) * 2)
    mode_x = {col: statistics.mode(xs) for col, xs in margins.items() if len(xs) >= 3}
    kept = []
    for c in cands:
        mx = mode_x.get(c["col"])
        tol = 8 if c["kind"] == "item" else 14
        if mx is None or abs(c["x"] - mx) <= tol:
            kept.append(c)

    # 번호는 읽는 순서대로 커진다. 선택과목에서 되풀이되는 번호와 엉뚱한 숫자는 건너뛴다.
    kept.sort(key=lambda c: (pages.index(c["page"]), c["col"], c["y"]))
    nums = [c["a"] for c in kept if c["kind"] == "item"]
    # 시작 번호: 연속한 세 번호(n, n+1, n+2)가 처음 나오는 곳(--section 으로 23번부터 볼 때 등)
    start = next((n for i, n in enumerate(nums[:-2]) if nums[i + 1] == n + 1 and nums[i + 2] == n + 2), 1)
    out, last = [], start - 1
    for c in kept:
        if last < c["a"] <= last + 8:
            out.append(c)
            if c["kind"] == "item":
                last = c["a"]
    for i, c in enumerate(out):
        c["idx"] = i
        c["y"] = line_top(c["page"], c["rect"])
    return out


def line_top(p, r):
    """번호가 있는 줄의 실제 윗변. 분수·지수 같은 수식 조각은 글줄보다 몇 pt 위에 따로 있다."""
    x0, x1 = p.cols[p.col_of(r.x0)]
    ys = [s["bbox"][1] for s in p.spans
          if x0 - 1 <= s["bbox"][0] < x1 and s["bbox"][3] > r.y0 - 3
          and r.y0 - 14 < s["bbox"][1] < r.y1]
    return min(ys + [r.y0])


# ── 영역 계산 ─────────────────────────────────────────────────────────────

class Layout:
    def __init__(self, pages, markers):
        self.pages = pages
        self.markers = markers
        self.regions = []      # (Page, col, x0, x1, top, bottom) 읽는 순서
        for p in pages:
            # 머리글 가로줄은 문항 번호 글줄(수식 조각 말고)보다 위에 있다
            ys = [m["rect"].y0 for m in markers if m["page"] is p]
            hb = p.header_bottom(min(ys) if ys else None)
            ft = p.footer_top()
            for ci, (x0, x1) in enumerate(p.cols):
                bottom = ft
                nt = p.notice_top(x0, x1, hb)
                if nt is not None:
                    bottom = min(bottom, nt)
                self.regions.append((p, ci, x0, x1, hb, bottom))

    def region_of(self, m):
        return next(i for i, r in enumerate(self.regions) if r[0] is m["page"] and r[1] == m["col"])

    def next_marker(self, m):
        return self.markers[m["idx"] + 1] if m["idx"] + 1 < len(self.markers) else None

    def same_region_end(self, m):
        """m이 시작하는 단에서, 다음 번호 직전 또는 단 끝."""
        ri = self.region_of(m)
        nxt = self.next_marker(m)
        bottom = self.regions[ri][5]
        if nxt and self.region_of(nxt) == ri:
            bottom = min(bottom, nxt["y"] - 4)
        return ri, bottom

    def flow(self, start_m, end_m, max_regions=4, stop_sections=False):
        """start_m부터 end_m 직전까지 여러 단에 걸친 조각들."""
        segs = []
        ri = self.region_of(start_m)
        y = max(start_m["y"] - 3, self.regions[ri][4])
        end_ri = self.region_of(end_m) if end_m else None
        for k in range(max_regions):
            if ri >= len(self.regions):
                break
            p, col, x0, x1, top, bottom = self.regions[ri]
            if end_m is not None and ri == end_ri:
                bottom = min(bottom, end_m["y"] - 4)
            if stop_sections:
                for ry, rx, text, r in p.rows(x0, x1, y + 6, bottom):
                    if SECTION_RE.match(text.strip()):
                        bottom = ry - 4
                        break
            if bottom > y + 2:
                segs.append((p, x0, y, x1, bottom))
            if end_m is not None and ri >= end_ri:
                break
            if bottom < self.regions[ri][5] - 1:   # 단 중간에서 멈췄다
                break
            ri += 1
            if ri < len(self.regions):
                y = self.regions[ri][4]
        return segs

    def continuation_hint(self, m):
        """낱문항이 다음 단으로 이어지는지 의심되면 그 단의 첫 줄을 돌려준다."""
        ri, bottom = self.same_region_end(m)
        nxt = self.next_marker(m)
        if nxt is not None and self.region_of(nxt) == ri:
            return None
        if ri + 1 >= len(self.regions):
            return None
        p, col, x0, x1, top, bottom2 = self.regions[ri + 1]
        limit = nxt["y"] - 4 if nxt is not None and self.region_of(nxt) == ri + 1 else bottom2
        for y, x, text, r in p.rows(x0, x1, top, limit):
            t = text.strip()
            # 「5지선다형」「단답형」「홀수형」 같은 안내 글자와 쪽 번호는 이어지는 내용이 아니다
            if re.search(r"[가-힣A-Za-z]", t) and not re.fullmatch(r"(5\s*지\s*선다형|단\s*답\s*형|[홀짝]\s*수\s*형)", t):
                return t[:40]
        return None


# ── 그리기 ───────────────────────────────────────────────────────────────

def render_segment(p, x0, y0, x1, y1):
    """한 조각: 안의 글자·그림·선을 모아 잘라 흰 여백을 다듬는다. (그림, 한글 글자 크기들)"""
    R = pymupdf.Rect(x0, y0, x1, y1)
    boxes, sizes = [], []
    for s in p.spans:
        r = pymupdf.Rect(s["bbox"])
        if R.contains((r.tl + r.br) / 2):
            boxes.append(r)
            if re.search(r"[가-힣]", s["text"]):
                sizes.append(round(s["size"], 1))
    for d in p.drawings:
        r0 = d["rect"]
        if (r0.width < 2.5 and r0.height > 0.4 * p.H) or r0.width > 0.9 * p.W:
            continue  # 단 구분선, 머리글 가로줄
        if r0.width < 2.5 and any(abs((r0.x0 + r0.x1) / 2 - b) < 6 for b in p.bounds):
            continue  # 여러 토막으로 그린 단 구분선
        w = max(0.6, (d.get("width") or 0) / 2)
        r = pymupdf.Rect(r0.x0 - w, r0.y0 - w, r0.x1 + w, r0.y1 + w)  # 두께 0인 선도 넓이를 갖게
        if R.contains((r.tl + r.br) / 2) or (R.intersects(r) and r.y0 >= y0 - 1):
            boxes.append(r & R)
    for r in p.images:
        if R.contains((r.tl + r.br) / 2):
            boxes.append(r & R)
    boxes = [b for b in boxes if not b.is_empty]
    if not boxes:
        return None, sizes
    U = pymupdf.Rect(boxes[0])
    for b in boxes[1:]:
        U |= b
    # 위로는 넓히지 않는다: 조각 윗변 바로 위가 쪽 머리글 가로줄인 경우가 많다
    clip = (U + (-4, -4, 4, 4)) & R
    pix = p.pg.get_pixmap(matrix=pymupdf.Matrix(Z0, Z0), clip=clip, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    bb = ImageOps.invert(img).point(lambda v: 255 if v > 24 else 0).getbbox()
    if not bb:
        return None, sizes
    m = int(3 * Z0)
    img = img.crop((max(0, bb[0] - m), max(0, bb[1] - m),
                    min(img.width, bb[2] + m), min(img.height, bb[3] + m)))
    return img, sizes


def stack(images, gap_pt=8):
    gap = int(gap_pt * Z0)
    w = max(im.width for im in images)
    h = sum(im.height for im in images) + gap * (len(images) - 1)
    out = Image.new("L", (w, h), 255)
    y = 0
    for im in images:
        out.paste(im, (0, y))
        y += im.height + gap
    return out


def save_item(path, segs):
    rendered, sizes = [], []
    for seg in segs:
        img, sz = render_segment(*seg)
        sizes += sz
        if img is not None:
            rendered.append(img)
    if not rendered:
        return None
    img = stack(rendered)
    body = statistics.median(sizes) if sizes else 10
    w_src = img.width / Z0
    scale = min(TARGET_PT / body, MAX_W / w_src)
    dpi = Z0 * 72 / scale
    w_pt, h_pt = w_src * scale, img.height / Z0 * scale
    info = PngImagePlugin.PngInfo()
    info.add_text(SIZE_KEY, f"{w_pt:.2f}x{h_pt:.2f}")   # 워크북에 놓일 크기(pt)
    img.save(path, dpi=(dpi, dpi), optimize=True, pnginfo=info)
    return round(w_pt), round(h_pt)


# ── 문항 고르기 ───────────────────────────────────────────────────────────

def plan_items(specs, layout, mode, flow_single):
    """요청한 문항마다 (라벨, 종류, 번호들, 조각들, 경고들)."""
    markers = layout.markers
    items = {m["a"]: m for m in markers if m["kind"] == "item"}
    sets = [m for m in markers if m["kind"] == "set"]

    def set_type(h):
        first = items.get(h["a"])
        if first is None:
            return "passage"
        same = layout.region_of(first) == layout.region_of(h)
        return "instruction" if same and first["y"] - h["y"] < INSTRUCTION_GAP else "passage"

    def set_segments(h):
        last = items.get(h["b"])
        if last is None:
            return None
        segs = layout.flow(h, None, max_regions=len(layout.regions))
        # 마지막 문항이 있는 단의, 그 문항 끝까지만
        ri_last, bottom = layout.same_region_end(last)
        out = []
        for (p, x0, y0, x1, y1) in segs:
            ri = next(i for i, r in enumerate(layout.regions) if r[0] is p and r[2] == x0)
            if ri < ri_last:
                out.append((p, x0, y0, x1, y1))
            elif ri == ri_last:
                out.append((p, x0, y0, x1, min(y1, bottom)))
                break
        return out

    def single_segments(m, warnings):
        if mode == "solution" or flow_single:
            return layout.flow(m, layout.next_marker(m), max_regions=4, stop_sections=True)
        ri, bottom = layout.same_region_end(m)
        p, col, x0, x1, top, _ = layout.regions[ri]
        hint = layout.continuation_hint(m)
        if hint:
            warnings.append(f"다음 단으로 이어질 수 있음(다음 단 첫 줄: 「{hint}」) → 묶음 확인, 필요하면 --flow")
        return [(p, x0, max(m["y"] - 3, top), x1, bottom)]

    wanted = []
    for spec in specs:
        kind, a, b = parse_item_spec(spec)
        if kind == "auto":
            covered = set()
            for h in sets:
                if a <= h["a"] and h["b"] <= b and set_type(h) == "passage":
                    wanted.append(("set", h["a"], h["b"]))
                    covered.update(range(h["a"], h["b"] + 1))
            wanted += [("item", n, n) for n in range(a, b + 1) if n not in covered and n in items]
        else:
            wanted.append((kind, a, b))
    wanted.sort(key=lambda w: w[1])

    plans = []
    for kind, a, b in wanted:
        label, warnings = item_label(kind, a, b), []
        if kind == "set":
            h = next((s for s in sets if s["a"] == a and s["b"] == b), None)
            if h is None:
                plans.append((label, kind, list(range(a, b + 1)), None, [f"[{a}~{b}] 머리글을 못 찾음"]))
                continue
            segs = set_segments(h)
            plans.append((label, kind, list(range(a, b + 1)), segs, warnings))
            continue
        m = items.get(a)
        if m is None:
            plans.append((label, kind, [a], None, [f"{a}번을 못 찾음"]))
            continue
        segs = single_segments(m, warnings)
        if mode == "problem":
            h = next((s for s in sets if s["a"] <= a <= s["b"] and s["idx"] < m["idx"]), None)
            if h is not None and set_type(h) == "instruction":
                first = items[h["a"]]
                ri = layout.region_of(h)
                _, _, hx0, hx1, htop, _ = layout.regions[ri]
                if a == h["a"]:
                    p, x0, y0, x1, y1 = segs[0]
                    segs[0] = (p, x0, max(h["y"] - 3, htop), x1, y1)
                else:  # 같은 지시문 아래 문항이면 지시문을 위에 붙인다
                    segs = [(h["page"], hx0, max(h["y"] - 3, htop), hx1, first["y"] - 4)] + segs
            elif h is not None:
                warnings.append(f"[{h['a']}~{h['b']}] 지문 묶음 안의 문항 → 지문이 빠짐. 묶음({h['a']}-{h['b']})으로 자르는지 확인")
        plans.append((label, kind, [a], segs, warnings))
    return plans


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("exams", type=Path, help="exams 폴더(<시험>/<pdf>)")
    ap.add_argument("--items", nargs="+", required=True, help="14 · 4-9 · auto:1-17")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pdf", default="prob.pdf", help="시험 폴더 안 PDF 이름")
    ap.add_argument("--mode", choices=["problem", "solution"], default="problem")
    ap.add_argument("--only", nargs="*", help="이 시험들만")
    ap.add_argument("--pages", help="볼 쪽 범위(1부터), 예: 1-8")
    ap.add_argument("--section", help="머리글에 이 글자가 있는 쪽만 (예: 미적분)")
    ap.add_argument("--flow", action="store_true", help="낱문항도 다음 단으로 이어서 자른다")
    ap.add_argument("--marker", help="해설 문항 머리 정규식(기본: 「14. [출제의도]」 꼴)")
    ap.add_argument("--override", type=Path, help='{"<시험>/<라벨>": [[쪽, x0, y0, x1, y1], ...]}')
    args = ap.parse_args()

    marker_re = re.compile(args.marker) if args.marker else SOLUTION_RE
    overrides = json.loads(args.override.read_text(encoding="utf-8")) if args.override else {}
    exams = sorted(d for d in args.exams.iterdir() if (d / args.pdf).exists())
    if args.only:
        exams = [d for d in exams if d.name in args.only]
    if not exams:
        raise SystemExit(f"{args.exams}/<시험>/{args.pdf} 가 없습니다.")

    problems = 0
    for d in exams:
        doc = pymupdf.open(d / args.pdf)
        page_ids = form_pages(doc, args.pages, args.section)
        pages = [Page(doc, i) for i in page_ids]
        markers = find_markers(pages, args.mode, marker_re)
        layout = Layout(pages, markers)
        plans = plan_items(args.items, layout, args.mode, args.flow)
        out_dir = args.out / d.name
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"exam": d.name, "pdf": args.pdf, "mode": args.mode, "items": []}
        notes = []
        for label, kind, numbers, segs, warnings in plans:
            key = f"{d.name}/{label}"
            if key in overrides:
                segs = [(Page(doc, s[0] - 1), s[1], s[2], s[3], s[4]) for s in overrides[key]]
                warnings = ["override 사용"]
            if not segs:
                notes.append(f"  ✗ {label}: {'; '.join(warnings)}")
                problems += 1
                continue
            size = save_item(out_dir / f"{label}.png", segs)
            if size is None:
                notes.append(f"  ✗ {label}: 빈 영역")
                problems += 1
                continue
            manifest["items"].append({
                "label": label, "kind": kind, "numbers": numbers, "size_pt": size,
                "segments": [[p.pno + 1, round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]
                             for p, x0, y0, x1, y1 in segs],
                "warnings": warnings,
            })
            for w in warnings:
                notes.append(f"  ! {label}: {w}")
        (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        labels = [it["label"] for it in manifest["items"]]
        print(f"{d.name}: {len(labels)}개 {labels}")
        for n in notes:
            print(n)
    if problems:
        print(f"\n못 자른 항목 {problems}개 — 위 ✗ 줄을 보세요.")


if __name__ == "__main__":
    main()

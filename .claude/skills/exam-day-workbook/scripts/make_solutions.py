#!/usr/bin/env python3
"""공식 해설지에서 자른 해설을 워크북과 같은 순서(#001 …)로 묶어 해설 PDF를 만든다.

    python make_solutions.py 학평.json                 # 설정의 모든 반
    python make_solutions.py 학평.json --only PRO반

시험을 낸 기관이 직접 펴낸 해설지(교육청 「정답 및 해설」 등)만 쓴다. 평가원·사관학교처럼
공식 해설이 없는 시험은 해설 PDF를 만들지 않는다. 해설을 새로 쓰지도 않는다.

설정(JSON)에 더 넣는 것:
  solutions        crop.py --mode solution 출력 폴더 (<시험>/<번호>.png)
  solution_source  해설 출처, 예: 「각 시·도 교육청 정답 및 해설」 — 없으면 만들지 않는다
  solution_skip    (선택) 공식 해설이 없는 시험 목록, 예: ["2022-사관", "2023-사관"]
"""

import argparse
import json
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_workbook as wb  # noqa: E402
from common import exam_label, label_numbers, register_fonts  # noqa: E402

PAGE_W, PAGE_H = A4
LEFT, RIGHT = 40, PAGE_W - 40
TOP_Y, BOTTOM_Y = 60, 796
GUTTER = 14
COL_W = (RIGHT - LEFT - GUTTER) / 2


def top(y):
    return PAGE_H - y


def width_pt(path):
    return wb.natural_size(wb.Image.open(path))[0]


class Writer:
    """DAY마다 새 쪽. 해설 그림이 모두 좁으면(3단 해설지) 2단으로, 아니면 1단으로 흘려 놓는다."""

    def __init__(self, path, title, footer):
        self.c = canvas.Canvas(str(path), pagesize=A4)
        self.c.setTitle(title)
        self.c.setAuthor("")
        self.footer = footer
        self.page_no = 0
        self.cols = [LEFT]
        self.col = 0
        self.y = TOP_Y
        self.top_y = TOP_Y

    def new_page(self, top_y=TOP_Y):
        if self.page_no:
            self.c.showPage()
        self.page_no += 1
        self.col, self.y, self.top_y = 0, top_y, top_y
        c = self.c
        c.setFillColor(wb.FAINT_INK)
        c.setFont("P-Medium", 6.5)
        c.drawString(LEFT, top(812), self.footer)
        c.setFont("P-Regular", 7)
        c.drawCentredString(PAGE_W / 2, top(812), str(self.page_no))

    def room(self, h):
        """현재 단에 h만큼 자리가 없으면 다음 단, 없으면 다음 쪽."""
        if self.y + h <= BOTTOM_Y:
            return
        if self.col + 1 < len(self.cols):
            self.col += 1
            self.y = self.top_y
        else:
            self.new_page()

    def start_day(self, day, exam, two_col):
        self.cols = [LEFT, LEFT + COL_W + GUTTER] if two_col else [LEFT]
        self.new_page()
        c = self.c
        c.setFillColor(wb.INK)
        c.setFont("P-Bold", 14)
        c.drawString(LEFT, top(self.y + 16), f"DAY {day:02d}")
        c.setFillColor(wb.SUB_INK)
        c.setFont("P-Medium", 10)
        c.drawString(LEFT + pdfmetrics.stringWidth(f"DAY {day:02d}", "P-Bold", 14) + 10,
                     top(self.y + 16), f"{exam_label(exam)} 해설")
        c.setStrokeColor(wb.BLUE_RULE)
        c.setLineWidth(1.2)
        c.line(LEFT, top(self.y + 24), RIGHT, top(self.y + 24))
        self.y += 36
        self.top_y = self.y

    def tag(self, seq_no, text, first_h):
        # 표시만 단 끝에 홀로 남지 않게, 첫 그림 조각과 같이 들어갈 자리를 본다
        self.room(15 + first_h)
        c, x = self.c, self.cols[self.col]
        t = f"#{seq_no:03d}"
        c.setFillColor(wb.BLUE)
        c.setFont("P-Bold", 8.5)
        c.drawString(x, top(self.y + 9), t)
        c.setFillColor(wb.SUB_INK)
        c.setFont("P-Regular", 8)
        c.drawString(x + pdfmetrics.stringWidth(t, "P-Bold", 8.5) + 4, top(self.y + 9), text)
        self.y += 15

    def pieces(self, path):
        cap_w = COL_W if len(self.cols) > 1 else RIGHT - LEFT
        # DAY 첫 쪽(머리글 36pt)에서도 표시(15pt)와 함께 한 단에 들어가는 높이
        cap_h = BOTTOM_Y - TOP_Y - 36 - 15
        out = []
        for im, w, h in wb.image_pieces(path, max_h=cap_h, max_w=cap_w):
            s = min(1, cap_w / w, cap_h / h)
            out.append((im, w * s, h * s))
        return out

    def image(self, pieces):
        for im, w, h in pieces:
            self.room(h)
            self.c.drawImage(ImageReader(im), self.cols[self.col], top(self.y + h), w, h, mask="auto")
            self.y += h + 6

    def note(self, text):
        self.room(16)
        self.c.setFillColor(wb.FAINT_INK)
        self.c.setFont("P-Regular", 8)
        self.c.drawString(self.cols[self.col] + 10, top(self.y + 9), text)
        self.y += 16

    def gap(self, h):
        self.y += h

    def save(self):
        self.c.save()


def solution_path(sol_dir, exam, n):
    return next((sol_dir / exam / f"{n}.{e}" for e in ("png", "jpg", "jpeg")
                 if (sol_dir / exam / f"{n}.{e}").exists()), None)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("config", type=Path)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--solutions", type=Path, help="해설 그림 폴더(설정보다 우선)")
    args = ap.parse_args()

    cfg = wb.Config(args.config)
    raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
    source = raw.get("solution_source")
    if not source:
        raise SystemExit("solution_source(공식 해설 출처)가 설정에 없습니다. 공식 해설이 없는 시험은 해설 PDF를 만들지 않습니다.")
    sol_dir = (args.solutions or (cfg.base / raw.get("solutions", "solutions"))).resolve()
    skip = set(raw.get("solution_skip", []))
    register_fonts()

    out_dir = (args.out or cfg.base).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, specs in cfg.classes.items():
        if args.only and name not in args.only:
            continue
        out = out_dir / f"{cfg.file}_{name}_해설.pdf"
        w = Writer(out, f"{cfg.title} {name} 해설", f"{name} 해설 · 출처: {source}")
        seq_no, missing, warn = 1, [], []
        for day, exam in enumerate(cfg.schedule, 1):
            labels = wb.resolve_items(cfg, specs, exam, warn)
            if exam in skip:
                seq_no += len(labels)   # 번호는 워크북과 맞춘다
                continue
            paths = {n: solution_path(sol_dir, exam, n) for l in labels for n in label_numbers(l)}
            two_col = all(width_pt(p) <= COL_W * 1.08 for p in paths.values() if p)   # 조금 넓으면 줄인다
            w.start_day(day, exam, two_col)
            for label in labels:
                parts = [(n, w.pieces(paths[n]) if paths[n] else None) for n in label_numbers(label)]
                first_h = next((pcs[0][2] for _, pcs in parts if pcs), 16)
                w.tag(seq_no, f"[{wb.item_text(cfg, exam, label)}]", first_h)
                for n, pcs in parts:
                    if pcs is None:
                        w.note(f"{n}번 해설 그림 없음")
                        missing.append(f"{exam}/{n}")
                    else:
                        w.image(pcs)
                w.gap(10)
                seq_no += 1
        w.save()
        print(f"{out.name}: {w.page_no}쪽" + (f", 건너뛴 시험 {len(skip & set(cfg.schedule))}개" if skip else ""))
        for m in warn:
            print("  !", m)
        if missing:
            print(f"  · 해설 그림 없음 {len(missing)}개: {', '.join(missing[:8])}{' …' if len(missing) > 8 else ''}")


if __name__ == "__main__":
    main()

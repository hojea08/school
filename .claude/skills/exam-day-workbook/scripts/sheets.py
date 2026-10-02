#!/usr/bin/env python3
"""눈으로 검수할 그림을 만든다. 만든 PNG는 Read 도구로 열어 본다.

    python sheets.py contact problems --out sheets              # 시험마다 자른 문항 모아보기
    python sheets.py overlay exams problems --out sheets        # 시험지 쪽 위에 자른 영역 상자
    python sheets.py pdf 평가원_공통_PRO반.pdf --pages 1-3 --out sheets   # 만든 PDF 쪽

contact: 문항 위·아래가 잘렸는지, 다른 문항이 끼었는지, 그림·표가 빠졌는지 본다.
overlay: 자른 상자가 문항 전체를 덮는지, 지문 묶음이 다음 단까지 이어졌는지 본다.
"""

import argparse
import json
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import hangul_font_path  # noqa: E402

COLORS = [(220, 40, 40), (30, 110, 220), (20, 150, 70), (200, 120, 0), (150, 50, 180)]


def font(size):
    try:
        return ImageFont.truetype(hangul_font_path(), size)
    except OSError:
        return ImageFont.load_default()


def contact(problems, out, only=None, width=1300, max_h=3600):
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for d in sorted(p for p in problems.iterdir() if p.is_dir()):
        if only and d.name not in only:
            continue
        manifest = d / "manifest.json"
        items = json.loads(manifest.read_text(encoding="utf-8"))["items"] if manifest.exists() else \
            [{"label": p.stem, "warnings": []} for p in sorted(d.glob("*.png"))]
        tiles = []
        for it in items:
            path = d / f"{it['label']}.png"
            if not path.exists():
                continue
            im = Image.open(path).convert("L")
            s = min(1, (width - 20) / im.width)
            im = im.resize((int(im.width * s), int(im.height * s)))
            head = f"{d.name} / {it['label']}" + (f"   ! {'; '.join(it.get('warnings', []))}" if it.get("warnings") else "")
            tile = Image.new("RGB", (width, im.height + 44), "white")
            dr = ImageDraw.Draw(tile)
            dr.rectangle([0, 0, width - 1, 30], fill=(235, 238, 245))
            dr.text((8, 4), head[:110], fill=(200, 30, 30) if it.get("warnings") else (30, 30, 30), font=font(20))
            tile.paste(im, (10, 36))
            dr.rectangle([9, 35, 10 + im.width, 36 + im.height], outline=(180, 180, 180))
            tiles.append(tile)
        # 세로로 쌓다가 max_h를 넘으면 다음 장
        sheet, k = [], 1
        for tile in tiles + [None]:
            if tile is None or (sheet and sum(t.height for t in sheet) + tile.height > max_h):
                if sheet:
                    img = Image.new("RGB", (width, sum(t.height for t in sheet)), "white")
                    y = 0
                    for t in sheet:
                        img.paste(t, (0, y))
                        y += t.height
                    path = out / f"{d.name}_{k}.png"
                    img.save(path)
                    made.append(path)
                    k += 1
                sheet = []
            if tile is not None:
                sheet.append(tile)
    return made


def overlay(exams, problems, out, pdf_name="prob.pdf", only=None, zoom=1.0):
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for d in sorted(p for p in problems.iterdir() if (p / "manifest.json").exists()):
        if only and d.name not in only:
            continue
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        pdf = exams / d.name / man.get("pdf", pdf_name)
        if not pdf.exists():
            continue
        doc = pymupdf.open(pdf)
        boxes = {}
        for i, it in enumerate(man["items"]):
            for seg in it["segments"]:
                boxes.setdefault(seg[0], []).append((it["label"], seg[1:], COLORS[i % len(COLORS)]))
        for pno in sorted(boxes):
            page = doc[pno - 1]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            dr = ImageDraw.Draw(img)
            for label, (x0, y0, x1, y1), color in boxes[pno]:
                dr.rectangle([x0 * zoom, y0 * zoom, x1 * zoom, y1 * zoom], outline=color, width=3)
                dr.rectangle([x0 * zoom, y0 * zoom, x0 * zoom + 70, y0 * zoom + 24], fill=color)
                dr.text((x0 * zoom + 4, y0 * zoom + 2), label, fill="white", font=font(18))
            path = out / f"{d.name}_p{pno}.png"
            img.save(path)
            made.append(path)
    return made


def pdf_pages(pdf, pages, out, dpi=70):
    out.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf)
    a, b = (pages.split("-") + [None])[:2] if pages else ("1", str(doc.page_count))
    made = []
    for pno in range(int(a), min(int(b or a), doc.page_count) + 1):
        path = out / f"{Path(pdf).stem}_{pno:03d}.png"
        doc[pno - 1].get_pixmap(dpi=dpi).save(str(path))
        made.append(path)
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("contact")
    a.add_argument("problems", type=Path)
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--only", nargs="*")
    a = sub.add_parser("overlay")
    a.add_argument("exams", type=Path)
    a.add_argument("problems", type=Path)
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--pdf", default="prob.pdf")
    a.add_argument("--only", nargs="*")
    a = sub.add_parser("pdf")
    a.add_argument("pdf", type=Path)
    a.add_argument("--pages", help="예: 1-6")
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--dpi", type=int, default=70)
    args = ap.parse_args()

    if args.cmd == "contact":
        made = contact(args.problems, args.out, args.only)
    elif args.cmd == "overlay":
        made = overlay(args.exams, args.problems, args.out, args.pdf, args.only)
    else:
        made = pdf_pages(args.pdf, args.pages, args.out, args.dpi)
    for p in made:
        print(p)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""시험지 PDF에서 9~15·20~22번 문항을 잘라 problems/<시험>/<번호>.png 로 저장한다.

    exams/2021-3월/prob.pdf   (폴더 이름은 answers.csv의 「시험」 열과 같게)
    python crop_problems.py   → problems/2021-3월/9.png …

글자가 텍스트로 들어 있는 시험지 PDF(EBSi·교육청 배포본, 사관학교 문제지)만 된다.
문항 번호 「14.」의 위치를 찾아, 같은 단에서 다음 번호 직전까지를 자른다.
저장한 PNG의 DPI에는 워크북에 놓일 실제 크기가 들어 있어, make_workbook.py가
본문 글자가 약 10pt가 되도록 그대로 배치한다.
"""

import argparse
import re
import statistics
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
TARGETS = [9, 10, 11, 12, 13, 14, 15, 20, 21, 22]
TARGET_PT = 10.2  # 워크북에서 본문 글자 크기
MAX_W = 515       # 워크북 본문 폭(pt)
Z0 = 4.5          # 원본 1pt당 픽셀

NUMBER = re.compile(r"^\s*(\d{1,2})\s*\.\s*")


def page_spans(pg):
    return [s for b in pg.get_text("dict")["blocks"] for l in b.get("lines", [])
            for s in l["spans"] if s["text"].strip()]


def find_numbers(doc):
    """번호 → (쪽, x, y). 줄 맨 앞의 「n.」만 보고, 처음 나온 것을 쓴다."""
    found = {}
    for pno in range(doc.page_count):
        for b in doc[pno].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                if not l["spans"]:
                    continue
                m = NUMBER.match("".join(s["text"] for s in l["spans"]))
                if m and 1 <= int(m.group(1)) <= 30:
                    x, y = l["spans"][0]["bbox"][:2]
                    found.setdefault(int(m.group(1)), (pno, x, y))
    return found


def footer_top(pg, spans):
    """쪽 번호(「3 / 20」 등) 위까지만 쓴다. 블로그 워터마크도 그 아래에 있다."""
    H = pg.rect.height
    ys = [s["bbox"][1] for s in spans if s["bbox"][1] > 0.85 * H
          and re.fullmatch(r"\d{1,2}|/|\d{1,2}\s*/\s*\d{1,2}", s["text"].strip())]
    return min(ys) - 12 if ys else 0.9 * H


def crop_one(doc, nums, two_col, n):
    pno, x0, y0 = nums[n]
    pg = doc[pno]
    W, H = pg.rect.width, pg.rect.height
    spans = page_spans(pg)
    drawings = pg.get_drawings()
    left = x0 < W / 2
    cx0, cx1 = ((0, W / 2) if left else (W / 2, W)) if two_col else (0, W)

    top = y0 - 3
    bottom = footer_top(pg, spans)
    nxt = nums.get(n + 1)
    if nxt and nxt[0] == pno and nxt[2] > y0 + 5 and (not two_col or (nxt[1] < W / 2) == left):
        bottom = nxt[2] - 4
    # 공통 마지막 쪽의 「※ 확인 사항」 상자는 뺀다
    for s in spans:
        if re.search(r"확인\s*사항", s["text"]) and s["bbox"][1] > top:
            c = pymupdf.Rect(s["bbox"]).tl + (1, 1)
            around = [d["rect"] for d in drawings if d["rect"].contains(c) and d["rect"].height < 0.3 * H]
            bottom = min(bottom, min(r.y0 for r in around) - 4 if around else s["bbox"][1] - 20)

    R = pymupdf.Rect(cx0, top, cx1, bottom)
    boxes, sizes = [], []
    for s in spans:
        r = pymupdf.Rect(s["bbox"])
        if R.contains((r.tl + r.br) / 2):
            boxes.append(r)
            if re.search(r"[가-힣]", s["text"]):
                sizes.append(round(s["size"], 1))
    for d in drawings:
        r0 = d["rect"]
        if (r0.width < 2.5 and r0.height > 0.4 * H) or r0.width > 0.9 * W:
            continue  # 단 구분선, 머리글 가로줄
        w = max(0.6, (d.get("width") or 0) / 2)
        r = pymupdf.Rect(r0.x0 - w, r0.y0 - w, r0.x1 + w, r0.y1 + w)  # 두께 0인 선도 넓이를 갖게
        if R.contains((r.tl + r.br) / 2) or (R.intersects(r) and r.y0 >= top - 1):
            boxes.append(r & R)
    for im in pg.get_image_info():
        r = pymupdf.Rect(im["bbox"])
        if R.contains((r.tl + r.br) / 2):
            boxes.append(r & R)

    U = pymupdf.Rect(boxes[0])
    for b in boxes[1:]:
        U |= b
    clip = (U + (-4, -4, 4, 4)) & pymupdf.Rect(cx0, top - 2, cx1, bottom)
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(Z0, Z0), clip=clip, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")

    # 흰 여백 자르기 (그림 이미지가 쪽 밖까지 걸쳐 있는 경우 등)
    bb = ImageOps.invert(img).point(lambda v: 255 if v > 24 else 0).getbbox()
    if bb:
        m = int(3 * Z0)
        img = img.crop((max(0, bb[0] - m), max(0, bb[1] - m),
                        min(img.width, bb[2] + m), min(img.height, bb[3] + m)))

    body = statistics.median(sizes) if sizes else 10
    scale = min(TARGET_PT / body, MAX_W / (img.width / Z0))
    return img, Z0 * 72 / scale


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exams", type=Path, default=HERE / "exams")
    parser.add_argument("--out", type=Path, default=HERE / "problems")
    args = parser.parse_args()

    for pdf in sorted(args.exams.glob("*/prob.pdf")):
        key = pdf.parent.name
        doc = pymupdf.open(pdf)
        nums = find_numbers(doc)
        missing = [n for n in TARGETS if n not in nums]
        if missing:
            print(f"{key}: 번호를 못 찾음 {missing} (스캔본이면 직접 캡처해 넣으세요)")
        W = doc[0].rect.width
        two_col = any(x > W * 0.45 for _, x, _ in nums.values())
        (args.out / key).mkdir(parents=True, exist_ok=True)
        for n in TARGETS:
            if n in nums:
                img, dpi = crop_one(doc, nums, two_col, n)
                img.save(args.out / key / f"{n}.png", dpi=(dpi, dpi), optimize=True)
        print(f"{key}: {len(TARGETS) - len(missing)}문항")


if __name__ == "__main__":
    main()

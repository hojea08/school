#!/usr/bin/env python3
"""교육청·사관 공통 DAY 워크북 PDF 생성기.

한 DAY = 표지 1쪽(총 소요시간 · 피드백 · 빠른답안) + 문항 5쪽.
25일차 × 6쪽 = 150쪽짜리 PDF를 반별(PRO / BASIC)로 하나씩 만든다.

    python make_workbook.py            # 두 반 모두
    python make_workbook.py --only PRO # PRO반만

문항 이미지(problems/<시험>/<번호>.png)와 정답(answers.csv)은 선택이다.
없으면 문항 자리는 비워 두고, 빠른답안 칸은 손으로 채울 수 있게 빈칸으로 둔다.
"""

import argparse
import csv
from pathlib import Path

from PIL import Image
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
PAGE_W, PAGE_H = A4

# 사진 속 일차표 그대로: (학년도, 회차)
SCHEDULE = [
    ("2021", "3월"), ("2021", "4월"), ("2021", "7월"), ("2021", "10월"), ("2022", "사관"),
    ("2022", "3월"), ("2022", "4월"), ("2022", "7월"), ("2022", "10월"), ("2023", "사관"),
    ("2023", "3월"), ("2023", "4월"), ("2023", "7월"), ("2023", "10월"), ("2024", "사관"),
    ("2024", "3월"), ("2024", "5월"), ("2024", "7월"), ("2024", "10월"), ("2025", "사관"),
    ("2025", "3월"), ("2025", "5월"), ("2025", "7월"), ("2025", "10월"), ("2026", "사관"),
]

CLASSES = {
    "PRO": {
        "title": "교육청, 사관 공통 PRO반",
        "numbers": [14, 15, 20, 21, 22],
        "file": "교육청_사관_공통_PRO반.pdf",
    },
    "BASIC": {
        "title": "교육청, 사관 공통 BASIC반",
        "numbers": [9, 10, 11, 12, 13],
        "file": "교육청_사관_공통_BASIC반.pdf",
    },
}

ALL_NUMBERS = [9, 10, 11, 12, 13, 14, 15, 20, 21, 22]
LAST_MULTIPLE_CHOICE = 15  # 공통 1~15번은 5지선다
CIRCLED = {"1": "①", "2": "②", "3": "③", "4": "④", "5": "⑤"}

INK = HexColor("#1f1f1f")
SUB_INK = HexColor("#4a4a4a")
FAINT_INK = HexColor("#9a9a9a")
RULE = HexColor("#7d7d7d")
LIGHT_RULE = HexColor("#c8c8c8")
HEAD_FILL = HexColor("#d6d6d6")
SIDE_FILL = HexColor("#e6e6e6")
BLUE = HexColor("#2f4f8f")
BLUE_RULE = HexColor("#5a73a8")


def register_fonts():
    for weight in ("Regular", "Medium", "Bold"):
        path = HERE / "fonts" / f"Pretendard-{weight}.ttf"
        if not path.exists():
            raise SystemExit(
                f"글꼴이 없습니다: {path}\n"
                "Pretendard TTF(Regular/Medium/Bold)를 fonts/ 폴더에 넣어 주세요."
            )
        pdfmetrics.registerFont(TTFont(f"P-{weight}", str(path)))


def exam_label(exam):
    year, session = exam
    return f"{year}학년도 {session}"


def exam_key(exam):
    year, session = exam
    return f"{year}-{session}"


def top(y):
    """위에서부터 잰 y(pt)를 reportlab 좌표로."""
    return PAGE_H - y


def load_answers(path):
    answers = {}
    if not path.exists():
        return answers
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row.get("시험") or "").strip()
            if key:
                answers[key] = {n: (row.get(str(n)) or "").strip() for n in ALL_NUMBERS}
    return answers


def write_answers_template(path):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["일차", "시험"] + [str(n) for n in ALL_NUMBERS])
        for day, exam in enumerate(SCHEDULE, 1):
            w.writerow([day, exam_key(exam)] + [""] * len(ALL_NUMBERS))


def format_answer(number, value):
    if number <= LAST_MULTIPLE_CHOICE and value in CIRCLED:
        return CIRCLED[value]
    return value


def find_problem_image(problems_dir, exam, number):
    folder = problems_dir / exam_key(exam)
    for ext in ("png", "jpg", "jpeg", "PNG", "JPG", "JPEG"):
        path = folder / f"{number}.{ext}"
        if path.exists():
            return path
    return None


def draw_footer(c, page_no, left_text):
    c.setFillColor(FAINT_INK)
    c.setFont("P-Medium", 6.5)
    c.drawString(40, top(812), left_text)
    c.setFont("P-Regular", 7)
    c.drawCentredString(PAGE_W / 2, top(812), str(page_no))


def draw_cover(c, cls, day, exam, answers, page_no):
    left, right = 60, PAGE_W - 58

    c.setFillColor(INK)
    c.setFont("P-Bold", 24)
    c.drawString(left, top(104), cls["title"])

    c.setFont("P-Bold", 32)
    c.drawString(left, top(170), f"DAY {day:02d}")

    # 총 소요시간 칸
    box_x, box_w, box_y, box_h, label_w = 300, right - 300, 142, 30, 112
    c.setFillColor(SIDE_FILL)
    c.rect(box_x, top(box_y + box_h), label_w, box_h, stroke=0, fill=1)
    c.setStrokeColor(RULE)
    c.setLineWidth(0.8)
    c.rect(box_x, top(box_y + box_h), box_w, box_h, stroke=1, fill=0)
    c.line(box_x + label_w, top(box_y), box_x + label_w, top(box_y + box_h))
    c.setFillColor(INK)
    c.setFont("P-Medium", 10)
    c.drawCentredString(box_x + label_w / 2, top(box_y + box_h / 2 + 3.5), "총 소요시간")

    numbers = ",".join(str(n) for n in cls["numbers"])
    c.setFillColor(SUB_INK)
    c.setFont("P-Medium", 9.5)
    c.drawString(left, top(196), f"({exam_label(exam)} {numbers}번)")

    # 피드백 표
    c.setFillColor(INK)
    c.setFont("P-Medium", 8.5)
    c.drawString(left, top(252), "[피드백]")

    t_top, head_h, row_h, side_w, rows = 266, 24, 74, 26, 5
    t_bottom = t_top + head_h + row_h * rows
    t_w = right - left
    c.setFillColor(HEAD_FILL)
    c.rect(left, top(t_top + head_h), t_w, head_h, stroke=0, fill=1)
    c.setFillColor(SIDE_FILL)
    c.rect(left, top(t_bottom), side_w, row_h * rows, stroke=0, fill=1)

    c.setFillColor(INK)
    c.setFont("P-Medium", 7.5)
    c.drawCentredString(
        left + side_w + (t_w - side_w) / 2, top(t_top + head_h / 2 + 2.6),
        "틀린 이유, 대응책, 새롭게 알게된 점 등 피드백",
    )

    c.setStrokeColor(RULE)
    c.setLineWidth(1.0)
    c.line(left, top(t_top), right, top(t_top))
    c.line(left, top(t_bottom), right, top(t_bottom))
    c.setLineWidth(0.6)
    c.line(left, top(t_top + head_h), right, top(t_top + head_h))
    c.line(left + side_w, top(t_top), left + side_w, top(t_bottom))
    for i in range(1, rows):
        y = t_top + head_h + row_h * i
        c.line(left, top(y), right, top(y))

    c.setFont("P-Medium", 8)
    for i in range(rows):
        y = t_top + head_h + row_h * i + row_h / 2 + 2.8
        c.drawCentredString(left + side_w / 2, top(y), str(i + 1))

    # 빠른답안
    c.setFont("P-Medium", 8.5)
    c.drawRightString(right, top(706), "[빠른답안]")
    a_x, a_y, a_h = 285, 716, 36
    a_w = right - a_x
    c.setStrokeColor(RULE)
    c.setLineWidth(0.8)
    c.rect(a_x, top(a_y + a_h), a_w, a_h, stroke=1, fill=0)

    exam_answers = answers.get(exam_key(exam), {})
    values = [format_answer(n, exam_answers.get(n, "")) for n in cls["numbers"]]
    baseline = top(a_y + a_h / 2 + 3.6)
    if all(values):
        c.setFillColor(INK)
        c.setFont("P-Medium", 10.5)
        c.drawCentredString(a_x + a_w / 2, baseline, " / ".join(values))
    else:
        # 정답이 비어 있으면 직접 적을 수 있게 칸만 나눠 둔다
        slot = a_w / len(values)
        c.setFont("P-Medium", 10.5)
        for i, v in enumerate(values):
            cx = a_x + slot * (i + 0.5)
            if v:
                c.setFillColor(INK)
                c.drawCentredString(cx, baseline, v)
            if i:
                c.setFillColor(FAINT_INK)
                c.drawCentredString(a_x + slot * i, baseline, "/")

    draw_footer(c, page_no, cls["short"])


def draw_check_header(c, left, right):
    """사진처럼 체크 항목 네 개를 줄 끝까지 고르게 펼친다."""
    y, size, box = top(40), 8, 6.6
    title = "✓학습체크 표시"
    steps = ["1. 구하는 것 확인", "2. 조건 해석하기", "3. 연결 설계", "4. 피드백"]

    title_w = pdfmetrics.stringWidth(title, "P-Bold", size)
    step_ws = [pdfmetrics.stringWidth(s, "P-Medium", size) + 3.5 + box for s in steps]
    gap = (right - left - title_w - sum(step_ws)) / len(steps)

    c.setFillColor(BLUE)
    c.setFont("P-Bold", size)
    c.drawString(left, y, title)
    x = left + title_w
    c.setFont("P-Medium", size)
    for step, step_w in zip(steps, step_ws):
        c.setFillColor(LIGHT_RULE)
        c.drawCentredString(x + gap / 2, y, "|")
        x += gap
        c.setFillColor(SUB_INK)
        c.drawString(x, y, step)
        c.setStrokeColor(SUB_INK)
        c.setLineWidth(0.5)
        c.rect(x + step_w - box, y - 0.6, box, box, stroke=1, fill=0)
        x += step_w

    c.setStrokeColor(BLUE_RULE)
    c.setLineWidth(1.6)
    c.line(left, top(49), right, top(49))


PROBLEM_W = 292
PROBLEM_MAX_W = PAGE_W - 80
PROBLEM_MAX_H = 720
CROPPED_MIN_DPI = 200  # crop_problems.py가 저장한 이미지는 DPI에 실제 크기가 들어 있다


def problem_image_size(path):
    with Image.open(path) as im:
        iw, ih = im.size
        dpi = (im.info.get("dpi") or (0, 0))[0]
    if dpi >= CROPPED_MIN_DPI:
        w, h = iw * 72 / dpi, ih * 72 / dpi
    else:  # 캡처 화면 등: 왼쪽 문항 칸 폭에 맞춘다
        w, h = PROBLEM_W, PROBLEM_W * ih / iw
    fit = min(1, PROBLEM_MAX_W / w, PROBLEM_MAX_H / h)
    return w * fit, h * fit


def draw_problem(c, cls, seq_no, exam, number, page_no):
    left, right = 40, PAGE_W - 40
    draw_check_header(c, left, right)

    tag = f"#{seq_no:03d}"
    c.setFillColor(BLUE)
    c.setFont("P-Bold", 8)
    c.drawString(left, top(66), tag)
    c.setFillColor(SUB_INK)
    c.setFont("P-Regular", 7.5)
    c.drawString(
        left + pdfmetrics.stringWidth(tag, "P-Bold", 8) + 4, top(66),
        f"[{exam_label(exam)} {number}번]",
    )

    image = find_problem_image(cls["problems"], exam, number)
    area_y = 76
    if image:
        w, h = problem_image_size(image)
        c.drawImage(ImageReader(str(image)), left, top(area_y + h), w, h, mask="auto")
    else:
        h = 210
        c.setStrokeColor(LIGHT_RULE)
        c.setLineWidth(0.6)
        c.setDash(3, 3)
        c.rect(left, top(area_y + h), PROBLEM_W, h, stroke=1, fill=0)
        c.setDash()
        c.setFillColor(FAINT_INK)
        c.setFont("P-Medium", 10)
        cx = left + PROBLEM_W / 2
        c.drawCentredString(cx, top(area_y + h / 2 - 2), f"{exam_label(exam)} {number}번")
        c.setFont("P-Regular", 7.5)
        c.drawCentredString(cx, top(area_y + h / 2 + 12), "문항 붙이는 자리")

    draw_footer(c, page_no, cls["short"])


def build(cls, out_path, answers):
    c = canvas.Canvas(str(out_path), pagesize=A4)
    c.setTitle(cls["title"])
    c.setAuthor("")
    c.setSubject("DAY별 " + ", ".join(str(n) for n in cls["numbers"]) + "번 묶음")

    page_no, seq_no = 1, 1
    for day, exam in enumerate(SCHEDULE, 1):
        key = f"day{day:02d}"
        c.bookmarkPage(key)
        c.addOutlineEntry(f"DAY {day:02d} · {exam_label(exam)}", key, level=0)
        draw_cover(c, cls, day, exam, answers, page_no)
        c.showPage()
        page_no += 1

        for number in cls["numbers"]:
            key = f"q{seq_no:03d}"
            c.bookmarkPage(key)
            c.addOutlineEntry(f"#{seq_no:03d} {number}번", key, level=1)
            draw_problem(c, cls, seq_no, exam, number, page_no)
            c.showPage()
            page_no += 1
            seq_no += 1

    c.save()
    return page_no - 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=sorted(CLASSES), help="한 반만 만들기")
    parser.add_argument("--out", type=Path, default=HERE, help="PDF를 저장할 폴더")
    parser.add_argument("--answers", type=Path, default=HERE / "answers.csv")
    parser.add_argument("--problems", type=Path, default=HERE / "problems",
                        help="문항 이미지 폴더 (problems/<시험>/<번호>.png)")
    args = parser.parse_args()

    register_fonts()
    if not args.answers.exists():
        write_answers_template(args.answers)
        print(f"정답 입력용 빈 표를 만들었습니다: {args.answers}")
    answers = load_answers(args.answers)

    args.out.mkdir(parents=True, exist_ok=True)
    for name, cls in CLASSES.items():
        if args.only and name != args.only:
            continue
        cls = dict(cls, short=f"{name}반", problems=args.problems)
        out = args.out / cls["file"]
        pages = build(cls, out, answers)
        print(f"{out.name}: {pages}쪽")


if __name__ == "__main__":
    main()

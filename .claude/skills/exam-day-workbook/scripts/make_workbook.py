#!/usr/bin/env python3
"""DAY 워크북 PDF 생성기. 시험 한 회차가 DAY 하나이고, 반(문항 묶음)마다 PDF를 하나씩 만든다.

    python make_workbook.py 평가원.json                 # 설정의 모든 반
    python make_workbook.py 평가원.json --only PRO반     # 한 반만
    python make_workbook.py 평가원.json --out ~/완성본   # 저장 폴더

한 DAY = 표지 1쪽(총 소요시간 · 피드백 · 빠른답안) + 문항마다 1쪽.
문항이 한 쪽에 안 들어가면(지문 묶음 등) 빈 줄에서 나눠 다음 쪽에 「(계속)」으로 잇는다.

설정(JSON) — references/format.md 에 자세히:
  title         표지 제목. 뒤에 반 이름이 붙는다 (「평가원 공통」 + 「PRO반」)
  file          PDF 이름 앞부분 (「평가원_공통」 → 평가원_공통_PRO반.pdf)
  classes       {"PRO반": ["14","15","20","21","22"], "전체": ["auto:1-20"]}
                「14」 낱문항 · 「6-7」 지문 묶음 · 「auto:1-20」 crop.py가 찾은 묶음·낱문항 전부
  schedule      ["2027-6월", "2027-9월", …]  DAY 순서
  answers       정답 CSV (시험 열 + 번호 열). 없으면 빈 표를 만든다
  problems      crop.py 출력 폴더 (<시험>/<라벨>.png, manifest.json)
  subject       (선택) 문항 표기에 넣을 과목 이름, 예: 「공업 일반」
  short_answer  (선택) 숫자로 적힌 답을 원문자로 바꾸지 않을 번호, 예: "16-22,29-30"
  check_steps   (선택) 학습체크 네 칸 글자
  feedback_head (선택) 피드백 표 머리글

설정 파일의 상대 경로는 설정 파일이 있는 폴더 기준이다.
문항 그림과 정답은 없어도 된다. 없으면 문항 자리는 점선 칸, 빠른답안은 빈칸으로 남는다.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

from PIL import Image
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (exam_label, format_answer, label_display, label_numbers,  # noqa: E402
                    parse_item_spec, parse_numbers, register_fonts)

PAGE_W, PAGE_H = A4

INK = HexColor("#1f1f1f")
SUB_INK = HexColor("#4a4a4a")
FAINT_INK = HexColor("#9a9a9a")
RULE = HexColor("#7d7d7d")
LIGHT_RULE = HexColor("#c8c8c8")
HEAD_FILL = HexColor("#d6d6d6")
SIDE_FILL = HexColor("#e6e6e6")
BLUE = HexColor("#2f4f8f")
BLUE_RULE = HexColor("#5a73a8")

CHECK_STEPS = ["1. 구하는 것 확인", "2. 조건 해석하기", "3. 연결 설계", "4. 피드백"]
FEEDBACK_HEAD = "틀린 이유, 대응책, 새롭게 알게된 점 등 피드백"

PROBLEM_W = 292          # 그림이 없을 때 점선 칸 폭
PROBLEM_MAX_W = PAGE_W - 80
PROBLEM_MAX_H = 720      # 문항 영역 76pt ~ 796pt
SQUEEZE = 1.12           # 이만큼 넘치는 정도면 나누지 않고 줄여서 한 쪽에
SIZE_KEY = "workbook-size-pt"   # crop.py가 PNG에 적어 둔 실제 크기(pt)
MIN_DPI = 150            # 크기 기록이 없을 때: 이보다 높은 DPI면 DPI대로, 낮으면 캡처 화면으로 본다


def top(y):
    """위에서부터 잰 y(pt)를 reportlab 좌표로."""
    return PAGE_H - y


# ── 설정과 자료 ──────────────────────────────────────────────────────────

class Config:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.base = self.path.parent
        d = json.loads(self.path.read_text(encoding="utf-8"))
        self.title = d["title"]
        self.file = d.get("file", d["title"].replace(" ", "_").replace(",", ""))
        self.classes = d["classes"]
        self.schedule = d["schedule"]
        self.answers = self.base / d.get("answers", "answers.csv")
        self.problems = self.base / d.get("problems", "problems")
        self.subject = d.get("subject", "")
        self.short_answer = set(parse_numbers([d["short_answer"]])) if d.get("short_answer") else set()
        self.check_steps = d.get("check_steps", CHECK_STEPS)
        self.feedback_head = d.get("feedback_head", FEEDBACK_HEAD)

    def all_numbers(self):
        nums = set()
        for specs in self.classes.values():
            for spec in specs:
                kind, a, b = parse_item_spec(spec)
                nums.update(range(a, b + 1))
        return sorted(nums)


def load_answers(path):
    answers = {}
    if not path.exists():
        return answers
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row.get("시험") or "").strip()
            if key:
                answers[key] = {int(k): (v or "").strip() for k, v in row.items()
                                if k and k.strip().isdigit()}
    return answers


def write_answers_template(cfg):
    nums = cfg.all_numbers()
    with cfg.answers.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["일차", "시험"] + [str(n) for n in nums])
        for day, exam in enumerate(cfg.schedule, 1):
            w.writerow([day, exam] + [""] * len(nums))


def resolve_items(cfg, specs, exam, warn):
    """반의 문항 지정을 이 시험의 라벨 목록으로. auto는 crop.py의 manifest.json을 본다."""
    labels = []
    for spec in specs:
        kind, a, b = parse_item_spec(spec)
        if kind != "auto":
            labels.append(str(a) if kind == "item" else f"{a}-{b}")
            continue
        manifest = cfg.problems / exam / "manifest.json"
        if not manifest.exists():
            warn.append(f"{exam}: {spec} 는 crop.py 의 manifest.json 이 있어야 합니다 → 낱문항으로 둡니다")
            labels += [str(n) for n in range(a, b + 1)]
            continue
        items = json.loads(manifest.read_text(encoding="utf-8"))["items"]
        labels += [it["label"] for it in items if a <= min(it["numbers"]) and max(it["numbers"]) <= b]
    return labels


def find_image(cfg, exam, label):
    folder = cfg.problems / exam
    for ext in ("png", "jpg", "jpeg", "PNG", "JPG", "JPEG"):
        path = folder / f"{label}.{ext}"
        if path.exists():
            return path
    return None


def item_text(cfg, exam, label):
    subj = f" {cfg.subject}" if cfg.subject else ""
    return f"{exam_label(exam)}{subj} {label_display(label)}번"


# ── 문항 그림 나누기 ──────────────────────────────────────────────────────

def natural_size(im):
    """그림이 워크북에 놓일 크기(pt). crop.py 기록 → DPI → 캡처 화면(문항 칸 폭) 순으로."""
    iw, ih = im.size
    tag = im.info.get(SIZE_KEY)
    if tag:
        w, h = (float(v) for v in tag.split("x"))
        return w, h
    dpi = (im.info.get("dpi") or (0, 0))[0]
    if dpi >= MIN_DPI:
        return iw * 72 / dpi, ih * 72 / dpi
    return PROBLEM_W, PROBLEM_W * ih / iw


def image_pieces(path, max_h=PROBLEM_MAX_H, max_w=PROBLEM_MAX_W):
    """[(PIL 그림, 폭pt, 높이pt)] — max_h에 안 들어가면 흰 줄에서 나눈다. 조각은 모두 max_h 이하."""
    im = Image.open(path)
    im.load()
    iw, ih = im.size
    w, h = natural_size(im)
    per_pt = iw / w
    fit_w = min(1, max_w / w)
    w, h, per_pt = w * fit_w, h * fit_w, per_pt / fit_w
    if h <= max_h * SQUEEZE:
        s = min(1, max_h / h)
        return [(im, w * s, h * s)]

    gray = im.convert("L")
    white = [gray.crop((0, y, iw, y + 1)).getextrema()[0] > 235 for y in range(ih)]
    need = max(3, int(4 * per_pt))   # 4pt 이상 비어 있는 곳에서 자른다
    cap = int(max_h * per_pt)
    pieces, y0 = [], 0
    while ih - y0 > cap * SQUEEZE:
        # 쪽 아래쪽 절반에서 가장 넓은 빈 띠(문항·지문 사이)에서 자른다. 넓이가 비슷하면 아래쪽.
        runs, start = [], None
        for y in range(y0 + int(cap * 0.45), y0 + cap + 1):
            if white[y] and start is None:
                start = y
            elif not white[y] and start is not None:
                if y - start >= need:
                    runs.append((start, y))
                start = None
        if start is not None and y0 + cap + 1 - start >= need:
            runs.append((start, y0 + cap + 1))
        if runs:
            a, b = max(runs, key=lambda r: (min(r[1] - r[0], int(30 * per_pt)) // max(1, int(2 * per_pt)), r[0]))
            cut = (a + b) // 2
        else:
            cut = y0 + cap   # 빈 줄이 없으면 그냥 자른다
        pieces.append(im.crop((0, y0, iw, cut)))
        y0 = cut
        while y0 < ih - 1 and white[y0]:
            y0 += 1
    pieces.append(im.crop((0, y0, iw, ih)))
    out = []
    for p in pieces:
        pw, ph = p.size[0] / per_pt, p.size[1] / per_pt
        s = min(1, max_h / ph)
        out.append((p, pw * s, ph * s))
    return out


# ── 그리기 ───────────────────────────────────────────────────────────────

def draw_footer(c, page_no, left_text):
    c.setFillColor(FAINT_INK)
    c.setFont("P-Medium", 6.5)
    c.drawString(40, top(812), left_text)
    c.setFont("P-Regular", 7)
    c.drawCentredString(PAGE_W / 2, top(812), str(page_no))


def fit_font(text, font, size, width, smallest):
    while size > smallest and pdfmetrics.stringWidth(text, font, size) > width:
        size -= 0.25
    return size


def answer_groups(cfg, labels, exam_answers):
    """빠른답안 칸에 들어갈 조각들(문항·묶음마다 하나)."""
    groups = []
    for label in labels:
        vals = [format_answer(n, exam_answers.get(n, ""), cfg.short_answer) for n in label_numbers(label)]
        if "-" in label:
            joined = "".join(vals) if all(v in "①②③④⑤" for v in vals if v) else " ".join(vals)
            groups.append((f"{label_display(label)} {joined}" if all(vals) else "", all(vals)))
        else:
            groups.append((vals[0], bool(vals[0])))
    return groups


def draw_cover(c, cfg, cls_name, day, exam, labels, answers, page_no):
    left, right = 60, PAGE_W - 58

    c.setFillColor(INK)
    title = f"{cfg.title} {cls_name}".strip()
    c.setFont("P-Bold", fit_font(title, "P-Bold", 24, right - left, 16))
    c.drawString(left, top(104), title)

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

    numbers = ",".join(label_display(l) for l in labels) if all("-" not in l for l in labels) \
        else ", ".join(label_display(l) for l in labels)
    subj = f" {cfg.subject}" if cfg.subject else ""
    sub = f"({exam_label(exam)}{subj} {numbers}번)"
    c.setFillColor(SUB_INK)
    c.setFont("P-Medium", fit_font(sub, "P-Medium", 9.5, right - left, 7))
    c.drawString(left, top(196), sub)

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
    c.drawCentredString(left + side_w + (t_w - side_w) / 2, top(t_top + head_h / 2 + 2.6), cfg.feedback_head)

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
    groups = answer_groups(cfg, labels, answers.get(exam, {}))
    text = " / ".join(g for g, _ in groups)
    a_x, a_y, a_h = 285, 716, 36
    size = 10.5
    if all(ok for _, ok in groups):
        size = fit_font(text, "P-Medium", 10.5, right - a_x - 16, 8)
        if pdfmetrics.stringWidth(text, "P-Medium", size) > right - a_x - 16:
            a_x = left   # 길면 칸을 왼쪽 끝까지 넓힌다
            size = fit_font(text, "P-Medium", 10.5, right - a_x - 16, 6.5)
    a_w = right - a_x
    c.setFillColor(INK)
    c.setFont("P-Medium", 8.5)
    c.drawRightString(right, top(706), "[빠른답안]")
    c.setStrokeColor(RULE)
    c.setLineWidth(0.8)
    c.rect(a_x, top(a_y + a_h), a_w, a_h, stroke=1, fill=0)

    baseline = top(a_y + a_h / 2 + 3.6)
    if all(ok for _, ok in groups):
        c.setFillColor(INK)
        c.setFont("P-Medium", size)
        c.drawCentredString(a_x + a_w / 2, baseline, text)
    else:
        # 정답이 비어 있으면 직접 적을 수 있게 칸만 나눠 둔다
        slot = a_w / max(1, len(groups))
        c.setFont("P-Medium", 10.5)
        for i, (v, ok) in enumerate(groups):
            cx = a_x + slot * (i + 0.5)
            if ok:
                c.setFillColor(INK)
                c.setFont("P-Medium", fit_font(v, "P-Medium", 10.5, slot - 6, 6))
                c.drawCentredString(cx, baseline, v)
                c.setFont("P-Medium", 10.5)
            if i:
                c.setFillColor(FAINT_INK)
                c.drawCentredString(a_x + slot * i, baseline, "/")

    draw_footer(c, page_no, cls_name)


def draw_check_header(c, steps, left, right):
    """체크 항목 네 개를 줄 끝까지 고르게 펼친다."""
    y, size, box = top(40), 8, 6.6
    title = "✓학습체크 표시"

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


def draw_problem_page(c, cfg, cls_name, seq_no, exam, label, piece, page_no, cont):
    left, right = 40, PAGE_W - 40
    draw_check_header(c, cfg.check_steps, left, right)

    tag = f"#{seq_no:03d}"
    c.setFillColor(BLUE)
    c.setFont("P-Bold", 8)
    c.drawString(left, top(66), tag)
    c.setFillColor(SUB_INK)
    c.setFont("P-Regular", 7.5)
    c.drawString(left + pdfmetrics.stringWidth(tag, "P-Bold", 8) + 4, top(66),
                 f"[{item_text(cfg, exam, label)}]" + (" (계속)" if cont else ""))

    area_y = 76
    if piece is not None:
        im, w, h = piece
        c.drawImage(ImageReader(im), left, top(area_y + h), w, h, mask="auto")
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
        c.drawCentredString(cx, top(area_y + h / 2 - 2), item_text(cfg, exam, label))
        c.setFont("P-Regular", 7.5)
        c.drawCentredString(cx, top(area_y + h / 2 + 12), "문항 붙이는 자리")

    draw_footer(c, page_no, cls_name)


def build(cfg, cls_name, specs, out_path, answers):
    c = canvas.Canvas(str(out_path), pagesize=A4)
    c.setTitle(f"{cfg.title} {cls_name}".strip())
    c.setAuthor("")
    warn, missing_img, missing_ans = [], [], []

    page_no, seq_no = 1, 1
    for day, exam in enumerate(cfg.schedule, 1):
        labels = resolve_items(cfg, specs, exam, warn)
        key = f"day{day:02d}"
        c.bookmarkPage(key)
        c.addOutlineEntry(f"DAY {day:02d} · {exam_label(exam)}", key, level=0)
        draw_cover(c, cfg, cls_name, day, exam, labels, answers, page_no)
        c.showPage()
        page_no += 1
        ex_ans = answers.get(exam, {})
        for label in labels:
            if any(not ex_ans.get(n) for n in label_numbers(label)):
                missing_ans.append(f"{exam}/{label}")
            image = find_image(cfg, exam, label)
            pieces = image_pieces(image) if image else [None]
            if image is None:
                missing_img.append(f"{exam}/{label}")
            key = f"q{seq_no:03d}"
            c.bookmarkPage(key)
            c.addOutlineEntry(f"#{seq_no:03d} {label_display(label)}번", key, level=1)
            for k, piece in enumerate(pieces):
                draw_problem_page(c, cfg, cls_name, seq_no, exam, label, piece, page_no, cont=k > 0)
                c.showPage()
                page_no += 1
            seq_no += 1
    c.save()
    return page_no - 1, seq_no - 1, warn, missing_img, missing_ans


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("config", type=Path, help="워크북 설정 JSON")
    ap.add_argument("--only", nargs="*", help="이 반들만")
    ap.add_argument("--out", type=Path, help="PDF 저장 폴더(기본: 설정 파일 폴더)")
    ap.add_argument("--answers", type=Path, help="정답 CSV(설정보다 우선)")
    ap.add_argument("--problems", type=Path, help="문항 그림 폴더(설정보다 우선)")
    args = ap.parse_args()

    cfg = Config(args.config)
    if args.answers:
        cfg.answers = args.answers.resolve()
    if args.problems:
        cfg.problems = args.problems.resolve()
    register_fonts()
    if not cfg.answers.exists():
        write_answers_template(cfg)
        print(f"정답 입력용 빈 표를 만들었습니다: {cfg.answers}")
    answers = load_answers(cfg.answers)

    out_dir = (args.out or cfg.base).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, specs in cfg.classes.items():
        if args.only and name not in args.only:
            continue
        out = out_dir / f"{cfg.file}_{name}.pdf"
        pages, items, warn, no_img, no_ans = build(cfg, name, specs, out, answers)
        print(f"{out.name}: {len(cfg.schedule)}일차, {items}문항, {pages}쪽")
        for w in warn:
            print("  !", w)
        if no_img:
            print(f"  · 그림 없는 문항 {len(no_img)}개(점선 칸): {', '.join(no_img[:8])}{' …' if len(no_img) > 8 else ''}")
        if no_ans:
            print(f"  · 정답 빈칸 {len(no_ans)}개: {', '.join(no_ans[:8])}{' …' if len(no_ans) > 8 else ''}")


if __name__ == "__main__":
    main()

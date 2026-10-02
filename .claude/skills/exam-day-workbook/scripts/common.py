"""스크립트들이 같이 쓰는 도구: 내려받기, 시험 이름, 문항 지정, 정답 표기, 글꼴."""

import subprocess
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
FONT_DIR = SKILL_DIR / "assets" / "fonts"

CIRCLED = {"1": "①", "2": "②", "3": "③", "4": "④", "5": "⑤"}
# 교육청·제3자 정답표에 섞여 나오는 다른 원문자도 숫자로 돌린다
TO_DIGIT = {**{v: k for k, v in CIRCLED.items()},
            "⓵": "1", "⓶": "2", "⓷": "3", "⓸": "4", "⓹": "5", "ⓛ": "1"}
# 한글 수식 글꼴(HyhwpEQ)은 숫자를 사용자 영역 글자로 넣는다: U+E034..E03C = 1..9, U+E03D = 0
PUA_DIGITS = {chr(0xE034 + i): str((i + 1) % 10) for i in range(10)}


def decode_pua(text):
    return "".join(PUA_DIGITS.get(ch, ch) for ch in text)


# ── 내려받기 ─────────────────────────────────────────────────────────────

def fetch(url, out, referer=None, data=None, tries=6, timeout=120):
    """curl로 받는다. 프록시가 연결을 끊는 일이 잦아 지수 백오프로 다시 시도한다."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    err = ""
    for i in range(tries):
        cmd = ["curl", "-sSL", "--max-time", str(timeout), "-A", "Mozilla/5.0", "-o", str(out)]
        if referer:
            cmd += ["-e", referer]
        if data is not None:
            cmd += ["-X", "POST", "--data", data]
        cmd.append(url)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return True
        err = r.stderr.strip()
        time.sleep(min(30, 2 ** (i + 1)))
    print(f"[내려받기 실패] {url}\n  {err[:200]}", file=sys.stderr)
    if "403" in err:
        print("  → 네트워크 정책이 막고 있을 수 있습니다. references/sources.md의 '네트워크' 항목을 보세요.",
              file=sys.stderr)
    return False


def fetch_text(url, cache, referer=None, data=None, tries=6):
    if not (Path(cache).exists() and Path(cache).stat().st_size > 0):
        if not fetch(url, cache, referer, data, tries):
            return ""
    return Path(cache).read_text(encoding="utf-8", errors="ignore")


# ── 시험 이름 ────────────────────────────────────────────────────────────
# 시험 키는 "2027-6월", "2026-수능", "2022-사관" 처럼 「학년도-회차」로 쓴다.

def exam_key(year, session):
    return f"{year}-{session}"


def split_key(key):
    year, session = key.split("-", 1)
    return year, session


def exam_label(key):
    year, session = split_key(key)
    return f"{year}학년도 {session}"


# ── 문항 지정 ────────────────────────────────────────────────────────────
# "14"        한 문항
# "4-9"       [4~9] 묶음(지문 + 문항 전체)
# "auto:1-17" 1~17번 범위에서 시험지에 실제로 있는 묶음과 낱문항을 모두

def parse_item_spec(spec):
    spec = str(spec).strip()
    if spec.startswith("auto:"):
        a, b = spec[5:].split("-")
        return ("auto", int(a), int(b))
    if "-" in spec:
        a, b = spec.split("-")
        return ("set", int(a), int(b))
    return ("item", int(spec), int(spec))


def item_label(kind, a, b):
    return str(a) if kind == "item" else f"{a}-{b}"


def label_numbers(label):
    if "-" in label:
        a, b = label.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(label)]


def label_display(label):
    """「4-9」 → 「4~9」"""
    return label.replace("-", "~")


def expand_numbers(specs):
    """정답 CSV에 필요한 번호 목록."""
    nums = set()
    for s in specs:
        kind, a, b = parse_item_spec(s)
        nums.update(range(a, b + 1))
    return sorted(nums)


def parse_numbers(parts):
    """["9-15", "20-22"] 또는 ["9-15,20-22"] → [9, 10, …, 15, 20, 21, 22]"""
    nums = []
    for part in parts:
        for tok in str(part).replace(",", " ").split():
            if "-" in tok:
                a, b = tok.split("-")
                nums += range(int(a), int(b) + 1)
            elif tok:
                nums.append(int(tok))
    return sorted(set(nums))


def same_answer(a, b):
    """「③」과 「3」을 같은 답으로 본다."""
    norm = lambda v: TO_DIGIT.get(str(v).strip(), str(v).strip())
    return norm(a) == norm(b)


def format_answer(number, value, short_answer):
    """빠른답안에 찍을 글자. 원문자로 받은 답은 그대로, 숫자는 단답형이 아니면 원문자로."""
    value = str(value).strip()
    if value in TO_DIGIT and value not in CIRCLED.values():
        value = CIRCLED[TO_DIGIT[value]]   # ⓵ 같은 다른 원문자는 ①로 맞춘다
    if number not in short_answer and value in CIRCLED:
        return CIRCLED[value]
    return value


# ── 글꼴 ─────────────────────────────────────────────────────────────────

def register_fonts():
    """Pretendard(OFL)를 P-Regular / P-Medium / P-Bold 로 등록한다."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for weight in ("Regular", "Medium", "Bold"):
        path = FONT_DIR / f"Pretendard-{weight}.ttf"
        if not path.exists():
            raise SystemExit(f"글꼴이 없습니다: {path}")
        pdfmetrics.registerFont(TTFont(f"P-{weight}", str(path)))


def hangul_font_path():
    return str(FONT_DIR / "Pretendard-Bold.ttf")

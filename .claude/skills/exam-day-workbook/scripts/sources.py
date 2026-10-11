#!/usr/bin/env python3
"""시험지·정답표를 공식 게시판과 자료 블로그에서 받는다.

평가원(대학수학능력시험 홈페이지, www.suneung.re.kr)
    python sources.py kice-list --kind 모평 --area 수학                # 게시글 목록
    python sources.py kice-get  --kind 모평 --year 2027 --month 6 --area 수학 --out exams/2027-6월
    python sources.py kice-get  --kind 수능 --year 2026 --area 직업탐구 --subject "공업 일반" --out exams/2026-수능
    python sources.py kice-batch --area 수학 --years 2022-2027 --out exams
    python sources.py objections --since 2025-06                      # 이의신청 심사 결과(정답 변경 여부)

자료 블로그(교육청 학력평가·사관학교 등. 공식 게시판이 막혀 있거나 없을 때)
    python sources.py blog-find https://legendstudy.com 2021 3월 수학
    python sources.py blog-files https://legendstudy.com/1618
    python sources.py blog-get https://legendstudy.com/1618 --match 문제 --out exams/2021-3월/prob.pdf

압축 파일(탐구 영역은 과목마다 PDF가 하나씩 든 zip)
    python sources.py unzip 직업탐구영역_문제지.zip --match "공업 일반" --out exams/2027-6월/prob.pdf

kice-get 은 받은 파일을 시험 폴더에 원래 이름으로 두고, 문제지는 prob.pdf, 정답표는 official_ans.pdf 로도 복사한다.
2021학년도까지(가형·나형)는 prob_가형.pdf, prob_나형.pdf(수능은 zip 안 홀수형), official_ans_가형.pdf …로 둔다.
정답표가 한 파일이면 official_ans.pdf 하나에 쪽마다 형이 나뉘어 있다(쪽 목록을 알려 준다).
"""

import argparse
import html
import re
import shutil
import sys
import urllib.parse
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import fetch, fetch_text  # noqa: E402

KICE = "https://www.suneung.re.kr/boardCnts"
BOARDS = {"모평": ("1500236", "0403"), "수능": ("1500234", "0403")}
OBJECTION_LIST = ("https://www.kice.re.kr/boardCnts/list.do?boardID=10024&m=050102&s=kice"
                  "&searchType=S&searchStr={kw}&page={page}")
OBJECTION_VIEW = "https://www.kice.re.kr/boardCnts/view.do?boardID=10024&boardSeq={seq}&lev=0&m=050102&s=kice"
CACHE = Path(".cache")
FORMS = ("가형", "나형")   # 2021학년도까지 수학 문제지


def clean(fragment):
    text = re.sub(r"<[^>]+>", " ", html.unescape(fragment))
    return re.sub(r"\s+", " ", text).strip()


def cache_path(url):
    return CACHE / (re.sub(r"[^0-9A-Za-z가-힣]+", "_", url)[-150:] + ".html")


def page(url, fresh=False):
    path = cache_path(url)
    if fresh and path.exists():
        path.unlink()
    return fetch_text(url, path)


# ── 평가원 게시판 ─────────────────────────────────────────────────────────

def kice_posts(kind, pages=3, year=None, area=None):
    """[{seq, year, month, area, title, date}] — 모평: 학년도·월·영역, 수능: 학년도·영역.
    게시판의 학년도 거르기(C01)만 믿을 만해서, 영역은 받아 온 목록에서 거른다."""
    board, m = BOARDS[kind]
    out = {}
    for pg in range(1, pages + 1):
        url = f"{KICE}/list.do?boardID={board}&m={m}&s=suneung&page={pg}"
        if year:
            url += f"&C01={year}"
        h = page(url, fresh=True)
        n = 0
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
            seq = re.findall(r"goView\('\d+','(\d+)'", row)
            if not seq:
                continue
            n += 1
            cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            title = re.search(r'title="([^"]*)"[^>]*goView', row) or re.search(r"goView[^>]*>\s*([^<]+)<", row)
            date = next((c for c in cells if re.fullmatch(r"20\d\d[-.]\d\d[-.]\d\d", c)), "")
            yr = next((c for c in cells[1:] if re.fullmatch(r"20\d\d", c)), "")
            mon = next((c for c in cells if re.fullmatch(r"\d{1,2}월", c)), "수능" if kind == "수능" else "")
            i = cells.index(mon) + 1 if mon in cells else (cells.index(yr) + 1 if yr in cells else 2)
            ar = cells[i] if i < len(cells) else ""
            post = dict(seq=seq[0], year=yr, month=mon, area=ar,
                        title=clean(title.group(1)) if title else "", date=date)
            if area and area.replace(" ", "") not in ar.replace(" ", ""):
                continue
            out[seq[0]] = post
        if n == 0:
            break
    return list(out.values())


def kice_files(kind, seq):
    board, m = BOARDS[kind]
    view = f"{KICE}/view.do?boardID={board}&boardSeq={seq}&lev=0&m={m}&s=suneung"
    h = page(view)
    files = [(f"{KICE}/{u}", html.unescape(n).strip())
             for u, n in re.findall(r'(fileDown\.do\?fileSeq=[0-9a-f]+)[^>]*>([^<]+)', h)]
    return view, files


def classify(name):
    n = name.replace(" ", "")
    if "듣기" in n or n.lower().endswith((".mp3", ".wav")):
        return "listening"
    if "정답" in n:
        return "answer"
    if "해설" in n:
        return "solution"
    if "문제" in n:
        return "problem"
    if n.lower().endswith(".zip"):   # 「수학.zip」 「2교시_수학영역.zip」(2020·2021학년도 수능 문제지)
        return "problem"
    return "other"


def unzip_pick(zpath, match, out):
    """zip 안에서 이름에 match가 든 파일을 꺼낸다(match가 목록이면 모두 든 파일). 이름은 cp949로 저장된 경우가 많다."""
    terms = [match] if isinstance(match, str) else list(match or [])
    zf = zipfile.ZipFile(zpath)
    names = []
    for info in zf.infolist():
        name = info.filename
        if not info.flag_bits & 0x800:
            try:
                name = name.encode("cp437").decode("cp949")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        names.append(name)
        if terms and all(t.replace(" ", "") in name.replace(" ", "") for t in terms):
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(zf.read(info))
            return name, names
    return None, names


def kice_get(kind, year, month, area, out_dir, subject=None, odd=True):
    posts = kice_posts(kind, pages=3, year=year, area=area)
    want_month = "수능" if kind == "수능" else f"{int(month)}월"
    cands = [p for p in posts if p["year"] == str(year) and p["month"] == want_month]
    if not cands:
        print(f"✗ 게시글 없음: {kind} {year} {want_month} {area}")
        return False
    post = cands[0]
    view, files = kice_files(kind, post["seq"])
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{year} {want_month} {area}: {post['title']} ({post['date']}) — 첨부 {len(files)}개")
    got = {}
    for url, name in files:
        kind_ = classify(name)
        if kind_ == "listening" and not name.lower().endswith(".pdf"):
            print(f"  · 건너뜀(듣기 음원): {name}")
            continue
        dest = out_dir / name
        if not (dest.exists() and dest.stat().st_size > 0):
            if not fetch(url, dest, referer=view):
                continue
        print(f"  ↓ {name} ({dest.stat().st_size // 1024} KB)")
        got.setdefault(kind_, []).append(dest)
    ok = True
    for kind_, target in (("problem", "prob.pdf"), ("answer", "official_ans.pdf")):
        paths = got.get(kind_, [])
        if not paths:
            print(f"  ✗ {target} 로 쓸 파일이 없음")
            ok = False
            continue
        stem = Path(target).stem
        # 가형·나형 파일이 따로 있으면 형마다 prob_가형.pdf …
        forms = {f: [p for p in paths if f in p.name and p.suffix.lower() == ".pdf"] for f in FORMS}
        if any(forms.values()):
            for f, ps in forms.items():
                if ps:
                    shutil.copyfile(ps[0], out_dir / f"{stem}_{f}.pdf")
                    print(f"  → {stem}_{f}.pdf ← {ps[0].name}")
            continue
        # 홀수형 파일이 따로 있으면 그것
        paths.sort(key=lambda p: (0 if (odd and "홀수" in p.name) else 1, p.suffix != ".pdf"))
        src = paths[0]
        if src.suffix.lower() == ".zip":
            _, names = unzip_pick(src, None, None)
            zforms = [f for f in FORMS if any(f in n for n in names)]
            if zforms and not subject:
                for f in zforms:   # 수능 zip: 「수학(가형)_홀.pdf」 「2교시_수학영역_나형_홀.pdf」
                    picked, _ = unzip_pick(src, [f, "홀"], out_dir / f"{stem}_{f}.pdf")
                    print(f"  → {stem}_{f}.pdf ← {src.name} 안의 {picked}" if picked else f"  ✗ {f} 홀수형 없음: {names}")
                    ok = ok and bool(picked)
                continue
            if not subject and odd and any("홀수" in n for n in names):
                picked, _ = unzip_pick(src, "홀수", out_dir / target)   # 「수학영역_홀수형.pdf」(2022학년도 수능)
                print(f"  → {target} ← {src.name} 안의 {picked}")
                continue
            if not subject:
                print(f"  ! {src.name} 는 과목별 zip입니다. --subject 로 고르세요: {names}")
                ok = False
                continue
            picked, names = unzip_pick(src, subject, out_dir / target)
            print(f"  → {target} ← {src.name} 안의 {picked}" if picked else f"  ✗ {subject} 없음: {names}")
            ok = ok and bool(picked)
        else:
            shutil.copyfile(src, out_dir / target)
            print(f"  → {target} ← {src.name}")
            if kind_ == "answer":
                note_form_pages(out_dir / target)
    return ok


def note_form_pages(pdf):
    """정답표 한 파일에 가형·나형이 쪽마다 있으면 쪽 목록을 알려 준다(answers.py --page 에 쓴다)."""
    import pymupdf
    pages = []
    for i, pg in enumerate(pymupdf.open(pdf)):
        flat = re.sub(r"\s", "", pg.get_text())
        form = next((f for f in FORMS if f"({f})" in flat), None)
        odd = "홀수" if "(홀수)" in flat else ("짝수" if "(짝수)" in flat else "")
        if form:
            pages.append(f"{i + 1}쪽 {form}{' ' + odd if odd else ''}")
    if pages:
        print("    정답표 쪽: " + ", ".join(pages) + " → answers.py --page")


def objections(since=None, keyword=None, pages=6):
    """평가원 이의신청 심사 결과 글 목록과 본문/첨부의 결론 문장."""
    import pymupdf
    found = {}
    # 「이의신청 심사 결과」 말고 「정답 발표」로만 올라온 해도 있다(2023학년도 6월, 2024·2025학년도 모평 등)
    for kw in ("이의신청", "정답 확정", "정답 발표"):
        for pg in range(1, pages + 1):
            h = page(OBJECTION_LIST.format(kw=urllib.parse.quote(kw), page=pg), fresh=True)
            n = 0
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
                seq = re.findall(r"goView\('\d+','(\d+)'", row)
                if not seq:
                    continue
                n += 1
                cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
                date = next((c for c in cells if re.fullmatch(r"20\d\d[-.]\d\d[-.]\d\d", c)), "")
                found[seq[0]] = (max(cells, key=len), date.replace(".", "-"))
            if n == 0:
                break
    rows = sorted(found.items(), key=lambda x: x[1][1])
    for seq, (title, date) in rows:
        if since and date < since:
            continue
        if not any(w in title for w in ("결과", "확정", "정답 발표")):
            continue
        view = OBJECTION_VIEW.format(seq=seq)
        h = page(view)
        body = clean(re.sub(r"<script.*?</script>", " ", h, flags=re.S))
        text = body
        for code, name in re.findall(r"fileDown\('([0-9a-f]+)'\)[^>]*>(.*?)</a>", h, re.S):
            name = clean(name)
            if not name.lower().endswith(".pdf") or "별첨" in name:
                continue
            pdf = CACHE / f"objection_{seq}.pdf"
            if not pdf.exists():
                fetch("https://www.kice.re.kr/boardCnts/fileDown.do", pdf, referer=view, data=f"fileSeq={code}")
            try:
                text += " " + " ".join(p.get_text() for p in pymupdf.open(pdf))
            except Exception:
                pass
            break
        text = re.sub(r"\s+", " ", text)
        keys = [m.group(0).strip() for m in re.finditer(
            r"[^.○□]*(이상\s*없음|복수\s*정답|정답\s*(?:을\s*)?변경|정답\s*없음|전원\s*정답)[^.○□]*", text)]
        if keyword:
            keys = [k for k in keys if keyword in k] or keys
        print(f"{date} {title[:60]}\n    {view}")
        for k in keys[:4]:
            print(f"    → {k[:160]}")


# ── 자료 블로그 ──────────────────────────────────────────────────────────

def blog_find(site, words):
    site = site.rstrip("/")
    q = " ".join(words)
    urls = [f"{site}/search/{urllib.parse.quote(q)}", f"{site}/?s={urllib.parse.quote(q)}"]
    seen = {}
    for url in urls:
        h = page(url, fresh=True)
        for href, text in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', h, re.S):
            t = clean(text)
            href = urllib.parse.urljoin(site + "/", html.unescape(href))
            if not t or urllib.parse.urlparse(href).netloc != urllib.parse.urlparse(site).netloc:
                continue
            if all(w in t for w in words):
                seen.setdefault(href, t)
    for href, t in seen.items():
        print(f"{t[:70]}\n    {href}")
    if not seen:
        print("찾은 글 없음 — 검색어를 줄여 보세요(예: 「2021 3월」).")


def blog_files(url):
    h = page(url)
    out = []
    for m in re.finditer(r'href="([^"]+?\.(?:pdf|hwp|hwpx|zip)(?:\?[^"]*)?)"', h, re.I):
        u = urllib.parse.urljoin(url, html.unescape(m.group(1)))
        name = urllib.parse.unquote(u.split("?")[0].rsplit("/", 1)[-1])
        if u not in [x[1] for x in out]:
            out.append((name, u))
    return out


def blog_get(url, match, exclude, out):
    files = blog_files(url)
    cands = [(n, u) for n, u in files
             if all(m in n for m in match) and not any(x in n for x in exclude)]
    if not cands:
        print("✗ 맞는 첨부 없음. 첨부 목록:")
        for n, _ in files:
            print("   ", n)
        return False
    name, u = cands[0]
    if len(cands) > 1:
        print("  ! 여러 개가 맞아 첫 번째를 받습니다:", [n for n, _ in cands])
    ok = fetch(u, out, referer=url)
    if ok:
        head = Path(out).read_bytes()[:5]
        print(f"↓ {name} → {out} ({Path(out).stat().st_size // 1024} KB)"
              + ("" if head.startswith(b"%PDF") or not str(out).endswith(".pdf") else "  ✗ PDF가 아님(로그인·차단 페이지?)"))
    return ok


# ── 실행 ────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("kice-list")
    a.add_argument("--kind", choices=list(BOARDS), required=True)
    a.add_argument("--year")
    a.add_argument("--area")
    a.add_argument("--pages", type=int, default=2)

    a = sub.add_parser("kice-get")
    a.add_argument("--kind", choices=list(BOARDS), required=True)
    a.add_argument("--year", required=True)
    a.add_argument("--month", help="모평: 6 또는 9")
    a.add_argument("--area", required=True, help="게시판의 영역 이름: 수학, 직업탐구 …")
    a.add_argument("--subject", help="탐구 과목(zip 안에서 고를 이름), 예: 「성공적인 직업생활」")
    a.add_argument("--out", required=True)

    a = sub.add_parser("kice-batch")
    a.add_argument("--area", required=True)
    a.add_argument("--years", required=True, help="예: 2022-2027 (학년도)")
    a.add_argument("--sessions", default="6월,9월,수능")
    a.add_argument("--subject")
    a.add_argument("--out", required=True)

    a = sub.add_parser("objections")
    a.add_argument("--since", help="이 날짜 이후 글만, 예: 2025-06")
    a.add_argument("--keyword", help="결론 문장 중 이 낱말이 든 것 위주(예: 수학)")

    a = sub.add_parser("blog-find")
    a.add_argument("site")
    a.add_argument("words", nargs="+")

    a = sub.add_parser("blog-files")
    a.add_argument("url")

    a = sub.add_parser("blog-get")
    a.add_argument("url")
    a.add_argument("--match", nargs="+", required=True, help="파일 이름에 모두 들어 있어야 할 낱말")
    a.add_argument("--exclude", nargs="*", default=[], help="이 낱말이 든 파일은 뺀다")
    a.add_argument("--out", required=True)

    a = sub.add_parser("unzip")
    a.add_argument("zip")
    a.add_argument("--match", required=True)
    a.add_argument("--out", required=True)

    args = ap.parse_args()
    if args.cmd == "kice-list":
        for p in kice_posts(args.kind, args.pages, args.year, args.area):
            print(f"{p['seq']}  {p['year']} {p['month']:>3} {p['area']:<6} {p['date']}  {p['title'][:50]}")
    elif args.cmd == "kice-get":
        if args.kind == "모평" and not args.month:
            raise SystemExit("모평은 --month 6 또는 9")
        ok = kice_get(args.kind, args.year, args.month, args.area, args.out, args.subject)
        sys.exit(0 if ok else 1)
    elif args.cmd == "kice-batch":
        y0, y1 = (args.years.split("-") + [None])[:2]
        bad = []
        for year in range(int(y1 or y0), int(y0) - 1, -1):
            for ses in args.sessions.split(","):
                kind = "수능" if ses == "수능" else "모평"
                month = None if kind == "수능" else ses.rstrip("월")
                if not kice_get(kind, year, month, args.area, Path(args.out) / f"{year}-{ses}", args.subject):
                    bad.append(f"{year}-{ses}")
        print("\n못 받은 시험:", bad if bad else "없음")
    elif args.cmd == "objections":
        objections(args.since, args.keyword)
    elif args.cmd == "blog-find":
        blog_find(args.site, args.words)
    elif args.cmd == "blog-files":
        for n, u in blog_files(args.url):
            print(f"{n}\n    {u}")
    elif args.cmd == "blog-get":
        sys.exit(0 if blog_get(args.url, args.match, args.exclude, args.out) else 1)
    elif args.cmd == "unzip":
        picked, names = unzip_pick(args.zip, args.match, args.out)
        print(f"{picked} → {args.out}" if picked else f"✗ 없음. 들어 있는 파일: {names}")


if __name__ == "__main__":
    main()

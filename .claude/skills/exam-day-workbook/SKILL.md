---
name: exam-day-workbook
description: 기출 시험지로 「DAY별 워크북」 PDF를 만드는 스킬. 시험 한 회차가 DAY 하나이고, DAY마다 표지(총 소요시간 칸, 피드백 5줄 표, 빠른답안)와 문항 쪽(학습체크 줄, #001 [2027학년도 6월 14번], 풀이 공간)이 이어진다. 평가원 6월·9월 모의평가와 수능, 교육청 학력평가, 사관학교 기출 PDF를 공식 게시판이나 자료 블로그에서 받아 문항(지문 묶음 포함)을 자르고, 정답표를 두 출처로 대조해 빠른답안을 채운다. 해설은 시험 기관의 공식 해설지가 있을 때만 따로 묶는다. 수학(공통 4점 PRO반·BASIC반)과 직업탐구(성공적인 직업생활, 공업 일반 등) 기준으로 맞춰져 있다. 사용자가 기출 문제집, DAY 워크북, 회차별 기출 PDF, 빠른답안, 문항 잘라 붙이기, 평가원·교육청·사관 기출 정리를 말하면 이름을 대지 않아도 이 스킬을 쓴다.
---

# DAY별 기출 워크북

시험지 PDF → 문항 그림 → 정답 CSV → 워크북 PDF(+ 공식 해설이 있으면 해설 PDF)를 만든다.
형식은 사용자가 사진으로 준 「교육청, 사관 공통 PRO반」 워크북을 그대로 따른다. 세부 치수는
[references/format.md](references/format.md)에 있다.

스크립트는 이 스킬 폴더의 `scripts/`에 있다. 아래에서 `$SK`는 그 경로다
(Claude Code 저장소 안에서는 `.claude/skills/exam-day-workbook/scripts`).
필요한 패키지: `pip install pymupdf reportlab pillow`. 글꼴(Pretendard, OFL)은 `assets/fonts/`에 들어 있다.

## 먼저 지킬 것

이 규칙들은 사용자가 직접 정한 것이다. 이유와 함께 기억한다.

1. **한국어로 답한다.** 사용자는 한국어로만 대화한다.
2. **요청한 과목·범위만 다룬다.** 사용자가 다룰 과목은 수학과 직업탐구다.
   다른 과목은 "점검용"으로라도 받거나 자르지 않는다. 시키지 않은 과목을 만지면 사용자가 싫어한다.
3. **해설을 만들지 않는다.** 해설 PDF는 시험을 낸 기관이 직접 펴낸 해설지(교육청 「정답 및 해설」 등)를
   잘라 묶을 때만 만든다. 평가원과 사관학교는 공식 해설지가 없으므로 해설을 제공하지 않는다.
   EBS·학원·블로그 해설을 쓰거나 풀이를 직접 쓰는 것도 안 된다. 해설이 없으면 "공식 해설이 없어 해설지는 만들지 않았다"고 말한다.
4. **저작권 있는 자료는 공개 저장소에 올리지 않는다.** 시험지, 잘라낸 문항, 문항이 들어간 완성본 PDF는
   커밋하지 않고 `SendUserFile`로 사용자에게 직접 전달한다. 저장소에는 코드, 빈 양식 PDF, 빈 정답 CSV만 둔다.
   작업 폴더(`exams/`, `problems/`, `solutions/`)는 저장소 밖이나 `.gitignore`된 곳에 둔다.
5. **형식을 바꾸지 않는다.** 표지·문항 쪽의 배치, 글자, 색은 사진 형식 그대로다. 사용자가 바꾸라고 할 때만 설정으로 바꾼다.
6. **정답은 두 번 이상 맞춰 본다.** 정답 하나가 틀리면 워크북 전체를 못 믿게 된다.
   공식 정답표 파싱 + 정답표 그림 직접 읽기 + 다른 출처 대조 + 이의신청 결과 확인까지 한다.
7. **자른 문항은 전부 눈으로 본다.** 자동 자르기는 대부분 맞지만, 수식 윗부분, 그림, 다음 단으로 이어지는 지문은
   그림을 봐야 확인된다. 확인하지 않은 것을 "확인했다"고 쓰지 않는다.

## 작업 순서

### 0. 요청 정리

시험 묶음(평가원 / 교육청·사관), 학년도 범위, DAY 순서, 반(문항 묶음), 과목을 정한다.
알려진 기본값은 아래와 같다. 모르는 것이 결과를 바꾸면 물어본다.

| 과목 | 반과 문항 | 비고 |
|---|---|---|
| 수학 | PRO반 `14 15 20 21 22`, BASIC반 `9 10 11 12 13` | 공통 4점. 단답형 `16-22,29-30` |
| 직업탐구 | 과목 하나에 `auto:1-20` (20문항 전부) | 과목명은 zip 안 파일 이름대로. [6~7] 같은 지문 묶음이 있다 |

DAY 순서: 사용자가 정한 것을 따른다. 평가원 묶음은 최근 학년도부터 거꾸로, 한 해 안에서는
6월 → 9월 → 수능 순서였다(예: 2027-6월, 2027-9월, 2026-6월, 2026-9월, 2026-수능 …).
과목·시험별 구조는 [references/subjects.md](references/subjects.md)를 본다.

### 1. 작업 폴더

```
작업폴더/
  exams/<시험>/prob.pdf          시험지 (시험 키: 2027-6월, 2026-수능, 2023-사관, 2021-3월)
  exams/<시험>/official_ans.pdf  공식 정답표 (블로그에서 받은 것은 ans.pdf)
  problems/<시험>/<라벨>.png     자른 문항 + manifest.json
  answers.csv                    시험 열 + 번호 열
  설정.json                      워크북 설정
```

### 2. 시험지·정답표 받기 — `sources.py`

평가원은 대학수학능력시험 홈페이지(www.suneung.re.kr) 게시판에서 받는다.

```bash
python $SK/sources.py kice-batch --area 수학 --years 2022-2027 --out exams          # 6월·9월·수능 한꺼번에
python $SK/sources.py kice-get --kind 모평 --year 2027 --month 6 --area 직업탐구 \
       --subject "성공적인 직업생활" --out exams/2027-6월                          # 탐구는 과목별 zip에서 고른다
python $SK/sources.py objections --since 2025-06 --keyword 수학                     # 이의신청 심사 결과
```

교육청 학력평가와 사관학교는 공식 게시판이 막혀 있거나 없어서 자료 블로그(legendstudy.com, horaeng.com)에서 받는다.

```bash
python $SK/sources.py blog-find https://legendstudy.com 2021 3월 고3
python $SK/sources.py blog-files https://legendstudy.com/1479
python $SK/sources.py blog-get https://legendstudy.com/1479 --match 수학 문제 --out exams/2021-3월/prob.pdf
```

받은 PDF는 첫 쪽 제목을 읽어 시험·과목이 맞는지 확인한다(블로그 파일 이름이 틀린 경우가 있었다).
수능은 홀수형을 쓴다(정답표가 홀수형 기준). 게시판 주소, 네트워크가 막힐 때 할 일은
[references/sources.md](references/sources.md)에 있다.

### 3. 문항 자르기 — `crop.py`

```bash
python $SK/crop.py exams --items 9 10 11 12 13 14 15 20 21 22 --out problems   # 수학
python $SK/crop.py exams --items auto:1-20 --out problems                      # 직업탐구(지문 묶음 자동)
```

- 문항 번호(「14.」)와 묶음 머리글(「[6~7]」)을 찾아 다음 번호 직전까지 자른다. 1·2·3단 쪽을 모두 처리한다.
- 쪽 머리글·쪽 번호·「※ 확인 사항」 상자·단 구분선은 뺀다. PNG에 워크북에 놓일 크기를 적어 둔다.
- `!` 경고가 나오면 그 문항을 꼭 본다. 「지문 묶음 안의 문항」 경고는 묶음(`6-7`)이나 `auto`로 다시 자르라는 뜻이다.

그다음 **모든 문항을 눈으로 확인한다.**

```bash
python $SK/sheets.py contact problems --out sheets          # 시험마다 모아보기 → Read로 연다
python $SK/sheets.py overlay exams problems --out sheets    # 시험지 위에 자른 상자 → 의심스러운 쪽만
```

확인할 점과 고치는 법(`--flow`, `--section`, `--override`)은 [references/verification.md](references/verification.md)에 있다.

### 4. 정답 — `answers.py`

```bash
python $SK/answers.py exams --pdf official_ans.pdf --numbers 9-15 20-22 \
       --check ans.pdf ans2.pdf --render sheets/answers --csv answers.csv
```

- 평가원 정답표(번호·정답·배점 칸), 교육청 해설지 첫머리(「1 ③ 2 ⑤ …」), 사관 답안지(번호 줄 + 정답 줄)를 읽는다.
- `--render` 그림을 Read로 열어 값을 직접 읽고 CSV와 맞춘다. 글자가 없는 그림 정답표는 이 방법으로만 채운다.
- `--check`로 다른 출처와 대조한다. 다르면 그림을 보고 판단한다.
- 이의신청 결과(`sources.py objections`)에서 정답 변경·복수 정답이 있으면 CSV를 고친다(복수 정답은 `③,⑤`처럼).

### 5. 워크북 — `make_workbook.py`

설정 예시는 `assets/config-수학-평가원.json`, `assets/config-직업탐구.json`에 있다.

```bash
python $SK/make_workbook.py 설정.json --out 완성본
python $SK/sheets.py pdf 완성본/평가원_공통_PRO반.pdf --pages 1-7 --out sheets/pdf   # 표지·문항 쪽 확인
```

한 쪽에 안 들어가는 문항(지문 묶음 등)은 문항 사이의 넓은 빈 줄에서 나눠 「(계속)」 쪽으로 잇는다.
그림·정답이 없는 칸은 점선 칸과 빈칸으로 남고, 끝에 몇 개인지 알려 준다. 이 수가 0인지 확인한다.

### 6. 해설 — `make_solutions.py` (공식 해설지가 있을 때만)

```bash
python $SK/crop.py exams --mode solution --pdf ans.pdf --items 14 15 20 21 22 --out solutions
python $SK/make_solutions.py 설정.json --out 완성본
```

설정에 `solutions`, `solution_source`(출처 이름), `solution_skip`(공식 해설이 없는 시험, 예: 사관)을 넣는다.
`solution_source`가 없으면 스크립트가 만들지 않는다. 번호(#001 …)는 워크북과 같게 매긴다.

### 7. 전달

- 완성본 PDF는 `SendUserFile`로 보낸다. 공개 저장소에 커밋하지 않는다.
- 답장은 한국어로 짧게 쓴다. 무엇을 만들었는지(일차·문항·쪽 수), 무엇을 어떻게 확인했는지,
  못 한 것이나 사용자가 판단할 것이 무엇인지만 적는다.

## 설정 파일

```json
{
  "title": "평가원 공통",
  "file": "평가원_공통",
  "classes": {"PRO반": ["14", "15", "20", "21", "22"], "BASIC반": ["9", "10", "11", "12", "13"]},
  "schedule": ["2027-6월", "2027-9월", "2026-6월", "2026-9월", "2026-수능"],
  "answers": "answers.csv",
  "problems": "problems",
  "short_answer": "16-22,29-30"
}
```

`classes`의 값은 낱문항 `"14"`, 지문 묶음 `"6-7"`, 자동 `"auto:1-20"`(crop.py가 찾은 묶음·낱문항 전부)이다.
선택 항목은 `subject`(문항 표기에 과목명), `check_steps`(학습체크 네 칸), `feedback_head`(피드백 표 머리글)이다.

## 참고 문서

| 문서 | 언제 읽나 |
|---|---|
| [references/format.md](references/format.md) | 형식을 확인하거나 사용자가 형식 변경을 요청할 때 |
| [references/sources.md](references/sources.md) | 자료를 받을 때, 받기가 막힐 때 |
| [references/subjects.md](references/subjects.md) | 과목·시험 구조(문항 수, 선택과목, 묶음, 정답표 모양)가 궁금할 때 |
| [references/verification.md](references/verification.md) | 자른 문항·정답·완성본을 확인할 때, 자르기가 틀렸을 때 |

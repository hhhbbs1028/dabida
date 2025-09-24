import re
import argparse
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

DEFAULT_LIST_URL = "https://www.ebsi.co.kr/ebs/xip/xipc/previousPaperListAjax.ajax"
DEFAULT_BASE = "https://wdown.ebsi.co.kr/W61001/01exam"

# 카테고리 인덱스 → 카테고리명
CATEGORY_MAP = {
    1: "국어",
    2: "수학",
    3: "영어",
    4: "한국사",
    5: "사회탐구",
    6: "과학탐구",
    7: "직업탐구",
    8: "제2외국어",
}

TARGET_MAP = {
    1: "D100",
    2: "D200",
    3: "D300",
}

GRADE_NAME = {1: "고1", 2: "고2", 3: "고3"}

# ---------- 유틸 ----------
def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', " ", name)
    name = re.sub(r'\s+', " ", name).strip()
    return name

def extract_year(title: str) -> str:
    m = re.search(r'(\d{4})\s*년', title)
    return m.group(1) if m else "기타"

def extract_month(title: str) -> str:
    m = re.search(r'(\d{1,2})\s*월', title)
    if m:
        return f"{int(m.group(1)):02d}"
    m = re.search(r'(\d{1,2})\.\s*\d{1,2}\s*시행', title)
    if m:
        return f"{int(m.group(1)):02d}"
    m = re.search(r'(\d{1,2})\.\s*\d{1,2}', title)
    if m:
        return f"{int(m.group(1)):02d}"
    return "00"

def extract_subject_raw(title: str) -> str:
    t = title.replace("\xa0", " ")
    parts = t.strip().split()
    return parts[-1] if parts else "과목"

def normalize_subject(subj: str) -> str:
    s = (subj or "").replace(" ", "")
    roman_map = {"Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "I": "1", "II": "2", "III": "3", "IV": "4"}
    for k, v in roman_map.items():
        s = s.replace(k, v)

    m_level = re.search(r'([1-4])$', s)
    level = m_level.group(1) if m_level else ""

    base = ""
    if "생명과학" in s:
        base = "생명과학"
    elif "지구과학" in s:
        base = "지구과학"
    elif s.startswith("물리") or "물리학" in s:
        base = "물리"
    elif "화학" in s or s.startswith("화"):
        base = "화학"
    else:
        base = s

    return f"{base}{level}" if level else base

def build_abs_url(raw: str | None, base: str) -> str | None:
    if not raw:
        return None
    raw = raw.strip().strip("'").strip('"')
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return urljoin(base if base.endswith('/') else base + '/', raw.lstrip('/'))

def ext_from_url(u: str | None, default: str = ".pdf") -> str:
    if not u:
        return default
    m = re.search(r"\.([A-Za-z0-9]{1,5})(?:\?|#|$)", u)
    return f".{m.group(1)}" if m else default

def parse_list_items(html: str):
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    items = []
    container = soup.select_one("div.board_qusesion") or soup
    problem_btns = container.select('li button[onclick^="goDownLoadP("]')

    for pbtn in problem_btns:
        li = pbtn.find_parent("li")
        if not li:
            continue

        title_tag = li.select_one(".tit") or li.find("p", class_="tit")
        if title_tag:
            title = sanitize_filename(title_tag.get_text(separator=" ", strip=True))
        else:
            raw = li.get_text(separator=" ", strip=True)
            title = sanitize_filename(raw.split("  ")[0] if raw else "제목미상")

        on_p = pbtn.get("onclick", "")
        m_p = re.search(r"\(\s*(['\"])(.+?)\1\s*,", on_p)
        prob_path = m_p.group(2) if m_p else None

        hbtn = li.select_one('button[onclick^="goDownLoadH("]')
        sol_path = None
        if hbtn:
            on_h = hbtn.get("onclick", "")
            m_h = re.search(r"\(\s*(['\"])(.+?)\1\s*,", on_h)
            sol_path = m_h.group(2) if m_h else None

        if prob_path or sol_path:
            items.append((title, prob_path, sol_path))

    return items

def download_file(session: requests.Session, url: str, out_path: Path, chunk=1024*64):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with session.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        with open(out_path, "wb") as f, tqdm(
            total=total if total > 0 else None, unit="B", unit_scale=True, desc=out_path.name
        ) as pbar:
            for part in r.iter_content(chunk_size=chunk):
                if part:
                    f.write(part)
                    if total:
                        pbar.update(len(part))

def add_cookies_from_header(cookie_header: str, session: requests.Session):
    if not cookie_header:
        return
    for piece in [p.strip() for p in cookie_header.split(";") if p.strip()]:
        if "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        session.cookies.set(k.strip(), v.strip().strip('"').strip("'"))

# ---------- 메인 ----------
def main():
    ap = argparse.ArgumentParser(description="EBS Ajax 기반 페이지 순회 다운로드 (규칙형 이름 버전)")
    ap.add_argument("--list-url", default=DEFAULT_LIST_URL)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--out", default="downloads")
    ap.add_argument("--cookie-file", default=str(Path(__file__).with_name("cookie.txt")))
    ap.add_argument("--cookie", default=None)
    ap.add_argument("--debug", action="store_true")

    ap.add_argument(
        "--category",
        type=int,
        choices=range(1, 9),
        default=6,
        help="과목 대분류 선택: 1=국어, 2=수학, 3=영어, 4=한국사, 5=사회탐구, 6=과학탐구, 7=직업탐구, 8=제2외국어 (기본: 6)"
    )

    ap.add_argument(
        "--grade",    
        type=int,
        choices=range(1, 4),       # 1,2,3만 허용
        default=3,
        help="학년 선택: 1=고1, 2=고2, 3=고3 (기본: 3)"
    )

    # 기간/정렬 등은 기존 값 유지(필요하면 인자화해서 확장 가능)
    ap.add_argument("--beginYear", default="2020")
    ap.add_argument("--endYear", default="2024")
    ap.add_argument("--sort", default="recent")
    ap.add_argument("--pageSize", default="15")
    ap.add_argument("--monthList", default="03,02,04,05,06,07,09,10,11,12")

    args = ap.parse_args()

    target_cd = TARGET_MAP[args.grade]

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; EBS-Downloader/2.2)",
        "Origin": "https://www.ebsi.co.kr",
        "Referer": "https://www.ebsi.co.kr/ebs/xip/xipc/previousPaperList.ebs?targetCd={target_cd}",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })

    cookie_header = None
    if args.cookie_file and Path(args.cookie_file).exists():
        cookie_header = Path(args.cookie_file).read_text(encoding="utf-8").strip()
    elif args.cookie:
        cookie_header = args.cookie.strip()

    if cookie_header:
        add_cookies_from_header(cookie_header, session)
    else:
        print("⚠️ cookie.txt 파일을 찾지 못했거나 비어 있음 → 로그인 필요한 자료는 다운로드 불가할 수 있습니다.")

    out_root = Path(args.out)

    grade_name = GRADE_NAME[args.grade]

    # ✅ payload를 동적으로 구성 (subjList를 인자로부터)
    payload_base = {
        "beginYear": args.beginYear,
        "endYear": args.endYear,
        "targetCd": target_cd,
        "monthList": args.monthList,
        "subjList": str(args.category),  # ← 여기!
        "sort": args.sort,
        "pageSize": args.pageSize,
    }

    # 카테고리명(폴더명에 사용)
    category_name = CATEGORY_MAP.get(args.category, f"카테고리{args.category}")

    page = 1
    while True:
        data = list(payload_base.items()) + [("currentPage", str(page))]
        r = session.post(args.list_url, data=data, timeout=60)
        r.raise_for_status()
        html = r.text

        if args.debug:
            Path(f"debug_page_{page}.html").write_text(html, encoding="utf-8", errors="ignore")

        if "<li" not in html:
            print(f"페이지 {page}: 더 이상 항목 없음 → 종료")
            break

        items = parse_list_items(html)
        if not items:
            print(f"페이지 {page}: 항목 없음 → 종료")
            break

        print(f"페이지 {page}: {len(items)}건 다운로드")

        for title, prob_path, sol_path in items:
            year  = extract_year(title)
            month = extract_month(title)
            subj_raw = extract_subject_raw(title)
            subj_norm = normalize_subject(subj_raw)

            prob_url = build_abs_url(prob_path, args.base) if prob_path else None
            sol_url  = build_abs_url(sol_path,  args.base) if sol_path  else None

            prob_ext = ext_from_url(prob_url, ".pdf")
            sol_ext  = ext_from_url(sol_url,  ".pdf")

            # ✅ 폴더명 규칙: downloads/기출문제_고3_{카테고리명}_{세부과목}_{년도}
            # 예) 기출문제_고3_과학탐구_물리1_2021  /  기출문제_고3_영어_영어_2021
            target_dir = out_root / f"기출문제_{grade_name}_{category_name}_{subj_norm}_{year}"

            # 파일명 규칙: YYYY_MM_과목_문제 / YYYY_MM_과목_해설 (변경 없음)
            base_prefix = f"{year}_{month}_{subj_norm}"
            prob_name = f"{base_prefix}_문제{prob_ext}"
            sol_name  = f"{base_prefix}_해설{sol_ext}"

            try:
                if prob_url:
                    download_file(session, prob_url, target_dir / prob_name)
                else:
                    print(f"※ 문제 URL 없음: {title}")

                if sol_url:
                    download_file(session, sol_url, target_dir / sol_name)
                else:
                    print(f"※ 해설 URL 없음: {title}")

            except requests.HTTPError as e:
                print(f"HTTP 오류: {title} -> {e}")
            except Exception as e:
                print(f"다운로드 실패: {title} -> {e}")

        page += 1

    print("✅ 전체 완료")

if __name__ == "__main__":
    main()

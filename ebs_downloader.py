import re
import argparse
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

DEFAULT_LIST_URL = "https://www.ebsi.co.kr/ebs/xip/xipc/previousPaperListAjax.ajax"
DEFAULT_BASE = "https://wdown.ebsi.co.kr/W61001/01exam"

# 이번 payload 고정 값 (month(복수 키) 없이, 페이지네이션 사용)
FIXED_PAYLOAD = {
    "beginYear": "2020",
    "endYear": "2024",
    "targetCd": "D300",
    "monthList": "03,02,04,05,06,07,09,10,11,12",
    "subjList": "6",
    "sort": "recent",
    "pageSize": "15",
}

# ---------- 유틸 ----------
def sanitize_filename(name: str) -> str:
    # 파일명 금지문자 제거 + 공백 정리
    name = re.sub(r'[\\/:*?"<>|]', " ", name)
    name = re.sub(r'\s+', " ", name).strip()
    return name

def extract_year(title: str) -> str:
    m = re.search(r'(\d{4})\s*년', title)
    return m.group(1) if m else "기타"

def extract_month(title: str) -> str:
    """
    제목에서 월 추출 (두 자리, 못 찾으면 '00')
    - '10월', '3월' 패턴
    - '12.3 시행' 같은 'M.D 시행' 패턴
    - '12.3'처럼 점 표기(시행 근처)도 커버
    """
    # 1) '10월', '3월'
    m = re.search(r'(\d{1,2})\s*월', title)
    if m:
        return f"{int(m.group(1)):02d}"
    # 2) '12.3 시행' / '12.03 시행'
    m = re.search(r'(\d{1,2})\.\s*\d{1,2}\s*시행', title)
    if m:
        return f"{int(m.group(1)):02d}"
    # 3) '12.3' 근처에 '시행'이 없더라도 월로 보이는 경우(보수적)
    m = re.search(r'(\d{1,2})\.\s*\d{1,2}', title)
    if m:
        return f"{int(m.group(1)):02d}"
    return "00"

def extract_subject_raw(title: str) -> str:
    """제목 마지막 토큰을 과목 후보로 사용 (예: '물리학Ⅰ', '생명과학Ⅱ')"""
    t = title.replace("\xa0", " ")
    parts = t.strip().split()
    return parts[-1] if parts else "과목"

def normalize_subject(subj: str) -> str:
    """
    과목 표기 정규화:
      - 마지막 토큰(예: 물리학Ⅰ, 화학Ⅱ, 생명과학Ⅰ, 지구과학Ⅱ)을 표준화
      - 로마 숫자 ⅠⅡⅢⅣ → 1/2/3/4
      - '물리학' → '물리', '화학'은 그대로 '화학', '생명과학', '지구과학' 유지
      - 최종 형태: 물리1, 화학2, 생명과학1, 지구과학2
    """
    s = (subj or "").replace(" ", "")
    # 로마 숫자 → 숫자
    roman_map = {"Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "I": "1", "II": "2", "III": "3", "IV": "4"}
    for k, v in roman_map.items():
        s = s.replace(k, v)

    # 숫자(레벨) 추출 (마지막 숫자 1~4)
    m_level = re.search(r'([1-4])$', s)
    level = m_level.group(1) if m_level else ""

    # 베이스 과목명 판정
    base = ""
    if "생명과학" in s:
        base = "생명과학"
    elif "지구과학" in s:
        base = "지구과학"
    elif s.startswith("물리") or "물리학" in s:
        base = "물리"
    elif "화학" in s or s.startswith("화"):   # 과탐에서 '화'만 온 경우도 화학으로 간주
        base = "화학"
    else:
        # 예외: 그대로 반환 (숫자 붙어 있으면 유지)
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
    ap.add_argument("--cookie-file", default=None)
    ap.add_argument("--cookie", default=None)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; EBS-Downloader/2.2)",
        "Origin": "https://www.ebsi.co.kr",
        "Referer": "https://www.ebsi.co.kr/ebs/xip/xipc/previousPaperList.ebs?targetCd=D300",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })

    cookie_header = None
    if args.cookie_file:
        cookie_header = Path(args.cookie_file).read_text(encoding="utf-8").strip()
    elif args.cookie:
        cookie_header = args.cookie.strip()
    if cookie_header:
        add_cookies_from_header(cookie_header, session)

    out_root = Path(args.out)

    page = 1
    while True:
        data = list(FIXED_PAYLOAD.items()) + [("currentPage", str(page))]
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
            month = extract_month(title)          # 새로 추가
            subj_raw = extract_subject_raw(title)
            subj_norm = normalize_subject(subj_raw)

            prob_url = build_abs_url(prob_path, args.base) if prob_path else None
            sol_url  = build_abs_url(sol_path,  args.base) if sol_path  else None

            prob_ext = ext_from_url(prob_url, ".pdf")
            sol_ext  = ext_from_url(sol_url,  ".pdf")

            # ---- 폴더명 규칙: downloads/기출문제_고3_과학탐구{과목}_{년도}
            target_dir = out_root / f"기출문제_고3_과학탐구_{subj_norm}_{year}"

            # ---- 파일명 규칙: YYYY_MM_과목_문제 / YYYY_MM_과목_해설
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

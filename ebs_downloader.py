import re
import argparse
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

# ✅ 목록은 '전체 페이지(.ebs)'가 아니라 Ajax 전용 엔드포인트(.ajax)에서 내려옴
DEFAULT_LIST_URL = "https://www.ebsi.co.kr/ebs/xip/xipc/previousPaperListAjax.ajax"
# ✅ goDownLoadP/H 첫 인자(imgUrl)에 앞에 붙일 베이스(상대경로 → 절대경로)
DEFAULT_BASE = "https://wdown.ebsi.co.kr/W61001/01exam"

def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def extract_year(title: str) -> str:
    m = re.search(r'(\d{4})\s*년', title)
    return m.group(1) if m else "기타"

def extract_subject(title: str) -> str:
    t = title.replace("\xa0", " ")
    parts = t.strip().split()
    return parts[-1] if parts else "과목"

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
    """Ajax로 받은 리스트 조각에서 <li> 카드별 제목/문제/해설 추출"""
    # 파서 폴백
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # 스크립트/스타일 제거 (JS 함수 정의로 인한 오탐 방지)
    for tag in soup(["script", "style"]):
        tag.decompose()

    items = []

    # 리스트 컨테이너가 보통 여기로 들어옴
    container = soup.select_one("div.board_qusesion") or soup

    # 문제 버튼을 기준으로 상위 li를 타고 올라가 제목과 함께 수집
    problem_btns = container.select('li button[onclick^="goDownLoadP("]')

    for pbtn in problem_btns:
        li = pbtn.find_parent("li")
        if not li:
            continue

        # 제목
        title_tag = li.select_one(".tit") or li.find("p", class_="tit")
        if title_tag:
            title = sanitize_filename(title_tag.get_text(separator=" ", strip=True))
        else:
            raw = li.get_text(separator=" ", strip=True)
            title = sanitize_filename(raw.split("  ")[0] if raw else "제목미상")

        # 문제 URL (onclick 첫 번째 인자)
        on_p = pbtn.get("onclick", "")
        m_p = re.search(r"\(\s*(['\"])(.+?)\1\s*,", on_p)
        prob_path = m_p.group(2) if m_p else None

        # 같은 li의 해설 버튼
        hbtn = li.select_one('button[onclick^="goDownLoadH("]')
        sol_path = None
        if hbtn:
            on_h = hbtn.get("onclick", "")
            m_h = re.search(r"\(\s*(['\"])(.+?)\1\s*,", on_h)
            sol_path = m_h.group(2) if m_h else None

        if prob_path or sol_path:
            items.append((title, prob_path, sol_path))

    # 보강: 혹시 goDownLoadP가 없고 goDownLoadH만 있는 예외 케이스
    if not items:
        for li in container.select("li"):
            title_tag = li.select_one(".tit") or li.find("p", class_="tit")
            if not title_tag:
                continue
            title = sanitize_filename(title_tag.get_text(separator=" ", strip=True))
            pbtn = li.select_one('button[onclick^="goDownLoadP("]')
            hbtn = li.select_one('button[onclick^="goDownLoadH("]')
            def first_arg(btn):
                if not btn: return None
                m = re.search(r"\(\s*(['\"])(.+?)\1\s*,", btn.get("onclick",""))
                return m.group(2) if m else None
            prob_path = first_arg(pbtn)
            sol_path  = first_arg(hbtn)
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
    """Request Headers의 Cookie 한 줄을 세션에 주입"""
    if not cookie_header:
        return
    for piece in [p.strip() for p in cookie_header.split(";") if p.strip()]:
        if "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        k = k.strip(); v = v.strip()
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        session.cookies.set(k, v)

def main():
    ap = argparse.ArgumentParser(description="EBS Ajax(payload) 기반 문제/해설 자동 다운로드")
    ap.add_argument("--list-url", default=DEFAULT_LIST_URL, help="Ajax 목록 URL (.ajax)")
    ap.add_argument("--base", default=DEFAULT_BASE, help="상대경로 앞에 붙일 다운로드 서버 베이스 URL")
    ap.add_argument("--out", default="downloads", help="다운로드 루트 폴더")
    ap.add_argument("--debug", action="store_true", help="응답/파싱 디버그 출력")

    # Form Data (DevTools에서 XHR의 Form Data와 동일형태로 맞추세요)
    ap.add_argument("--targetCd", default="D300", help="시험 코드 (예: 고3·N수=D300)")
    ap.add_argument("--beginYear", type=int, required=True)
    ap.add_argument("--endYear", type=int, required=True)
    ap.add_argument("--monthAll", default="on", choices=["on", "off"], help='"on"/"off"')
    ap.add_argument("--monthList", default="03,02,04,05,06,07,09,10,11,12",
                    help='콤마 문자열 예: "03,02,04,05,06,07,09,10,11,12" (사이트가 보내면 그대로 넣기)')
    ap.add_argument("--month", default="03,04,06,07,09,10,11",
                    help='실제 전송은 배열로 보냄. 편의상 콤마 문자열로 받고 내부에서 배열로 변환')
    ap.add_argument("--subjList", default="6", help='과목(영역) 리스트 코드 (예: 과탐 전체="6")')
    ap.add_argument("--subj", default="6", help='과목(영역) 코드 (예: 과탐 전체="6")')
    ap.add_argument("--sort", default="recent", help='정렬 (예: recent)')
    # Ajax에서 종종 쓰는 숨은 필드
    ap.add_argument("--pageIndex", default="1")
    ap.add_argument("--searchFlag", default="Y")

    # 쿠키
    ap.add_argument("--cookie-file", default=None, help="Cookie 헤더 문자열 파일 경로")
    ap.add_argument("--cookie", default=None, help="Cookie 헤더 문자열(한 줄)")

    args = ap.parse_args()

    # 세션/헤더 (XHR 성격을 명시)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; EBS-Downloader/2.0)",
        "Origin": "https://www.ebsi.co.kr",
        "Referer": "https://www.ebsi.co.kr/ebs/xip/xipc/previousPaperList.ebs?targetCd=D300",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })

    # 쿠키 주입
    cookie_header = None
    if args.cookie_file:
        cookie_header = Path(args.cookie_file).read_text(encoding="utf-8").strip()
    elif args.cookie:
        cookie_header = args.cookie.strip()
    if cookie_header:
        add_cookies_from_header(cookie_header, session)

    # payload (사이트가 serialize하는 형식에 맞춤)
    payload = {
        "targetCd": args.targetCd,
        "beginYear": args.beginYear,
        "endYear": args.endYear,
        "monthAll": args.monthAll,     # "on"/"off"
        "monthList": args.monthList,   # 콤마 문자열
        "subjList": args.subjList,     # 문자열
        "subj": args.subj,             # 문자열
        "sort": args.sort,
        "pageIndex": args.pageIndex,
        "searchFlag": args.searchFlag,
    }

    # month는 배열 전송(= 같은 키로 여러 값). requests는 리스트값을 주면 됨 → 튜플로 구성
    month_arr = [m.strip() for m in args.month.split(",") if m.strip()]
    data = []
    for k, v in payload.items():
        data.append((k, v))
    for m in month_arr:
        data.append(("month", m))

    # POST (Ajax 전용 URL)
    r = session.post(args.list_url, data=data, timeout=60)
    r.raise_for_status()
    html = r.text

    # 디버그 출력/저장
    if args.debug:
        print("DEBUG status:", r.status_code, "| content-type:", r.headers.get("content-type"))
        print("DEBUG head 2000:\n", html[:2000])
        Path("debug_response.html").write_text(html, encoding=r.encoding or "utf-8", errors="ignore")

    if "<li" not in html:
        print("⚠️ 리스트가 비어 있거나 로그인/파라미터 문제가 있습니다. (debug_response.html 확인)")
        return

    # 파싱
    items = parse_list_items(html)
    if args.debug:
        # 간단한 카운터 로그
        try:
            soup_tmp = BeautifulSoup(html, "lxml")
        except Exception:
            soup_tmp = BeautifulSoup(html, "html.parser")
        container_tmp = soup_tmp.select_one("div.board_qusesion") or soup_tmp
        btns = container_tmp.select('li button[onclick^="goDownLoadP("]')
        print("DEBUG: problem buttons found:", len(btns))

    if not items:
        print("⚠️ 파싱된 항목이 없습니다. (HTML 구조 변경 가능) → debug_response.html 열어 확인해 주세요.")
        return

    out_root = Path(args.out)

    # 다운로드
    for title, prob_path, sol_path in items:
        year = extract_year(title)
        subject_name = sanitize_filename(extract_subject(title))

        prob_url = build_abs_url(prob_path, args.base) if prob_path else None
        sol_url = build_abs_url(sol_path, args.base) if sol_path else None

        prob_ext = ext_from_url(prob_url, ".pdf")
        sol_ext = ext_from_url(sol_url, ".pdf")

        base_name = sanitize_filename(title)
        prob_name = f"{base_name}{prob_ext}"
        sol_name = f"{base_name}_해{sol_ext}"

        target_dir = out_root / year / subject_name

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

    print("✅ 완료")

if __name__ == "__main__":
    main()

import re
import argparse
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

DEFAULT_LIST_URL = "https://www.ebsi.co.kr/ebs/xip/xipc/previousPaperList.ebs"
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
    """<li> 블록에서 제목(.tit), 문제(goDownLoadP), 해설(goDownLoadH) 추출"""
    soup = BeautifulSoup(html, "lxml")
    items = []
    for li in soup.find_all("li"):
        title_tag = li.select_one(".txt_wrap .txt_group .tit")
        if not title_tag:
            continue
        title = sanitize_filename(title_tag.get_text(separator=" ", strip=True))

        problem_btn = li.find("button", attrs={"onclick": re.compile(r"goDownLoadP\(")})
        solution_btn = li.find("button", attrs={"onclick": re.compile(r"goDownLoadH\(")})

        def first_arg(btn):
            if not btn:
                return None
            on = btn.get("onclick", "")
            m = re.search(r"\(\s*(['\"])(.+?)\1\s*,", on)
            return m.group(2) if m else None

        prob_path = first_arg(problem_btn)
        sol_path = first_arg(solution_btn)

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
    """Cookie: a=1; b=2; _fcOM={"k":"..."} 같은 한 줄 문자열을 세션 쿠키로 주입"""
    if not cookie_header:
        return
    for piece in [p.strip() for p in cookie_header.split(";") if p.strip()]:
        if "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        k = k.strip(); v = v.strip()
        # 감싸진 따옴표 제거
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        session.cookies.set(k, v)

def main():
    ap = argparse.ArgumentParser(description="EBS POST(payload) 기반 문제/해설 자동 다운로드")
    ap.add_argument("--list-url", default=DEFAULT_LIST_URL, help="목록 페이지 URL")
    ap.add_argument("--base", default=DEFAULT_BASE, help="상대경로 앞에 붙일 다운로드 서버 베이스 URL")
    ap.add_argument("--out", default="downloads", help="다운로드 루트 폴더")

    # 네가 캡처한 Form Data와 동일 형태로 받도록 설계
    ap.add_argument("--targetCd", default="D300", help="시험 코드 (기본: D300)")
    ap.add_argument("--beginYear", type=int, required=True)
    ap.add_argument("--endYear", type=int, required=True)
    ap.add_argument("--monthAll", default="on", choices=["on", "off"], help='"on" 또는 "off"(기본 on)')
    ap.add_argument("--monthList", default="03,02,04,05,06,07,09,10,11,12",
                    help='콤마 문자열 (예: "03,02,04,05,06,07,09,10,11,12")')
    ap.add_argument("--month", default="03,04,06,07,09,10,11",
                    help='응답에서 보인 배열 값과 맞추기 위해 콤마 문자열로 받고 내부에서 배열로 변환 (예: "03,04,06,07,09,10,11")')
    ap.add_argument("--subjList", default="6", help='과목(영역) 리스트 코드 (과탐 전체= "6")')
    ap.add_argument("--subj", default="6", help='과목(영역) 코드 (과탐 전체= "6")')
    ap.add_argument("--sort", default="recent", help='정렬 (기본: recent)')

    # 쿠키
    ap.add_argument("--cookie-file", default=None, help="Cookie 헤더 문자열 파일 경로(권장)")
    ap.add_argument("--cookie", default=None, help="Cookie 헤더 문자열(한 줄)")

    args = ap.parse_args()

    # 세션/헤더
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; EBS-Downloader/2.0)",
        "Referer": args.list_url,
        "Origin": "https://www.ebsi.co.kr",
    })

    # 쿠키 주입
    cookie_header = None
    if args.cookie_file:
        cookie_header = Path(args.cookie_file).read_text(encoding="utf-8").strip()
    elif args.cookie:
        cookie_header = args.cookie.strip()
    if cookie_header:
        add_cookies_from_header(cookie_header, session)

    # payload 구성 (네가 올린 Payload 형식 그대로)
    payload = {
        "targetCd": args.targetCd,
        "beginYear": args.beginYear,
        "endYear": args.endYear,
        "monthAll": args.monthAll,         # "on"/"off"
        "monthList": args.monthList,       # 콤마 문자열
        "subjList": args.subjList,         # 문자열 "6"
        "subj": args.subj,                 # 문자열 "6"
        "sort": args.sort,
    }
    # month는 배열로 전송
    month_arr = [m.strip() for m in args.month.split(",") if m.strip()]
    for m in month_arr:
        # 폼데이터 배열 전송: month=03&month=04&...
        # requests는 리스트를 값으로 주면 알아서 배열 형태로 보냄 -> dict 대신 list of tuples 사용
        pass
    # 위를 위해 실제 전송 payload를 튜플 리스트로 빌드
    data = []
    for k, v in payload.items():
        data.append((k, v))
    for m in month_arr:
        data.append(("month", m))

    # POST 요청
    r = session.post(args.list_url, data=data, timeout=60)
    r.raise_for_status()
    html = r.text

    if "<li" not in html:
        print("⚠️ 목록이 비어 있습니다. (쿠키 만료/권한/파라미터 불일치 가능)")
        return

    # 항목 파싱
    items = parse_list_items(html)
    if not items:
        print("⚠️ 파싱된 항목이 없습니다. (필터 결과 없음 또는 HTML 구조 변경)")
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

# (base) PS C:\Users\JH\coding\project\산학협력> python ebs_downloader.py `
# >>   --list-url "https://www.ebsi.co.kr/ebs/xip/xipc/previousPaperList.ebs" `
# >>   --base "https://wdown.ebsi.co.kr/W61001/01exam" `
# >>   --beginYear 2020 --endYear 2024 `
# >>   --monthAll on `
# >>   --monthList "03,02,04,05,06,07,09,10,11,12" `
# >>   --month "03,04,06,07,09,10,11" `
# >>   --subjList 6 `
# >>   --subj 6 `
# >>   --sort recent `
# >>   --cookie-file "C:\Users\JH\coding\project\산학협력\cookie.txt" `
# >>   --out "C:\Users\JH\Downloads\ebs"
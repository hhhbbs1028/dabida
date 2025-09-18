import os
import re

def rename_files_in_directory(directory):
    # 과목명 매핑 (필요하면 추가 가능)
    subject_map = {
        "물리학Ⅰ": "물리1",
        "물리학Ⅱ": "물리2",
        "화학Ⅰ": "화학1",
        "화학Ⅱ": "화학2",
        "생명과학Ⅰ": "생명과학1",
        "생명과학Ⅱ": "생명과학2",
        "지구과학Ⅰ": "지구과학1",
        "지구과학Ⅱ": "지구과학2",
    }

    for filename in os.listdir(directory):
        name, ext = os.path.splitext(filename)

        # 1) 연도 추출
        year_match = re.search(r"(20\d{2})", name)
        if not year_match:
            print(f"⚠️ 연도 못찾음 → {filename}")
            continue
        year = year_match.group(1)

        # 2) 월 추출 (없으면 12)
        month_match = re.search(r"(\d{1,2})월", name)
        if month_match:
            month = month_match.group(1).zfill(2)
        else:
            month = "12"

        # 3) 과목 추출
        subj_match = re.search(r"(물리학Ⅰ|물리학Ⅱ|화학Ⅰ|화학Ⅱ|생명과학Ⅰ|생명과학Ⅱ|지구과학Ⅰ|지구과학Ⅱ)", name)
        if not subj_match:
            print(f"⚠️ 과목 못찾음 → {filename}")
            continue
        subject = subject_map[subj_match.group(1)]

        # 4) 문제/해설 구분
        qtype = "해설" if "_해" in name or "해설" in name else "문제"

        # 최종 새 이름
        new_name = f"{year}_{month}_{subject}_{qtype}{ext}"

        old_path = os.path.join(directory, filename)
        new_path = os.path.join(directory, new_name)

        os.rename(old_path, new_path)
        print(f"{filename}  ➝  {new_name}")


# 실행 예시
rename_files_in_directory(r"C:\Users\JH\coding\dabida")

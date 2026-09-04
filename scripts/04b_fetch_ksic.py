"""
04b_fetch_ksic.py — 외부 분류 자료 수집 (KSIC 전 단계 코드·명칭, KSIC 차수 연계표, ISIC Rev.4)

왜 필요한가 (docs/DB_구축_원칙.md §4.2):
- KSSC 「연계표」 자료실이 주는 xlsx 는 **세세분류(5자리)만** 담는다.
  KSIC→ISIC 대응을 만들려면 중분류·소분류·세분류의 이름이 있어야 한다.
  ICIO 산업이 갈리는 자리(C24A/C24B, C301/C302T309, H53/J61)가 KSIC 소분류에서 결정되기 때문이다.
- 공개된 파일이 없으므로 포털의 분류검색 결과를 그대로 긁어 원본으로 보존한다.

접근 주의 (§2): https 는 인증서 주체가 맞지 않아 실패한다. http 로 받는다.

출력: data/external/ksic_<차수>_all.csv  (분류코드, 분류항목명, 대분류)

실행:
  python scripts\\04b_fetch_ksic.py                 # 11차·10차 모두
  python scripts\\04b_fetch_ksic.py --degrees 11
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import EXTERNAL_DIR, USER_AGENT, setup_logging  # noqa: E402

log = setup_logging("fetch_ksic")

URL = (
    "http://kssc.kostat.go.kr/ksscNew_web/kssc/common/IndexedSearchList.do"
    "?gubun=1&addGubun=no&strCategoryNameCode=001"
)

# 그대로 내려받는 파일 둘. (저장 이름, URL)
FILES = [
    ("KSIC11_KSIC10_연계표.xlsx",
     "http://kssc.kostat.go.kr/ksscNew_web/upload/"
     "한국표준산업분류 제11차-제10차 연계표_20240509_20240514030911.xlsx"),
    ("ISIC_Rev_4_english_structure.txt",
     "https://unstats.un.org/unsd/classifications/Econ/Download/In%20Text/"
     "ISIC_Rev_4_english_structure.Txt"),
]
# 행은 tr 의 onclick 에 (코드, 항목명, 차수)를 그대로 담고 있다. td 파싱보다 이쪽이 안전하다.
ROW_RE = re.compile(
    r"fn_Detail\('[^']*','(\d{2,5})','(.*?)','\d+'\);\"[^>]*>.*?"
    r"<td align=\"center\">\s*(.*?)\s*</td>",
    re.S,
)


def fetch_file(name: str, url: str) -> bool:
    """그대로 내려받는다. 이미 있으면 건너뛴다.

    KSSC 는 https 인증서 주체가 맞지 않아 http 로 받는다(원칙 문서 §2).
    URL 에 한글·공백이 있어 인용 처리가 필요하다.
    """
    dest = EXTERNAL_DIR / name
    if dest.exists() and dest.stat().st_size > 0:
        log.info(f"  = {name}  이미 있음 ({dest.stat().st_size:,} bytes)")
        return True
    safe = urllib.parse.quote(url, safe=":/?&=%")
    req = urllib.request.Request(safe, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                dest.write_bytes(r.read())
            log.info(f"  ↓ {name}  {dest.stat().st_size:,} bytes")
            return True
        except Exception as e:  # noqa: BLE001
            log.warning(f"    재시도 {attempt + 1}/4 ({name}): {e}")
            time.sleep(2 ** attempt)
    log.error(f"  ✗ {name} 내려받기 실패. 출처가 이전 중일 수 있다 — 원칙 문서 §2 참조")
    return False


def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", s).strip()


def fetch_page(degree: str, page: int) -> str:
    body = urllib.parse.urlencode(
        {
            "categoryNameCode": "001",
            "categoryType": "1",
            "categoryMenu": "006",
            "categoryDegree": degree,
            "categoryCode": "",
            "categoryCodeName": "",
            "searchGugun": "Y",
            "detailCheck": "Y",
            "listCheck": "0",
            "pageIndex": str(page),
            "strCategoryDegree": degree,
            "strCategoryType": "1",
            "strSearchGugun": "2",
        }
    ).encode()
    req = urllib.request.Request(URL, data=body, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            log.warning(f"    재시도 {attempt + 1}/4 (p{page}): {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{degree}차 {page}쪽 실패")


def total_of(html: str) -> int:
    m = re.search(r"total\s*:\s*([\d,]+)", clean(html))
    return int(m.group(1).replace(",", "")) if m else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--degrees", nargs="*", default=["11", "10"])
    ap.add_argument("--sleep", type=float, default=0.15)
    ap.add_argument("--files-only", action="store_true", help="포털 훑기 없이 첨부 파일 둘만")
    args = ap.parse_args()

    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    log.info("[첨부 파일]")
    ok = all([fetch_file(n, u) for n, u in FILES])
    if not ok:
        return 1
    if args.files_only:
        return 0

    log.info("\n[포털 분류검색 훑기]")
    for degree in args.degrees:
        first = fetch_page(degree, 1)
        total = total_of(first)
        if total == 0:
            log.error(f"{degree}차: 결과 0 — 포털 응답 형식이 바뀌었을 수 있다")
            return 1
        per = len(ROW_RE.findall(first))
        pages = -(-total // per)
        log.info(f"{degree}차: 총 {total:,}건, 쪽당 {per}건, {pages}쪽")

        rows: list[tuple[str, str, str]] = []
        for p in range(1, pages + 1):
            html = first if p == 1 else fetch_page(degree, p)
            got = [(c, clean(n), clean(s)) for c, n, s in ROW_RE.findall(html)]
            if not got:
                log.warning(f"  {p}쪽: 행 0 — 건너뜀")
            rows.extend(got)
            if p % 40 == 0 or p == pages:
                log.info(f"  {p}/{pages}쪽  누적 {len(rows):,}행")
            if p < pages:
                time.sleep(args.sleep)

        df = pd.DataFrame(rows, columns=["ksic", "name_ko", "section"]).drop_duplicates("ksic")
        df["level"] = df["ksic"].str.len().map({2: "중분류", 3: "소분류", 4: "세분류", 5: "세세분류"})
        out = EXTERNAL_DIR / f"ksic_{degree}_all.csv"
        df.to_csv(out, index=False, encoding="utf-8-sig")
        log.info(f"  → {out}  {len(df):,}행  " + str(df["level"].value_counts().to_dict()))
        if len(df) != total:
            log.warning(f"  ! 수집 {len(df):,} ≠ 공표 {total:,} — 중복 제거 또는 누락 확인 필요")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
04_build_dims.py — 참조 테이블 (dim_*, meta_edition)

설계 원칙 (docs/DB_구축_원칙.md §5.2):
- 코드·명칭·계층은 dim 에 둔다. fact 는 건드리지 않는다.
- 출처가 기계가 읽을 수 있는 형태면 그것을 쓴다. PDF 를 손으로 옮기지 않는다.

만드는 표:
  dim_icio_entity   ICIO 개체(80개국 + ROW).  출처: ReadMe_ICIO_small.xlsx 'Area_Activities'
  dim_icio_ind      ICIO 50산업 + 대응 ISIC Rev.4. 같은 출처. edition 별.
  dim_icio_fd       최종수요 6유형. 출처: ReadMe 'Structure'(코드), 이름은 SNA 표준.
  dim_isic4         ISIC Rev.4 전체 코드·명칭·계층. 출처: UNSD 공식 구조 파일
  dim_ksic          KSIC 11차·10차 세세분류 코드·명칭. 출처: KSSC 11차-10차 연계표 xlsx
  map_ksic_vintage  KSIC 11차↔10차 연계. 같은 출처. 두 방향(신구·구신) 모두.
  meta_edition      판 메타데이터

입력:
  data/raw/icio2025/ReadMe_ICIO_small.xlsx
  data/external/ISIC_Rev_4_english_structure.txt
  data/external/KSIC11_KSIC10_연계표.xlsx
출력: data/processed/gvc.duckdb

실행:
  python scripts\\04_build_dims.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import (DB_PATH, EXTERNAL_DIR, edition_of, interim_of,  # noqa: E402
                          readme_of, setup_logging)

log = setup_logging("build_dims")

ISIC_TXT = EXTERNAL_DIR / "ISIC_Rev_4_english_structure.txt"
KSIC_XLSX = EXTERNAL_DIR / "KSIC11_KSIC10_연계표.xlsx"

# 확장판의 분할 개체 → 원래 경제권.
# 확장판에서 CN1(가공무역 제외)과 CN2(가공무역)는 **한 나라의 두 부문**이다.
# CN2 의 부가가치는 CN1 수출에 실려도 "외국 부가가치"가 아니고,
# CN1→CN2 흐름은 "수출"이 아니다. 그래서 지표를 셀 때 이 경제권을 기준으로 삼는다.
SPLIT_TO_ECONOMY = {"CN1": "CHN", "CN2": "CHN", "MX1": "MEX", "MX2": "MEX"}

# 최종수요 유형. 코드는 원본, 이름은 SNA 표준 용어.
FD_ROWS = [
    ("HFCE", "Household final consumption expenditure", "가계 최종소비지출"),
    ("NPISH", "Non-profit institutions serving households", "가계봉사 비영리단체 최종소비지출"),
    ("GGFC", "General government final consumption", "정부 최종소비지출"),
    ("GFCF", "Gross fixed capital formation", "총고정자본형성"),
    ("INVNT", "Changes in inventories and valuables", "재고증감 및 귀중품 순취득"),
    ("DPABR", "Direct purchases abroad by residents", "거주자의 해외 직접구매"),
]


def read_area_activities(read_me: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """ReadMe 'Area_Activities' 시트에서 개체 목록과 산업 목록을 뽑는다.

    시트는 국가를 두 벌(왼쪽 1~40, 오른쪽 41~81)로 나눠 담고, 오른쪽에 산업 표가 붙어 있다.
    헤더 행은 2번(0-based)이다.
    """
    raw = pd.read_excel(read_me, sheet_name="Area_Activities", header=2)
    cols = list(raw.columns)

    def block(code_col: str, name_col: str) -> pd.DataFrame:
        d = raw[[code_col, name_col]].dropna()
        d.columns = ["code", "name_en"]
        return d[d["code"].astype(str).str.strip().str.len().between(2, 6)]

    ent = pd.concat([block("Code", "Country"), block("Code2", "Country2")], ignore_index=True)
    ent["code"] = ent["code"].astype(str).str.strip()
    ent["name_en"] = ent["name_en"].astype(str).str.strip()
    ent = ent.drop_duplicates("code").reset_index(drop=True)
    # economy = 경제권. 확장판에서 CN1·CN2 는 한 나라(중국)의 두 부문이다.
    # "자국 부가가치"와 "수출"을 셀 때 기준이 되는 것은 개체가 아니라 이 경제권이다.
    ent["economy"] = ent["code"].map(SPLIT_TO_ECONOMY).fillna(ent["code"])

    ind_cols = [c for c in cols if c in ("Code.1", "Industry", "ISIC Rev.4")]
    if len(ind_cols) < 3:
        raise ValueError(f"산업 열을 찾지 못했다: {cols}")
    ind = raw[["Code.1", "Industry", "ISIC Rev.4"]].dropna(subset=["Code.1"])
    ind.columns = ["icio_ind", "name_en", "isic4_raw"]
    for c in ind.columns:
        ind[c] = ind[c].astype(str).str.strip()
    ind = ind.reset_index(drop=True)
    ind.insert(0, "ind_no", range(1, len(ind) + 1))
    return ent, ind


def read_isic4() -> pd.DataFrame:
    d = pd.read_csv(ISIC_TXT, dtype=str).rename(columns={"Code": "isic4", "Description": "name_en"})
    d["isic4"] = d["isic4"].str.strip()
    d["name_en"] = d["name_en"].str.strip()
    # 계층: 문자 1자 = section, 2자 = division, 3자 = group, 4자 = class
    d["level"] = d["isic4"].map(lambda c: "section" if c.isalpha() else {2: "division", 3: "group", 4: "class"}[len(c)])
    parent = []
    section = None
    for c, lv in zip(d["isic4"], d["level"]):
        if lv == "section":
            section = c
            parent.append(None)
        elif lv == "division":
            parent.append(section)
        else:
            parent.append(c[:-1])
    d["parent"] = parent
    return d[["isic4", "level", "parent", "name_en"]]


def read_ksic() -> tuple[pd.DataFrame, pd.DataFrame]:
    """KSSC 11차-10차 연계표에서 KSIC 세세분류 목록과 차수 간 연계를 뽑는다.

    시트 둘: '신구연계표'(11차→10차), '구신연계표'(10차→11차).
    헤더가 두 줄(1행 차수, 2행 코드/항목명)이므로 header=1 로 읽고 열 이름을 손으로 준다.
    """
    frames = {}
    for sheet, (a, b) in {"신구연계표": ("11", "10"), "구신연계표": ("10", "11")}.items():
        d = pd.read_excel(KSIC_XLSX, sheet_name=sheet, header=None, skiprows=2, dtype=str)
        d = d.iloc[:, :5]
        d.columns = ["from_code", "from_name", "to_code", "to_name", "relation"]
        for c in d.columns:
            d[c] = d[c].astype(str).str.strip().replace({"nan": None})
        d = d[d["from_code"].notna() & d["from_code"].str.fullmatch(r"\d{5}")]
        d.insert(0, "to_rev", b)
        d.insert(0, "from_rev", a)
        frames[sheet] = d.reset_index(drop=True)

    vintage = pd.concat(frames.values(), ignore_index=True)

    # 코드 사전: 각 방향의 출발·도착 열 모두에서 (차수, 코드, 이름) 을 모은다
    parts = []
    for d in frames.values():
        parts.append(d[["from_rev", "from_code", "from_name"]].rename(
            columns={"from_rev": "ksic_rev", "from_code": "ksic", "from_name": "name_ko"}))
        parts.append(d[["to_rev", "to_code", "to_name"]].rename(
            columns={"to_rev": "ksic_rev", "to_code": "ksic", "to_name": "name_ko"}))
    codes = pd.concat(parts, ignore_index=True).dropna(subset=["ksic"])
    codes = codes[codes["ksic"].str.fullmatch(r"\d{5}")]
    codes = codes.drop_duplicates(["ksic_rev", "ksic"]).sort_values(["ksic_rev", "ksic"]).reset_index(drop=True)
    codes["level"] = "세세분류"
    codes["ksic2"] = codes["ksic"].str[:2]
    codes["ksic3"] = codes["ksic"].str[:3]
    codes["ksic4"] = codes["ksic"].str[:4]
    return codes, vintage


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["SML", "EXT"], default="SML")
    args = ap.parse_args()
    edition = edition_of(args.variant)
    read_me = readme_of(args.variant)

    for p in (read_me, ISIC_TXT, KSIC_XLSX):
        if not p.exists():
            log.error(f"없음: {p}")
            return 1

    ent, ind = read_area_activities(read_me)
    isic = read_isic4()
    ksic, vintage = read_ksic()

    # ReadMe 는 참고용 전체 목록이라 실제 표에 없는 개체까지 담을 수 있다
    # (확장판 ReadMe 에는 CN1/CN2 와 함께 CHN 도 적혀 있다).
    # 그래서 **실제 원표에서 뽑은 라벨**과 맞춰 걸러 낸다. 02 가 남긴 _labels.json 이 근거다.
    labels_path = interim_of(args.variant) / "_labels.json"
    if labels_path.exists():
        import json
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        in_data = set().union(*(set(v["entities"]) for v in labels.values()))
        extra = sorted(set(ent["code"]) - in_data)
        missing = sorted(in_data - set(ent["code"]))
        if extra:
            log.info(f"  ReadMe 에만 있고 원표에 없는 개체 {len(extra)}개 제외: {extra}")
        if missing:
            log.error(f"  원표에 있는데 ReadMe 에 없는 개체: {missing}")
            return 1
        ent = ent[ent["code"].isin(in_data)].reset_index(drop=True)
    else:
        log.warning(f"  {labels_path} 없음 — ReadMe 목록을 그대로 쓴다(02 를 먼저 돌릴 것)")

    ent.insert(0, "edition", edition)
    ind.insert(0, "edition", edition)

    con = duckdb.connect(str(DB_PATH))
    con.execute("BEGIN")

    # edition 별 표: 있으면 그 판의 행만 지우고 다시 넣는다. 다른 판을 건드리지 않는다.
    for name, df, cols in [
        ("dim_icio_entity", ent, "edition VARCHAR, code VARCHAR, name_en VARCHAR, economy VARCHAR"),
        ("dim_icio_ind", ind,
         "edition VARCHAR, ind_no INTEGER, icio_ind VARCHAR, name_en VARCHAR, isic4_raw VARCHAR"),
    ]:
        con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({cols})")
        con.execute(f"DELETE FROM {name} WHERE edition = ?", [edition])
        con.register("_t", df)
        con.execute(f"INSERT INTO {name} SELECT * FROM _t")
        con.unregister("_t")
        log.info(f"  {name:18s} {len(df):>7,}행 (edition={edition})")

    # 판과 무관한 표: 통째로 다시 만든다
    for name, df in [("dim_isic4", isic), ("dim_ksic", ksic), ("map_ksic_vintage", vintage)]:
        con.register("_t", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t")
        con.unregister("_t")
        log.info(f"  {name:18s} {len(df):>7,}행")

    fd = pd.DataFrame(FD_ROWS, columns=["fd_type", "name_en", "name_ko"])
    con.register("_t", fd)
    con.execute("CREATE OR REPLACE TABLE dim_icio_fd AS SELECT * FROM _t")
    con.unregister("_t")
    log.info(f"  dim_icio_fd        {len(fd):>7,}행")

    con.execute("""
        CREATE TABLE IF NOT EXISTS meta_edition (
            edition VARCHAR, publisher VARCHAR, released VARCHAR,
            year_from SMALLINT, year_to SMALLINT,
            n_entity INTEGER, n_industry INTEGER, n_fd INTEGER,
            unit VARCHAR, classification VARCHAR, note VARCHAR)
    """)
    con.execute("DELETE FROM meta_edition WHERE edition = ?", [edition])
    note = ("표준판(SML). 80개국+ROW. 중국·멕시코가 하나씩이다."
            if args.variant == "SML" else
            "확장판(EXT). 중국을 CN1(가공무역 제외)·CN2(가공무역)로, 멕시코를 MX1·MX2로 쪼갠 표. "
            "OECD 는 공표 TiVA 를 이 판에서 계산한 뒤 CHN·MEX 로 합친다.")
    con.execute(
        "INSERT INTO meta_edition VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [edition, "OECD", "2025-11", 1995, 2022, len(ent), len(ind), len(fd),
         "current million USD", "ISIC Rev.4", note],
    )
    con.execute("COMMIT")

    log.info("  개체 예: " + ", ".join(f"{r[0]}→{r[1]}" for r in con.execute(
        "SELECT code, economy FROM dim_icio_entity WHERE edition=? "
        "AND code IN ('KOR','CHN','CN1','CN2','MEX','MX1','MX2','ROW') ORDER BY code",
        [edition]).fetchall()))
    log.info("  경제권 수: " + str(con.execute(
        "SELECT count(DISTINCT economy) FROM dim_icio_entity WHERE edition=?", [edition]).fetchone()[0]))
    log.info("  산업 예: " + ", ".join(f"{r[0]}=ISIC {r[1]}" for r in con.execute(
        "SELECT icio_ind, isic4_raw FROM dim_icio_ind WHERE edition=? "
        "AND icio_ind IN ('C24A','C24B','C301','C302T309','C26')", [edition]).fetchall()))
    log.info("  KSIC 차수별 세세분류 수: " + str(
        con.execute("SELECT ksic_rev, count(*) FROM dim_ksic GROUP BY 1 ORDER BY 1").fetchall()))
    log.info("  적재된 판: " + str(con.execute(
        "SELECT edition, n_entity, n_industry FROM meta_edition ORDER BY edition").fetchall()))
    con.execute("CHECKPOINT")
    con.close()
    log.info(f"완료 → {DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

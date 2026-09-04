"""
05_build_maps.py — 분류 연계표 (map_isic_icio, map_ksic_isic, map_ksic_icio)

설계 원칙 (docs/DB_구축_원칙.md §4):
- ICIO 산업의 ISIC 정의는 OECD ReadMe 에서 기계적으로 뽑는다(손으로 옮기지 않는다).
- KSIC→ICIO 는 **가중치 없는 결정적 매핑**을 목표로 한다. 산출액 비례배분을 기본에서 뺐다(§1.2 나).
  KSIC 세세분류마다 ICIO 산업이 하나로 정해지는지를 이 스크립트가 실측으로 확인한다.
- 상위 단계(중분류 등)에서 1:n 이 생기는 것은 당연하다. 그 경우 가중치를 지어내지 않고
  weight 를 NULL 로 두고 n_icio 에 몇 갈래인지 적는다. 어떻게 나눌지는 분석 계층의 판단이다.

KSIC↔ISIC 대응 규칙 (RULES):
  KSIC 은 ISIC Rev.4 를 기초로 만들었지만 중분류 번호가 그대로 같지는 않다.
  실측으로 확인한 어긋남과 ICIO 가 division 보다 잘게 자르는 자리만 예외로 적고,
  나머지는 'KSIC 중분류 2자리 = ISIC division 2자리'를 따른다.
  규칙은 **긴 접두사 우선**이다.

입력: data/processed/gvc.duckdb 의 dim_icio_ind, dim_ksic
      data/external/ksic_{11,10}_all.csv   (전 단계 코드·명칭. 04b 가 만든다)
출력: map_isic_icio, map_ksic_isic, map_ksic_icio

실행:
  python scripts\\05_build_maps.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import DB_PATH, EDITION, EXTERNAL_DIR, setup_logging  # noqa: E402

log = setup_logging("build_maps")

# ─────────────────────────────────────────────────────────────────────────────
# KSIC 접두사 → (ICIO 산업, 대응 ISIC Rev.4, 근거)
# 긴 접두사가 이긴다. 2자리는 기본 규칙, 3~4자리는 예외.
# ─────────────────────────────────────────────────────────────────────────────
RULES: list[tuple[str, str, str, str]] = [
    # ── 농림어업 (KSIC 2자리 = ISIC division) ──
    ("01", "A01", "01", ""), ("02", "A02", "02", ""), ("03", "A03", "03", ""),
    # ── 광업: KSIC 번호가 ISIC 과 한 칸씩 어긋난다 ──
    ("05", "B05", "05", "KSIC 05 는 석탄(ISIC 05)과 원유·가스(ISIC 06)를 함께 담는다 → 소분류에서 가른다"),
    ("051", "B05", "05", "석탄 광업"),
    ("052", "B06", "06", "원유 및 천연가스 채굴업"),
    ("06", "B07", "07", "KSIC 06 금속 광업 = ISIC 07 (ISIC 06 아님)"),
    ("07", "B08", "08", "KSIC 07 비금속광물 광업 = ISIC 08"),
    ("08", "B09", "09", "KSIC 08 광업 지원 서비스업 = ISIC 09"),
    # ── 제조업 ──
    ("10", "C10T12", "10", ""), ("11", "C10T12", "11", ""), ("12", "C10T12", "12", ""),
    ("13", "C13T15", "13", ""), ("14", "C13T15", "14", ""), ("15", "C13T15", "15", ""),
    ("16", "C16", "16", ""),
    ("17", "C17_18", "17", ""), ("18", "C17_18", "18", ""),
    ("19", "C19", "19", ""), ("20", "C20", "20", ""), ("21", "C21", "21", ""),
    ("22", "C22", "22", ""), ("23", "C23", "23", ""),
    ("24", "C24A", "24", "ICIO 가 철강(C24A)과 비철(C24B)로 가른다 → 소·세분류에서 결정"),
    ("241", "C24A", "241", "1차 철강 제조업"),
    ("242", "C24B", "242", "1차 비철금속 제조업"),
    ("243", "C24A", "243", "금속 주조업. 세분류에서 철강/비철로 갈린다"),
    ("2431", "C24A", "2431", "철강 주조업. KSIC 2431 = ISIC 2431"),
    ("2432", "C24B", "2432", "비철금속 주조업. KSIC 2432 = ISIC 2432"),
    ("25", "C25", "25", ""),
    ("26", "C26", "26", "KSIC 26 전 범위가 ISIC 26 안에 든다(261~266 → ISIC 261~264, 268)"),
    ("27", "C26", "26", "KSIC 27 은 대부분 ISIC 26(265·266·267). 예외는 2719"),
    ("2719", "C31T33", "325", "기타 의료용 기기 = ISIC 325 → ICIO C31T33"),
    ("28", "C27", "27", ""), ("29", "C28", "28", ""), ("30", "C29", "29", ""),
    ("31", "C301", "30", "ICIO 가 선박(C301)과 기타 운송장비로 가른다 → 소분류에서 결정"),
    ("311", "C301", "301", "선박 및 보트 건조업"),
    ("312", "C302T309", "302", "철도장비"),
    ("313", "C302T309", "303", "항공기·우주선"),
    ("319", "C302T309", "309", "그 외 기타 운송장비"),
    ("32", "C31T33", "31", "가구 제조업 = ISIC 31"),
    ("33", "C31T33", "32", "기타 제품 제조업 = ISIC 32"),
    ("34", "C31T33", "33", "산업용 기계·장비 수리업 = ISIC 33"),
    # ── 전기·수도·건설 ──
    ("35", "D", "35", ""),
    ("36", "E", "36", ""), ("37", "E", "37", ""), ("38", "E", "38", ""), ("39", "E", "39", ""),
    ("41", "F", "41", ""), ("42", "F", "42", "KSIC 42 전문직별 공사업 = ISIC 42+43"),
    # ── 도소매·운수 ──
    ("45", "G", "45", ""), ("46", "G", "46", ""), ("47", "G", "47", ""),
    ("49", "H49", "49", ""), ("50", "H50", "50", ""), ("51", "H51", "51", ""), ("52", "H52", "52", ""),
    ("55", "I", "55", ""), ("56", "I", "56", ""),
    # ── 정보통신: 우편이 ISIC 53 이라 ICIO H53 으로 간다 ──
    ("58", "J58T60", "58", ""), ("59", "J58T60", "59", ""), ("60", "J58T60", "60", ""),
    ("61", "J61", "61", "KSIC 61 은 우편(ISIC 53)과 통신(ISIC 61)을 함께 담는다 → 소분류에서 가른다"),
    ("611", "H53", "53", "공영 우편업 = ISIC 53 → ICIO H53"),
    ("612", "J61", "61", "전기 통신업"),
    ("62", "J62_63", "62", ""), ("63", "J62_63", "63", ""),
    # ── 금융·부동산 ──
    ("64", "K", "64", ""), ("65", "K", "65", ""), ("66", "K", "66", ""),
    ("68", "L", "68", ""),
    # ── 전문·사업지원: KSIC 번호가 ISIC 과 다르다 ──
    ("70", "M", "72", "KSIC 70 연구개발업 = ISIC 72"),
    ("71", "M", "69", "KSIC 71 전문 서비스업 = ISIC 69·70·73"),
    ("72", "M", "71", "KSIC 72 건축기술·엔지니어링 = ISIC 71"),
    ("73", "M", "74", "KSIC 73 기타 전문·과학·기술 = ISIC 74·75"),
    ("74", "N", "81", "KSIC 74 사업시설 관리·조경 = ISIC 81"),
    ("75", "N", "78", "KSIC 75 사업 지원 = ISIC 78·79·80·82"),
    ("76", "N", "77", "KSIC 76 임대업(부동산 제외) = ISIC 77"),
    # ── 공공·교육·보건 ──
    ("84", "O", "84", ""), ("85", "P", "85", ""),
    ("86", "Q", "86", ""), ("87", "Q", "87", "KSIC 87 사회복지 = ISIC 87·88"),
    ("90", "R", "90", "KSIC 90 창작·예술·여가 = ISIC 90·91"),
    ("91", "R", "93", "KSIC 91 스포츠·오락 = ISIC 92·93"),
    ("94", "S", "94", ""), ("95", "S", "95", ""), ("96", "S", "96", ""),
    ("97", "T", "97", ""), ("98", "T", "98", ""),
    # ── ICIO 에 대응 산업이 없는 것 ──
    ("99", None, "99", "국제 및 외국기관. ICIO 50산업에 ISIC 99 가 없다 → 매핑 없음"),
]

RULE_MAP = {p: (icio, isic, note) for p, icio, isic, note in RULES}
MAX_PREFIX = max(len(p) for p in RULE_MAP)


def rule_for(code: str) -> tuple[str | None, str, str, str] | None:
    """긴 접두사 우선. 맞는 규칙이 없으면 None."""
    for n in range(min(len(code), MAX_PREFIX), 1, -1):
        p = code[:n]
        if p in RULE_MAP:
            icio, isic, note = RULE_MAP[p]
            return icio, isic, note, p
    return None


def parse_isic_spec(spec: str) -> list[str]:
    """ICIO ReadMe 의 'ISIC Rev.4' 표기를 코드 목록으로 편다.

    실측 형태: '01' · '10, 11, 12' · '241, 2431' · '302 to 309' · '94,95, 96' · '69 to 75'
    """
    out: list[str] = []
    for part in re.split(r",", spec):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s+to\s+(\d+)", part)
        if m:
            a, b = m.group(1), m.group(2)
            if len(a) != len(b):
                raise ValueError(f"범위 자릿수 불일치: {part}")
            out.extend(str(i).zfill(len(a)) for i in range(int(a), int(b) + 1))
        elif re.fullmatch(r"\d+", part):
            out.append(part)
        else:
            raise ValueError(f"해석 못 한 ISIC 표기: {part!r} (원문 {spec!r})")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", default=EDITION)
    args = ap.parse_args()

    con = duckdb.connect(str(DB_PATH))

    # ── 1. map_isic_icio : ICIO 산업의 ISIC 정의를 편다 ──
    ind = con.execute(
        "SELECT icio_ind, isic4_raw FROM dim_icio_ind WHERE edition = ?", [args.edition]
    ).df()
    rows = [
        (args.edition, isic, len(isic), r.icio_ind)
        for r in ind.itertuples()
        for isic in parse_isic_spec(r.isic4_raw)
    ]
    m_isic = pd.DataFrame(rows, columns=["edition", "isic4", "isic_digits", "icio_ind"])
    dup = m_isic[m_isic.duplicated("isic4", keep=False)]
    if len(dup):
        log.warning(f"  ISIC 코드가 두 ICIO 산업에 걸침 {len(dup)}건:\n{dup}")

    # ── 2. KSIC 전 단계 코드에 규칙을 적용 ──
    ksic_all = []
    for rev in ("11", "10"):
        p = EXTERNAL_DIR / f"ksic_{rev}_all.csv"
        if not p.exists():
            log.error(f"없음: {p} — 04b_fetch_ksic.py 를 먼저 돌릴 것")
            return 1
        d = pd.read_csv(p, dtype=str)
        d["ksic_rev"] = rev
        ksic_all.append(d)
    ksic = pd.concat(ksic_all, ignore_index=True)

    leaf = ksic[ksic["level"] == "세세분류"].copy()
    applied = leaf["ksic"].map(rule_for)
    if applied.isna().any():
        bad = leaf.loc[applied.isna(), ["ksic_rev", "ksic", "name_ko"]]
        log.error(f"  규칙이 없는 세세분류 {len(bad)}건:\n{bad.head(20)}")
        return 1
    leaf["icio_ind"] = [a[0] for a in applied]
    leaf["isic4"] = [a[1] for a in applied]
    leaf["note"] = [a[2] for a in applied]
    leaf["rule_prefix"] = [a[3] for a in applied]

    # ── 3. 상위 단계는 자손의 ICIO 산업 집합으로 정한다 (1:n 을 자동으로 드러낸다) ──
    recs = []
    for rev, g in leaf.groupby("ksic_rev"):
        by_code = {}
        for r in g.itertuples():
            if r.icio_ind is None:
                continue
            for n in (2, 3, 4, 5):
                by_code.setdefault((r.ksic[:n], n), set()).add(r.icio_ind)
        for (code, n), inds in by_code.items():
            for icio in sorted(inds):
                recs.append((rev, code, {2: "중분류", 3: "소분류", 4: "세분류", 5: "세세분류"}[n],
                             icio, 1.0 if len(inds) == 1 else None, len(inds)))
    m_ksic_icio = pd.DataFrame(
        recs, columns=["ksic_rev", "ksic", "ksic_level", "icio_ind", "weight", "n_icio"]
    )
    # 규칙 근거를 붙인다
    m_ksic_icio = m_ksic_icio.merge(
        leaf[["ksic_rev", "ksic", "rule_prefix", "note"]], on=["ksic_rev", "ksic"], how="left"
    )

    m_ksic_isic = leaf[["ksic_rev", "ksic", "isic4", "icio_ind", "rule_prefix", "note"]].copy()

    # ── 4. 적재 ──
    con.execute("BEGIN")

    # edition 별 표 — 그 판의 행만 갈아 끼운다
    con.execute("CREATE TABLE IF NOT EXISTS map_isic_icio ("
                "edition VARCHAR, isic4 VARCHAR, isic_digits INTEGER, icio_ind VARCHAR)")
    con.execute("DELETE FROM map_isic_icio WHERE edition = ?", [args.edition])
    con.register("_t", m_isic)
    con.execute("INSERT INTO map_isic_icio SELECT * FROM _t")
    con.unregister("_t")
    log.info(f"  map_isic_icio    {len(m_isic):>7,}행 (edition={args.edition})")

    # KSIC 쪽은 판과 무관하다 — ICIO 50산업이 두 판에서 같기 때문이다
    for name, df in [("map_ksic_isic", m_ksic_isic), ("map_ksic_icio", m_ksic_icio)]:
        con.register("_t", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t")
        con.unregister("_t")
        log.info(f"  {name:16s} {len(df):>7,}행")
    con.execute("COMMIT")

    # ── 5. 실측 보고 — 가중치가 필요한지 여기서 판정된다 (§1.2 나) ──
    log.info("")
    log.info("[매핑 실측]")
    for rev in ("11", "10"):
        g = m_ksic_icio[m_ksic_icio.ksic_rev == rev]
        for lv in ("중분류", "소분류", "세분류", "세세분류"):
            s = g[g.ksic_level == lv]
            codes = s.ksic.nunique()
            multi = s[s.n_icio > 1].ksic.nunique()
            log.info(f"  {rev}차 {lv}: 코드 {codes:>5,}개 중 1:n {multi}개"
                     + (f"  → {sorted(s[s.n_icio > 1].ksic.unique())}" if 0 < multi <= 12 else ""))
        unmapped = leaf[(leaf.ksic_rev == rev) & (leaf.icio_ind.isna())]
        log.info(f"  {rev}차 세세분류 중 ICIO 대응 없음: {len(unmapped)}개"
                 + (f" ({', '.join(unmapped.ksic)})" if len(unmapped) <= 12 else ""))
    log.info(f"  ICIO 50산업 중 KSIC 이 닿지 않는 산업: "
             f"{sorted(set(ind.icio_ind) - set(m_ksic_icio.icio_ind.dropna()))}")

    con.execute("CHECKPOINT")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

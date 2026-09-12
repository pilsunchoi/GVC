"""
08_validate_tiva.py — OECD TiVA 공표치와 자체 산출치 대조 (mart_tiva_check)

왜 (docs/DB_구축_원칙.md §6-9):
- 우리가 ICIO 원표에서 직접 계산한 지표가 OECD 공표치를 재현하는지 확인한다.
  KCSDB2 의 `mart_nqi_check`(우리 도출치 대 관세청 공식치)와 같은 역할이다.

이 대조로 판정한 두 가지 (2026-09-04 실측):
 1. **TiVA 의 VALU = ICIO 의 VA + TLS 다.** 상대오차 중위 0.0000%.
    docs/method.md §1 에서 v = (VA+TLS)/X 로 둔 선택이 옳다는 뜻이다.
 2. **중국·멕시코만 EXGR_DVA/EXGR_FVA 가 어긋난다.** 확장판(EXT, CN1/CN2·MX1/MX2)에서 계산해
    CHN·MEX 로 합치면 맞으므로, 공표 TiVA 는 확장판 기준으로 계산된 것으로 판단한다.
    대조로 얻은 추론이며 OECD 문서에서 명시한 문장은 확인하지 못했다(2026-09-11). 가공무역 부문의
    국내부가가치율이 낮으므로 표준판(SML)으로 계산하면 DVA 가 높게 나온다.
    나머지 78개국 + ROW 는 반올림 수준으로 일치한다.

자료: OECD SDMX
  base   https://sdmx.oecd.org/sti-public/rest/data   (sdmx.oecd.org/public 은 404)
  흐름   OECD.STI.PIE,DSD_TIVA_MAINLV@DF_MAINLV,1.1
  키     MEASURE.REF_AREA.ACTIVITY.COUNTERPART_AREA.UNIT_MEASURE.FREQ

산업코드 차이: TiVA 는 ICIO 의 C24A/C24B 를 C241_2431/C242_2432 로 쓴다. 나머지는 같다.
TiVA 는 집계 항목(A, C, BTE, _T 등)도 함께 내므로 ICIO 50산업만 남긴다.

입력: mart_gvc_core
출력: data/external/tiva/*.csv (원본 보존), mart_tiva_check

실행:
  python scripts\\08_validate_tiva.py
  python scripts\\08_validate_tiva.py --refresh      # 캐시 무시하고 다시 받기
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import DB_PATH, EDITION, EXTERNAL_DIR, USER_AGENT, setup_logging  # noqa: E402

log = setup_logging("validate_tiva")

BASE = "https://sdmx.oecd.org/sti-public/rest/data/OECD.STI.PIE,DSD_TIVA_MAINLV@DF_MAINLV,1.1"

# TiVA 측정치 → mart_gvc_core 의 대응 식 (아래 SQL 의 long CTE 와 짝이 맞아야 한다)
MEASURES = {
    "EXGR": "총수출",
    "EXGR_DVA": "수출에 실린 자국 부가가치",
    "EXGR_FVA": "수출에 실린 외국 부가가치",
    "PROD": "총산출",
    "VALU": "부가가치 (VA + TLS)",
}

# TiVA 산업코드 → ICIO 2025 산업코드
ACT_FIX = {"C241_2431": "C24A", "C242_2432": "C24B"}

MATERIAL = 1000.0   # 백만 달러. 이보다 작은 칸은 상대오차가 과장되므로 판정에서 뺀다.


def fetch(measure: str, dest: Path, refresh: bool) -> pd.DataFrame:
    if dest.exists() and not refresh:
        log.info(f"  = {measure}: 캐시 사용 ({dest.stat().st_size / 1e6:.1f}MB)")
        return pd.read_csv(dest)
    url = f"{BASE}/{measure}...W..A?format=csvfile"
    log.info(f"  ↓ {measure} …")
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                dest.write_bytes(r.read())
            break
        except Exception as e:  # noqa: BLE001
            log.warning(f"    재시도 {attempt + 1}/4: {e}")
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"{measure} 내려받기 실패")
    df = pd.read_csv(dest)
    log.info(f"    {len(df):,}행  {dest.stat().st_size / 1e6:.1f}MB  {time.time() - t0:.0f}s")
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", default=EDITION)
    ap.add_argument("--method-version", default="v1")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    out_dir = EXTERNAL_DIR / "tiva"
    out_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    for m in MEASURES:
        d = fetch(m, out_dir / f"tiva_{m}.csv", args.refresh)
        d = d[["REF_AREA", "ACTIVITY", "TIME_PERIOD", "OBS_VALUE", "UNIT_MULT"]].copy()
        d["measure"] = m
        frames.append(d)
    tiva = pd.concat(frames, ignore_index=True)
    tiva["ACTIVITY"] = tiva["ACTIVITY"].replace(ACT_FIX)
    mult = set(tiva["UNIT_MULT"].dropna().unique())
    if mult - {6}:
        log.warning(f"  ! UNIT_MULT 이 6 아닌 값 있음: {sorted(mult)} — 단위 환산 필요")
    tiva = tiva.rename(columns={"REF_AREA": "cty", "ACTIVITY": "ind",
                                "TIME_PERIOD": "year", "OBS_VALUE": "tiva_value"})

    con = duckdb.connect(str(DB_PATH))
    inds = {r[0] for r in con.execute(
        "SELECT icio_ind FROM dim_icio_ind WHERE edition=?", [args.edition]).fetchall()}
    ctys = {r[0] for r in con.execute(
        "SELECT code FROM dim_icio_entity WHERE edition=?", [args.edition]).fetchall()}
    econs = {r[0] for r in con.execute(
        "SELECT DISTINCT economy FROM dim_icio_entity WHERE edition=?", [args.edition]).fetchall()}
    n0 = len(tiva)
    # 확장판에서는 비교 대상이 개체(CN1)가 아니라 경제권(CHN)이다
    tiva = tiva[tiva["ind"].isin(inds) & tiva["cty"].isin(econs)]
    log.info(f"  ICIO 50산업·경제권({len(econs)}개)만 남김: {n0:,} → {len(tiva):,}행")

    con.register("_t", tiva)
    con.execute(f"""
        CREATE OR REPLACE TABLE _tiva_new AS
        WITH ours AS (
            -- 확장판이면 CN1+CN2 → CHN, MX1+MX2 → MEX 로 접는다. TiVA 는 나라 단위로 공표되기 때문이다.
            -- 여기서 더하는 값은 모두 가산적이다(금액, 그리고 비중 x 금액).
            SELECT c.year, e.economy AS cty, c.ind,
                   sum(c.exgr) AS exgr,
                   sum(c.dva_share*c.exgr) AS dva,
                   sum(c.fva_share*c.exgr) AS fva,
                   sum(c.out) AS out,
                   sum(c.va + c.tls) AS va_tls
            FROM mart_gvc_core c
            JOIN dim_icio_entity e ON e.edition = c.edition AND e.code = c.cty
            WHERE c.edition=? AND c.method_version=?
            GROUP BY 1,2,3
        ), long AS (
            SELECT year, cty, ind, 'EXGR'     AS m, exgr   AS v FROM ours UNION ALL
            SELECT year, cty, ind, 'EXGR_DVA',      dva    FROM ours UNION ALL
            SELECT year, cty, ind, 'EXGR_FVA',      fva    FROM ours UNION ALL
            SELECT year, cty, ind, 'PROD',          out    FROM ours UNION ALL
            SELECT year, cty, ind, 'VALU',          va_tls FROM ours
        )
        SELECT ? AS edition, ? AS method_version,
               t.year, t.cty, t.ind, t.measure,
               l.v AS ours_value, t.tiva_value,
               l.v - t.tiva_value AS diff,
               CASE WHEN abs(t.tiva_value) > 1 THEN (l.v - t.tiva_value)/t.tiva_value END AS rel_diff,
               -- 상대오차는 작은 칸에서 과장된다. 규모가 있는 칸만 판정에 쓴다.
               t.tiva_value > {MATERIAL} AS material,
               -- 공표 TiVA 는 확장판 기준으로 판단된다(대조로 얻은 추론). 이 둘만 갈라 본다.
               t.cty IN ('CHN','MEX') AS split_country
        FROM _t t JOIN long l
          ON l.year=t.year AND l.cty=t.cty AND l.ind=t.ind AND l.m=t.measure
    """, [args.edition, args.method_version, args.edition, args.method_version])
    con.unregister("_t")
    # 그 판의 행만 갈아 끼운다
    con.execute("CREATE TABLE IF NOT EXISTS mart_tiva_check AS SELECT * FROM _tiva_new WHERE false")
    con.execute("DELETE FROM mart_tiva_check WHERE edition = ? AND method_version = ?",
                [args.edition, args.method_version])
    con.execute("INSERT INTO mart_tiva_check SELECT * FROM _tiva_new")
    con.execute("DROP TABLE _tiva_new")

    n = con.execute("SELECT count(*) FROM mart_tiva_check WHERE edition = ?", [args.edition]).fetchone()[0]
    log.info(f"\n  mart_tiva_check {n:,}행")

    log.info(f"\n[대조 결과] TiVA 값이 {MATERIAL:,.0f}(백만 달러)를 넘는 칸만, 중국·멕시코를 갈라서")
    for r in con.execute("""
        SELECT measure, split_country, count(*) n,
               round(max(abs(diff)),3), round(100*median(abs(rel_diff)),6),
               round(100*max(abs(rel_diff)),4),
               sum(CASE WHEN abs(rel_diff) > 0.001 THEN 1 ELSE 0 END)
        FROM mart_tiva_check WHERE edition = ? AND material AND rel_diff IS NOT NULL
        GROUP BY 1,2 ORDER BY 1,2""", [args.edition]).fetchall():
        tag = "CHN·MEX" if r[1] else "그 외  "
        log.info(f"  {r[0]:<9} {tag}  n={r[2]:>6,}  절대최대 {r[3]:>10,.3f}  "
                 f"중위 {r[4]:>8}%  최대 {r[5]:>8}%  0.1%초과 {r[6]:,}칸")

    log.info("\n[세계 총계 대조]")
    for r in con.execute("""
        SELECT measure, round(100*max(abs(d)),6) FROM (
          SELECT measure, year, (sum(ours_value)-sum(tiva_value))/sum(tiva_value) d
          FROM mart_tiva_check WHERE edition = ? GROUP BY 1,2) GROUP BY 1 ORDER BY 1""", [args.edition]).fetchall():
        log.info(f"  {r[0]:<9} 연도별 최대 괴리 {r[1]}%")

    log.info("\n[판정]")
    log.info("  · PROD·EXGR 은 반올림 수준으로 일치 → 원표 적재와 총수출 정의가 옳다.")
    log.info("  · VALU 는 va+tls 로 비교한다. TiVA 의 VALU 는 ICIO 의 VA 에 TLS 를 더한 것이다.")
    log.info("    method.md §1 의 v = (VA+TLS)/X 선택이 이것으로 확인된다.")
    log.info("  · EXGR_DVA·EXGR_FVA 는 중국·멕시코에서만 어긋난다(DVA 약 +3%, FVA 약 −7%).")
    log.info("    확장판(EXT)에서 계산해 CHN·MEX 로 합치면 맞으므로 공표 TiVA 는 확장판 기준으로 판단한다(추론).")
    log.info("    그 두 나라를 OECD 공표치와 맞추려면 EXT 판을 별도 edition 으로 적재한다:")
    log.info("      python scripts\\01_fetch_icio.py --variant EXT")
    log.info("      python scripts\\02_icio_to_parquet.py --variant EXT")
    log.info("      python scripts\\03_parquet_to_duckdb.py --edition ICIO2025_EXT")

    con.execute("CHECKPOINT")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

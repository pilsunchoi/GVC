"""
03_parquet_to_duckdb.py — 연도별 parquet → DuckDB fact 테이블

설계 원칙 (docs/DB_구축_원칙.md §5.1):
- fact 는 OECD 가 배포한 값만 담는다. 파생 컬럼 없음.
- edition 을 키에 넣는다. 판이 다르면 다른 자료이고 결합하지 않는다(§0-5).
- 다시 돌리면 같은 결과가 나와야 한다 → 같은 edition 의 행을 지우고 새로 넣는다.

입력: data/interim/icio2025/{z,fd,va}/*.parquet
출력: data/processed/gvc.duckdb 의 fact_icio_z, fact_icio_fd, fact_icio_va, meta_source

실행:
  python scripts\\03_parquet_to_duckdb.py
  python scripts\\03_parquet_to_duckdb.py --edition ICIO2025_EXT
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import DB_PATH, EDITION, RAW_DIR, interim_of, setup_logging, variant_of  # noqa: E402

log = setup_logging("parquet_to_duckdb")

DDL = {
    "fact_icio_z": """
        CREATE TABLE IF NOT EXISTS fact_icio_z (
            edition VARCHAR NOT NULL,
            year    SMALLINT NOT NULL,
            src_cty VARCHAR NOT NULL,
            src_ind VARCHAR NOT NULL,
            dst_cty VARCHAR NOT NULL,
            dst_ind VARCHAR NOT NULL,
            value   DOUBLE  NOT NULL
        )""",
    "fact_icio_fd": """
        CREATE TABLE IF NOT EXISTS fact_icio_fd (
            edition VARCHAR NOT NULL,
            year    SMALLINT NOT NULL,
            src_cty VARCHAR NOT NULL,
            src_ind VARCHAR NOT NULL,
            dst_cty VARCHAR NOT NULL,
            fd_type VARCHAR NOT NULL,
            value   DOUBLE  NOT NULL
        )""",
    "fact_icio_va": """
        CREATE TABLE IF NOT EXISTS fact_icio_va (
            edition VARCHAR NOT NULL,
            year    SMALLINT NOT NULL,
            cty     VARCHAR NOT NULL,
            ind     VARCHAR NOT NULL,
            tls     DOUBLE  NOT NULL,
            va      DOUBLE  NOT NULL,
            out     DOUBLE  NOT NULL
        )""",
    "meta_source": """
        CREATE TABLE IF NOT EXISTS meta_source (
            edition    VARCHAR NOT NULL,
            file_name  VARCHAR NOT NULL,
            url        VARCHAR,
            bytes      BIGINT,
            sha256     VARCHAR,
            fetched_at VARCHAR
        )""",
}

# (테이블, parquet 하위폴더, 파일 접두, SELECT 목록)
SPECS = [
    ("fact_icio_z", "z", "z", "src_cty, src_ind, dst_cty, dst_ind, value"),
    ("fact_icio_fd", "fd", "fd", "src_cty, src_ind, dst_cty, fd_type, value"),
    ("fact_icio_va", "va", "va", "cty, ind, tls, va, out"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", default=EDITION)
    ap.add_argument("--interim", help="지정하지 않으면 edition 에서 판을 찾아 도출한다")
    args = ap.parse_args()

    src_dir = Path(args.interim) if args.interim else interim_of(variant_of(args.edition))
    log.info(f"edition={args.edition}  parquet={src_dir}")
    if not src_dir.exists():
        log.error(f"없음: {src_dir} — 02_icio_to_parquet.py 를 먼저 돌릴 것")
        return 1

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    for ddl in DDL.values():
        con.execute(ddl)

    for table, sub, prefix, cols in SPECS:
        files = sorted((src_dir / sub).glob(f"{prefix}_*.parquet"))
        if not files:
            log.warning(f"{table}: {src_dir / sub} 에 parquet 없음 — 건너뜀")
            continue
        con.execute(f"DELETE FROM {table} WHERE edition = ?", [args.edition])
        t0 = time.time()
        for f in files:
            year = int(f.stem.split("_")[-1])
            con.execute(
                f"INSERT INTO {table} SELECT ?, ?, {cols} FROM read_parquet(?)",
                [args.edition, year, str(f)],
            )
        n = con.execute(f"SELECT count(*) FROM {table} WHERE edition = ?", [args.edition]).fetchone()[0]
        log.info(f"  {table:14s} {n:>12,}행  ({len(files)}개 연도, {time.time() - t0:.0f}s)")

    # meta_source — 무엇을 받아 만들었는지
    manifest = RAW_DIR / "icio2025" / "_manifest.json"
    if manifest.exists():
        con.execute("DELETE FROM meta_source WHERE edition = ?", [args.edition])
        rows = [
            (args.edition, k, v.get("url"), v.get("bytes"), v.get("sha256"), v.get("fetched_at"))
            for k, v in json.loads(manifest.read_text(encoding="utf-8")).items()
        ]
        con.executemany("INSERT INTO meta_source VALUES (?,?,?,?,?,?)", rows)
        log.info(f"  meta_source    {len(rows):>12,}행")
    else:
        log.warning("매니페스트가 없다 — meta_source 를 채우지 못했다")

    con.execute("CHECKPOINT")
    con.close()
    log.info(f"완료 → {DB_PATH}  ({DB_PATH.stat().st_size / 1e9:.2f} GB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

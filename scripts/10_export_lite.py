"""
10_export_lite.py — 교육·배포용 축소 DB (gvc_lite.duckdb)

왜 (docs/DB_구축_원칙.md §8):
- 본 DB는 약 2GB다. 그중 92%가 `fact_icio_z`(2억 5,500만 행) 하나다.
- 수업이나 첫 탐색에서 필요한 것은 dim·map·mart 이지 원표 자체가 아니다.
- 그래서 `fact_icio_z` 를 뺀 판을 따로 낸다. 수십 MB 라 내려받기 부담이 없다.

**뺀 것**: `fact_icio_z`(중간재 거래)와 `fact_icio_fd`(최종수요). 둘이 부피의 대부분이다.
이것들이 없으면 레온티에프 역행렬을 다시 구할 수 없으므로 **지표를 재계산하려면 본 DB가 필요하다.**
이미 계산된 지표를 **쓰는** 데는 이것으로 충분하다. dim·map·mart 와 `fact_icio_va` 는 그대로 있다.
그 사실을 `meta_lite` 표에 적어 둔다 — 받은 사람이 무엇이 없는지 알아야 한다.

출력: data/processed/gvc_lite.duckdb

실행:
  python scripts\\10_export_lite.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import DB_PATH, PROCESSED_DIR, setup_logging  # noqa: E402

log = setup_logging("export_lite")

# 빼는 것: 큰 원표 둘. 이것들이 DB 부피의 대부분이다.
# fact_icio_va(총산출·부가가치)는 작고 쓸모가 많아 남긴다.
EXCLUDE = {"fact_icio_z", "fact_icio_fd"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(PROCESSED_DIR / "gvc_lite.duckdb"))
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists():
        out.unlink()
    for suffix in (".wal",):
        p = out.with_suffix(out.suffix + suffix)
        if p.exists():
            p.unlink()

    src = duckdb.connect(str(DB_PATH), read_only=True)
    tables = [r[0] for r in src.execute("SHOW TABLES").fetchall()]
    src.close()

    con = duckdb.connect(str(out))
    # DuckDB 예약어 주의: FULL 은 alias 로 못 쓴다(FULL JOIN). src 로 붙인다.
    con.execute(f"ATTACH '{DB_PATH}' AS src (READ_ONLY)")
    kept, skipped = [], []
    for t in tables:
        if t in EXCLUDE:
            skipped.append(t)
            continue
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        kept.append((t, n))
        log.info(f"  {t:22s} {n:>12,}행")

    con.execute("""
        CREATE TABLE meta_lite (
            built_on VARCHAR, source_db VARCHAR, excluded VARCHAR, note VARCHAR)
    """)
    con.execute("INSERT INTO meta_lite VALUES (?,?,?,?)", [
        date.today().isoformat(), DB_PATH.name, ", ".join(skipped),
        "원표 중간재 거래(fact_icio_z)와 최종수요(fact_icio_fd)가 빠졌다. 이미 계산된 지표를 쓰는 데는 "
        "충분하지만, 레온티에프 역행렬을 다시 구할 수 없으므로 지표를 재계산하려면 본 DB가 필요하다.",
    ])
    con.execute("DETACH src")
    con.execute("CHECKPOINT")
    con.close()

    log.info(f"\n  뺀 표: {skipped}")
    log.info(f"  본 DB {DB_PATH.stat().st_size / 1e9:.2f} GB → 축소판 {out.stat().st_size / 1e6:.0f} MB")
    log.info(f"  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

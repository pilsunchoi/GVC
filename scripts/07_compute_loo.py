"""
07_compute_loo.py — leave-one-out 지표 (mart_gvc_loo)

왜 이 정의인가 (docs/DB_구축_원칙.md §1.2 다):
  원 계획서는 "한국 행·열을 ROW 에 흡수한 축소 ICIO 에서 재계산"이라고 적었다.
  그 표에는 한국이 없으므로 한국 산업의 지표가 나오지 않는다. 도구변수로 쓸 수 없다.
  표준적인 leave-one-out 은 **같은 산업·같은 연도의, 자국을 뺀 다른 나라들의 지표**다.
  자국 기업·산업의 내생적 반응과 무관하면서 같은 기술·수요 충격을 담는다.

산식:
  loo_x[s,i,t] = (Σ_r w[r,i,t]·x[r,i,t] − Σ_{r∈s} w[r,i,t]·x[r,i,t])
                 / (Σ_r w[r,i,t] − Σ_{r∈s} w[r,i,t])
  가중치 w 는 총수출. 단순평균(가중치 1)도 함께 낸다.

  **빼는 단위는 개체가 아니라 경제권이다.** 확장판에서 CN1 의 「타국」에 CN2 가 들어가면
  중국을 뺀 것이 아니다. `dim_icio_entity.economy` 로 묶어 통째로 뺀다.
  표준판에서는 개체가 곧 경제권이라 결과가 달라지지 않는다.

  **ROW 는 합계에서 뺀다.** 잔차 지역이라 한 나라로 볼 수 없다.
  수출이 0 인 칸은 가중평균에 뜻이 없으므로 뺀다.

입력: mart_gvc_core, dim_icio_entity
출력: mart_gvc_loo (행은 개체 단위. 같은 경제권의 개체는 같은 값을 갖는다)

실행:
  python scripts\\07_compute_loo.py
  python scripts\\07_compute_loo.py --edition ICIO2025_EXT
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import DB_PATH, EDITION, setup_logging  # noqa: E402

log = setup_logging("compute_loo")

METHOD_VERSION = "v1"

METRICS = ["dva_share", "fva_share", "backward", "forward", "upstreamness", "downstreamness"]

SQL = """
CREATE OR REPLACE TABLE _loo_new AS
WITH cell AS (
    SELECT c.year, c.cty, e.economy, c.ind, c.exgr AS w, {cols}
    FROM mart_gvc_core c
    JOIN dim_icio_entity e ON e.edition = c.edition AND e.code = c.cty
    WHERE c.edition = ? AND c.method_version = ?
      AND e.economy <> 'ROW'      -- 잔차 지역은 한 나라가 아니다
      AND c.exgr > 0              -- 수출 0 인 칸은 가중평균에 뜻이 없다
), econ AS (           -- 경제권 단위로 접는다 (뺄 단위가 이것이다)
    SELECT year, economy, ind, {econ_aggs}
    FROM cell GROUP BY year, economy, ind
), tot AS (            -- 산업·연도 전체 합
    SELECT year, ind, count(*) AS n_econ, {tot_aggs}
    FROM econ GROUP BY year, ind
)
SELECT
    ? AS edition, ? AS method_version, c.year, c.cty, c.ind,
    t.n_econ - 1 AS n_others,
    {outs}
FROM cell c
JOIN econ o ON o.year = c.year AND o.economy = c.economy AND o.ind = c.ind
JOIN tot  t ON t.year = c.year AND t.ind = c.ind
WHERE t.n_econ > 1
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", default=EDITION)
    args = ap.parse_args()

    cols = ", ".join(METRICS)
    # 경제권 단위 합: 가중합·가중치합·값합·개수. 나중에 전체에서 이것을 뺀다.
    econ_aggs = ", ".join(
        f"sum(w * {m}) AS wx_{m}, sum(CASE WHEN {m} IS NULL THEN 0 ELSE w END) AS ww_{m}, "
        f"sum({m}) AS sx_{m}, count({m}) AS nx_{m}"
        for m in METRICS
    )
    tot_aggs = ", ".join(
        f"sum(wx_{m}) AS Twx_{m}, sum(ww_{m}) AS Tww_{m}, "
        f"sum(sx_{m}) AS Tsx_{m}, sum(nx_{m}) AS Tnx_{m}"
        for m in METRICS
    )
    outs = ",\n    ".join(
        [
            f"CASE WHEN (t.Tww_{m} - o.ww_{m}) > 0 "
            f"THEN (t.Twx_{m} - o.wx_{m}) / (t.Tww_{m} - o.ww_{m}) END AS loo_w_{m}"
            for m in METRICS
        ]
        + [
            f"CASE WHEN (t.Tnx_{m} - o.nx_{m}) > 0 "
            f"THEN (t.Tsx_{m} - o.sx_{m}) / (t.Tnx_{m} - o.nx_{m}) END AS loo_m_{m}"
            for m in METRICS
        ]
    )

    con = duckdb.connect(str(DB_PATH))
    n_core = con.execute(
        "SELECT count(*) FROM mart_gvc_core WHERE edition=? AND method_version=?",
        [args.edition, METHOD_VERSION],
    ).fetchone()[0]
    if n_core == 0:
        log.error("mart_gvc_core 가 비었다 — 06_compute_gvc.py 를 먼저 돌릴 것")
        return 1

    # 임시 표로 만든 뒤 그 판의 행만 갈아 끼운다. 다른 판을 지우지 않는다.
    con.execute(
        SQL.format(cols=cols, econ_aggs=econ_aggs, tot_aggs=tot_aggs, outs=outs),
        [args.edition, METHOD_VERSION, args.edition, METHOD_VERSION],
    )
    con.execute("CREATE TABLE IF NOT EXISTS mart_gvc_loo AS SELECT * FROM _loo_new WHERE false")
    con.execute("DELETE FROM mart_gvc_loo WHERE edition = ? AND method_version = ?",
                [args.edition, METHOD_VERSION])
    con.execute("INSERT INTO mart_gvc_loo SELECT * FROM _loo_new")
    con.execute("DROP TABLE _loo_new")
    n = con.execute("SELECT count(*) FROM mart_gvc_loo WHERE edition = ?", [args.edition]).fetchone()[0]
    log.info(f"  mart_gvc_loo {n:,}행 (core {n_core:,}행에서, edition={args.edition})")

    log.info("\n[표본 확인] 한국 C26 — 자국 값 대 타국 평균")
    for r in con.execute("""
        SELECT c.year, round(c.backward,4), round(l.loo_w_backward,4), round(l.loo_m_backward,4), l.n_others
        FROM mart_gvc_core c JOIN mart_gvc_loo l USING (edition, method_version, year, cty, ind)
        WHERE c.cty='KOR' AND c.ind='C26' AND c.edition=? AND c.year IN (2000,2010,2019,2022) ORDER BY c.year
    """, [args.edition]).fetchall():
        log.info(f"  {r[0]}  한국 후방 {r[1]}   타국 수출가중 {r[2]}   타국 단순 {r[3]}  (타국 {r[4]}개)")

    # 확장판이면 같은 경제권의 개체가 같은 값을 갖는지 확인한다
    same = con.execute("""
        SELECT count(*) FROM (
            SELECT l.year, l.ind, e.economy, count(DISTINCT round(l.loo_w_backward, 12)) k
            FROM mart_gvc_loo l JOIN dim_icio_entity e ON e.edition=l.edition AND e.code=l.cty
            WHERE l.edition=? GROUP BY 1,2,3 HAVING k > 1)""", [args.edition]).fetchone()[0]
    log.info(f"  같은 경제권 안에서 값이 갈리는 (연도,산업,경제권) 조합: {same}개 (0이어야 한다)")

    con.execute("CHECKPOINT")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

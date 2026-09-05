"""
06b_compute_import_va.py — 수입에 체화된 상대국 부가가치 (mart_gvc_import_va)

왜 따로 필요한가 (docs/method.md §9)
  mart_gvc_bilateral 의 fva_from_partner 는 **수출**에 체화된 상대국 부가가치다.
  총액 기준 대일 의존도(한국의 대일 수입액 / 한국의 총수입액)와 견주려면 모집단이 같아야 하는데,
  수출 쪽 척도는 (가) 수입 중 수출에 실려 나가는 부분만 담고 (나) 수출 구성으로 가중된다.
  그래서 두 척도의 차이가 「총액 대 부가가치」 하나가 아니라 셋이 되어 대조가 교란된다.
  이 표는 **수입**을 부가가치 발생지별로 분해해 그 교란을 없앤다. 남는 차이는 총액 대 부가가치뿐이다.

산식 (docs/method.md §9)
  M[si, d] = Σ_j Z[si, dj] + F[si, d]        경제권 d 가 개체·산업 si 에서 들여온 금액
                                             (d 자신의 산출은 수입이 아니므로 0 으로 둔다)
  src_share[si, p] = Σ_{k∈p} v[k] B[k, si]   si 산출 1단위에 담긴 경제권 p 의 부가가치
                                             ** 마스크 없는 값이다.** bilateral() 이 저장하는
                                             fva_from_partner_share 는 자기 경제권을 0 으로 지우므로
                                             일본에서 직접 들여온 물량의 일본 부가가치가 사라진다.
  va_from_partner[d, p] = Σ_si M[si, d] · src_share[si, p]
  imp_from_partner[d, p] = Σ_{si ∈ p} M[si, d]        같은 모집단의 총액 기준 값

  Σ_p src_share[si, p] = 1 이므로 Σ_p va_from_partner[d, p] = d 의 총수입이다. 적재 후 확인한다.
  p = d 인 항은 수입에 실려 돌아온 자국 부가가치(RDV)다. 지우지 않고 저장한다.

입력: mart_gvc_core 가 아니라 원표(fact_icio_*). 06 의 load_year/compute 를 그대로 쓴다.
출력: mart_gvc_import_va

실행:
  python scripts\\06b_compute_import_va.py --edition ICIO2025_EXT
  python scripts\\06b_compute_import_va.py --edition ICIO2025_EXT --years 2018 2022
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import DB_PATH, EDITION, setup_logging  # noqa: E402

_m06 = importlib.import_module("06_compute_gvc")
load_year, compute, METHOD_VERSION = _m06.load_year, _m06.compute, _m06.METHOD_VERSION

log = setup_logging("compute_import_va")

DDL = """
CREATE TABLE IF NOT EXISTS mart_gvc_import_va (
    edition VARCHAR, method_version VARCHAR, year SMALLINT,
    cty VARCHAR, partner VARCHAR,
    imp_from_partner DOUBLE, va_from_partner DOUBLE
)"""


def import_va(res, ctys, inds, econs, econ_of_entity):
    """(수입국 경제권 × 부가가치 발생 경제권) 두 장을 낸다."""
    G, N = len(ctys), len(inds)
    GN = G * N
    E = len(econs)

    fold = np.zeros((G, E))
    fold[np.arange(G), econ_of_entity] = 1.0

    # si → 경제권 d 로 간 금액 (중간재 + 최종재). 자기 경제권행은 수입이 아니다.
    M = res["Z"].reshape(GN, G, N).sum(axis=2) @ fold + res["F"] @ fold      # GN x E
    same = res["econ_cell"][:, None] == np.arange(E)[None, :]
    M = np.where(same, 0.0, M)

    # si 산출 1단위에 담긴 경제권 p 의 부가가치 — 마스크하지 않는다
    src_share = (res["vB"].reshape(G, N, GN).sum(axis=1).T) @ fold           # GN x E

    onehot = np.zeros((GN, E))
    onehot[np.arange(GN), res["econ_cell"]] = 1.0

    imp = M.T @ onehot          # E x E : 수입국 d 행, 원산지 경제권 p 열 (총액)
    va = M.T @ src_share        # E x E : 수입국 d 행, 부가가치 발생 p 열
    return imp, va


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", default=EDITION)
    ap.add_argument("--years", type=int, nargs="*")
    args = ap.parse_args()

    con = duckdb.connect(str(DB_PATH))
    con.execute(DDL)

    ent = con.execute(
        "SELECT code, economy FROM dim_icio_entity WHERE edition=? ORDER BY code", [args.edition]).df()
    ctys = ent["code"].tolist()
    econs = sorted(ent["economy"].unique())
    econ_index = {e: i for i, e in enumerate(econs)}
    econ_of_entity = np.array([econ_index[e] for e in ent["economy"]])
    inds = [r[0] for r in con.execute(
        "SELECT icio_ind FROM dim_icio_ind WHERE edition=? ORDER BY ind_no", [args.edition]).fetchall()]
    G, N, E = len(ctys), len(inds), len(econs)
    log.info(f"격자: 개체 {G} × 산업 {N},  경제권 {E}개")

    years = args.years or [r[0] for r in con.execute(
        "SELECT DISTINCT year FROM fact_icio_va WHERE edition=? ORDER BY year", [args.edition]).fetchall()]
    if not years:
        log.error("fact 테이블이 비었다 — 03_parquet_to_duckdb.py 를 먼저 돌릴 것")
        return 1

    yr_list = ",".join(str(y) for y in years)
    con.execute(
        f"DELETE FROM mart_gvc_import_va WHERE edition=? AND method_version=? AND year IN ({yr_list})",
        [args.edition, METHOD_VERSION])

    ec = np.array(econs)
    worst = 0.0
    for year in years:
        t0 = time.time()
        Z, F, X, VA, TLS = load_year(con, args.edition, year, ctys, inds)
        r = compute(Z, F, X, VA, TLS, G, N, econ_of_entity)
        imp, va = import_va(r, ctys, inds, econs, econ_of_entity)

        # Σ_p va[d,p] 는 d 의 총수입과 같아야 한다 (Σ_p src_share = 1)
        tot_i, tot_v = imp.sum(axis=1), va.sum(axis=1)
        ok = tot_i > 0
        rel = np.abs(tot_v[ok] - tot_i[ok]) / tot_i[ok]
        worst = max(worst, float(rel.max()))

        keep = (imp != 0) | (va != 0)
        di, pi = np.nonzero(keep)
        df = pd.DataFrame({
            "edition": args.edition, "method_version": METHOD_VERSION, "year": year,
            "cty": ec[di], "partner": ec[pi],
            "imp_from_partner": imp[di, pi], "va_from_partner": va[di, pi],
        })
        con.register("_i", df)
        con.execute("INSERT INTO mart_gvc_import_va SELECT * FROM _i")
        con.unregister("_i")
        log.info(f"  {year}: {len(df):,}행  분해 상대오차 최댓값 {rel.max():.2e}  "
                 f"({time.time() - t0:.0f}s)")

    con.execute("CHECKPOINT")
    log.info(f"\n[검증] Σ_p 부가가치 = 총수입, 전 연도 상대오차 최댓값 {worst:.2e}")

    log.info("\n[표본 확인] 한국의 대일 의존, 총액 기준과 부가가치 기준")
    for row in con.execute("""
        WITH t AS (
          SELECT year,
                 sum(imp_from_partner) FILTER (partner='JPN') AS ig,
                 sum(imp_from_partner)                         AS it,
                 sum(va_from_partner)  FILTER (partner='JPN') AS vg,
                 sum(va_from_partner)  FILTER (partner<>'KOR') AS vt
          FROM mart_gvc_import_va
          WHERE edition=? AND method_version=? AND cty='KOR' AND year BETWEEN 2014 AND 2022
          GROUP BY 1)
        SELECT year, round(ig/it*100,2), round(vg/vt*100,2) FROM t ORDER BY year
    """, [args.edition, METHOD_VERSION]).fetchall():
        log.info(f"  {row[0]}  총액 {row[1]:>5}%   부가가치 {row[2]:>5}%")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

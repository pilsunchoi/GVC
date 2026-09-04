"""
06_compute_gvc.py — 표준 GVC 지표 산출 (mart_gvc_core, mart_gvc_bilateral)

설계 원칙 (docs/DB_구축_원칙.md §5.3, §5.4):
- 계산된 지표는 mart 계층에 둔다. fact 는 건드리지 않는다.
- 모든 행이 edition 과 method_version 을 갖는다. 산식을 고치면 method_version 을 올린다.
- 산식은 docs/method.md 에 적고 여기에 절 번호를 단다.

산식 (docs/method.md §1)
  A[k,j] = Z[k,j] / X[j]                        투입계수
  B      = (I - A)^{-1}                          레온티에프 역행렬
  v[j]   = 1 - Σ_k A[k,j] = (VA[j] + TLS[j]) / X[j]
           ** TLS 를 부가가치에 포함시킨다.** 그래야 v'B = 1' 이 성립해 수출이 남김없이 분해된다.
           포함하지 않으면 DVA + FVA < 총수출이 된다. TiVA 대조(08)에서 이 선택을 확인한다.
  E[s,i] = Σ_{r≠s} Σ_j Z[si,rj] + Σ_{r≠s} F[si,r]     총수출(중간재 + 최종재)
  DVA 비중  = Σ_{k∈s} v[k] B[k,si]               자국 부가가치 비중 (= EXGR_DVASH 대응)
  FVA 비중  = 1 - DVA 비중 = 후방참여도
  전방참여도 = v[si] · Σ_{r≠s} Σ_j B[si,rj] E[rj] / E[si]
              자국 (s,i) 의 부가가치가 타국 수출에 실려 나간 몫
  상류도 U  = (I - Δ)^{-1} 1,  Δ[i,j] = Z[i,j] / X[i]    (Antràs–Chor–Fally–Hillberry)
  하류도 D  = Σ_i B[i,j]                                  (Antràs–Chor, 원시 투입으로부터의 거리)

입력: data/processed/gvc.duckdb 의 fact_icio_z, fact_icio_fd, fact_icio_va
출력: mart_gvc_core, mart_gvc_bilateral

실행:
  python scripts\\06_compute_gvc.py
  python scripts\\06_compute_gvc.py --years 2019 2020 --no-bilateral
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import DB_PATH, EDITION, setup_logging  # noqa: E402

log = setup_logging("compute_gvc")

METHOD_VERSION = "v1"

CORE_DDL = """
CREATE TABLE IF NOT EXISTS mart_gvc_core (
    edition VARCHAR, method_version VARCHAR, year SMALLINT,
    cty VARCHAR, ind VARCHAR,
    out DOUBLE, va DOUBLE, tls DOUBLE, v DOUBLE,
    exgr DOUBLE, exgr_int DOUBLE, exgr_fin DOUBLE,
    dva_share DOUBLE, fva_share DOUBLE,
    backward DOUBLE, forward DOUBLE, dvx DOUBLE,
    upstreamness DOUBLE, downstreamness DOUBLE
)"""

BILAT_DDL = """
CREATE TABLE IF NOT EXISTS mart_gvc_bilateral (
    edition VARCHAR, method_version VARCHAR, year SMALLINT,
    cty VARCHAR, ind VARCHAR, partner VARCHAR,
    exgr_to_partner DOUBLE, fva_from_partner DOUBLE, fva_from_partner_share DOUBLE
)"""


def load_year(con, edition: str, year: int, ctys: list[str], inds: list[str]):
    """한 해치를 조밀 행렬로 올린다. 없는 조합은 0 이다(§1.2 라)."""
    G, N = len(ctys), len(inds)
    GN = G * N
    ci = {(c, i): g * N + n for g, c in enumerate(ctys) for n, i in enumerate(inds)}
    cidx = {c: g for g, c in enumerate(ctys)}

    Z = np.zeros((GN, GN))
    z = con.execute(
        "SELECT src_cty, src_ind, dst_cty, dst_ind, value FROM fact_icio_z WHERE edition=? AND year=?",
        [edition, year],
    ).df()
    Z[
        [ci[(a, b)] for a, b in zip(z.src_cty, z.src_ind)],
        [ci[(a, b)] for a, b in zip(z.dst_cty, z.dst_ind)],
    ] = z.value.to_numpy()

    F = np.zeros((GN, G))
    f = con.execute(
        "SELECT src_cty, src_ind, dst_cty, sum(value) v FROM fact_icio_fd "
        "WHERE edition=? AND year=? GROUP BY 1,2,3",
        [edition, year],
    ).df()
    np.add.at(
        F,
        ([ci[(a, b)] for a, b in zip(f.src_cty, f.src_ind)], [cidx[c] for c in f.dst_cty]),
        f.v.to_numpy(),
    )

    va = con.execute(
        "SELECT cty, ind, tls, va, out FROM fact_icio_va WHERE edition=? AND year=?", [edition, year]
    ).df()
    X = np.zeros(GN)
    VA = np.zeros(GN)
    TLS = np.zeros(GN)
    pos = [ci[(a, b)] for a, b in zip(va.cty, va.ind)]
    X[pos] = va.out.to_numpy()
    VA[pos] = va.va.to_numpy()
    TLS[pos] = va.tls.to_numpy()
    return Z, F, X, VA, TLS


def compute(Z, F, X, VA, TLS, G, N, econ_of_entity):
    """econ_of_entity: 길이 G 의 정수 배열. 같은 경제권에 속한 개체는 같은 번호를 갖는다.

    표준판에서는 개체 하나가 곧 경제권이라 항등이다.
    확장판에서는 CN1·CN2 가 같은 번호(중국), MX1·MX2 가 같은 번호(멕시코)를 갖는다.
    **"자국"과 "수출"의 경계를 개체가 아니라 이 경제권으로 잡는다.**
    그러지 않으면 CN2 의 부가가치가 CN1 수출에서 외국 부가가치로 잡히고,
    CN1→CN2 국내 거래가 수출로 잡힌다.
    """
    GN = G * N
    safe = np.where(X > 0, X, 1.0)

    A = Z / safe[None, :]
    A[:, X <= 0] = 0.0
    v = 1.0 - A.sum(axis=0)
    v[X <= 0] = 0.0

    B = np.linalg.inv(np.eye(GN) - A)

    # 경제권 블록 마스크 — (s,i) 와 같은 경제권에 속한 행/열
    econ_cell = np.repeat(econ_of_entity, N)              # 길이 GN
    own = econ_cell[:, None] == econ_cell[None, :]        # GN x GN

    # 총수출: 중간재(다른 경제권 산업으로) + 최종재(다른 경제권 최종수요로)
    exgr_int = Z.sum(axis=1) - np.where(own, Z, 0.0).sum(axis=1)
    own_fd = econ_cell[:, None] == econ_of_entity[None, :]   # GN x G
    exgr_fin = F.sum(axis=1) - np.where(own_fd, F, 0.0).sum(axis=1)
    E = exgr_int + exgr_fin

    # 부가가치 원천: vB[k, si] = 산업 si 총산출 1단위에 실린 k 의 부가가치
    vB = v[:, None] * B
    dva_share = np.where(own, vB, 0.0).sum(axis=0)  # 자국 원천 합
    dva_share[X <= 0] = np.nan
    fva_share = np.where(np.isnan(dva_share), np.nan, 1.0 - dva_share)

    # 전방참여도: (s,i) 의 부가가치가 타국 수출에 실린 몫
    BE = B @ E
    BE_own = np.where(own, B, 0.0) @ E
    dvx = v * (BE - BE_own)
    with np.errstate(divide="ignore", invalid="ignore"):
        forward = np.where(E > 0, dvx / E, np.nan)

    # 상류도 (ACFH): Δ[i,j] = Z[i,j] / X[i]
    Delta = Z / safe[:, None]
    Delta[X <= 0, :] = 0.0
    U = np.linalg.solve(np.eye(GN) - Delta, np.ones(GN))
    U[X <= 0] = np.nan

    # 하류도 (Antràs–Chor): D[j] = Σ_i B[i,j]
    D = B.sum(axis=0)
    D[X <= 0] = np.nan

    return dict(v=v, exgr=E, exgr_int=exgr_int, exgr_fin=exgr_fin,
                dva_share=dva_share, fva_share=fva_share, forward=forward, dvx=dvx,
                up=U, down=D, vB=vB, Z=Z, F=F, own=own, own_fd=own_fd,
                econ_cell=econ_cell, econ_of_entity=econ_of_entity)


def bilateral(res, ctys, inds, econs):
    """상대국별: 총수출과 상대국 부가가치 함량. **상대는 경제권 단위**로 낸다.

    확장판에서 CN1·CN2 를 따로 두면 상대국 목록이 실제 나라와 어긋난다.
    합쳐서 CHN 하나로 낸다(표준판에서는 개체가 곧 경제권이라 달라지는 것이 없다).
    """
    G, N = len(ctys), len(inds)
    GN = G * N
    Z, F = res["Z"], res["F"]
    econ_of_entity = res["econ_of_entity"]
    E = len(econs)

    # 개체 축을 경제권 축으로 접는 행렬 (G x E)
    fold = np.zeros((G, E))
    fold[np.arange(G), econ_of_entity] = 1.0

    # (s,i) → 경제권 r 총수출 = 중간재(r 의 모든 산업으로) + 최종재(r 최종수요로)
    exgr_to = Z.reshape(GN, G, N).sum(axis=2) @ fold + F @ fold      # GN x E
    # 상대 경제권 r 의 부가가치 함량 비중 = Σ_{k∈r} v_k B[k,si]
    src_share = (res["vB"].reshape(G, N, GN).sum(axis=1).T) @ fold   # GN x E

    # 자기 경제권으로 가는 것은 수출이 아니고, 자기 부가가치는 FVA 가 아니다
    same = res["econ_cell"][:, None] == np.arange(E)[None, :]        # GN x E
    exgr_to = np.where(same, 0.0, exgr_to)
    fva_share_by = np.where(same, 0.0, src_share)
    return exgr_to, fva_share_by


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", default=EDITION)
    ap.add_argument("--years", type=int, nargs="*")
    ap.add_argument("--no-bilateral", action="store_true")
    args = ap.parse_args()

    con = duckdb.connect(str(DB_PATH))
    con.execute(CORE_DDL)
    con.execute(BILAT_DDL)

    ent = con.execute(
        "SELECT code, economy FROM dim_icio_entity WHERE edition=? ORDER BY code", [args.edition]).df()
    ctys = ent["code"].tolist()
    econs = sorted(ent["economy"].unique())
    econ_index = {e: i for i, e in enumerate(econs)}
    econ_of_entity = np.array([econ_index[e] for e in ent["economy"]])
    inds = [r[0] for r in con.execute(
        "SELECT icio_ind FROM dim_icio_ind WHERE edition=? ORDER BY ind_no", [args.edition]).fetchall()]
    G, N = len(ctys), len(inds)
    log.info(f"격자: 개체 {G} × 산업 {N} = {G * N},  경제권 {len(econs)}개")
    if len(econs) != G:
        merged = ent[ent["code"] != ent["economy"]]
        log.info("  분할 개체를 경제권으로 묶는다: "
                 + ", ".join(f"{r.code}→{r.economy}" for r in merged.itertuples()))

    years = args.years or [r[0] for r in con.execute(
        "SELECT DISTINCT year FROM fact_icio_va WHERE edition=? ORDER BY year", [args.edition]).fetchall()]
    if not years:
        log.error("fact 테이블이 비었다 — 03_parquet_to_duckdb.py 를 먼저 돌릴 것")
        return 1

    # --years 로 일부만 돌릴 때 나머지 연도를 지우면 안 된다. 지울 범위를 연도로 좁힌다.
    yr_list = ",".join(str(y) for y in years)
    con.execute(f"DELETE FROM mart_gvc_core WHERE edition=? AND method_version=? AND year IN ({yr_list})",
                [args.edition, METHOD_VERSION])
    if not args.no_bilateral:
        con.execute(
            f"DELETE FROM mart_gvc_bilateral WHERE edition=? AND method_version=? AND year IN ({yr_list})",
            [args.edition, METHOD_VERSION])

    cty_col = np.repeat(ctys, N)
    ind_col = np.tile(inds, G)

    for year in years:
        t0 = time.time()
        Z, F, X, VA, TLS = load_year(con, args.edition, year, ctys, inds)
        t1 = time.time()
        r = compute(Z, F, X, VA, TLS, G, N, econ_of_entity)
        t2 = time.time()

        core = pd.DataFrame({
            "edition": args.edition, "method_version": METHOD_VERSION, "year": year,
            "cty": cty_col, "ind": ind_col,
            "out": X, "va": VA, "tls": TLS, "v": r["v"],
            "exgr": r["exgr"], "exgr_int": r["exgr_int"], "exgr_fin": r["exgr_fin"],
            "dva_share": r["dva_share"], "fva_share": r["fva_share"],
            "backward": r["fva_share"], "forward": r["forward"], "dvx": r["dvx"],
            "upstreamness": r["up"], "downstreamness": r["down"],
        })
        con.register("_c", core)
        con.execute("INSERT INTO mart_gvc_core SELECT * FROM _c")
        con.unregister("_c")

        nb = 0
        if not args.no_bilateral:
            exgr_to, fva_share_by = bilateral(r, ctys, inds, econs)
            keep = (exgr_to != 0) | (fva_share_by != 0)
            ri, ci = np.nonzero(keep)
            bil = pd.DataFrame({
                "edition": args.edition, "method_version": METHOD_VERSION, "year": year,
                "cty": cty_col[ri], "ind": ind_col[ri], "partner": np.array(econs)[ci],
                "exgr_to_partner": exgr_to[ri, ci],
                "fva_from_partner": fva_share_by[ri, ci] * r["exgr"][ri],
                "fva_from_partner_share": fva_share_by[ri, ci],
            })
            con.register("_b", bil)
            con.execute("INSERT INTO mart_gvc_bilateral SELECT * FROM _b")
            con.unregister("_b")
            nb = len(bil)

        log.info(f"  {year}: core {len(core):,}행 bilateral {nb:,}행  "
                 f"(적재 {t1 - t0:.0f}s 계산 {t2 - t1:.0f}s 총 {time.time() - t0:.0f}s)")

    con.execute("CHECKPOINT")
    # 눈으로 확인 — 한국 반도체·자동차의 후방참여도
    log.info("\n[표본 확인] 한국 C26(전자·광학), C29(자동차)")
    for row in con.execute("""
        SELECT year, ind, round(exgr,0) exgr, round(dva_share,4) dva, round(backward,4) bwd,
               round(forward,4) fwd, round(upstreamness,3) up
        FROM mart_gvc_core WHERE edition=? AND method_version=? AND cty='KOR'
          AND ind IN ('C26','C29') AND year IN (2000,2010,2019,2022) ORDER BY ind, year
    """, [args.edition, METHOD_VERSION]).fetchall():
        log.info(f"  {row[0]} {row[1]:<5} 수출{row[2]:>12,.0f}  DVA {row[3]}  후방 {row[4]}  전방 {row[5]}  상류도 {row[6]}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

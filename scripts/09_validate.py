"""
09_validate.py — 통합 검증 (단일 진입점)

설계 원칙 (docs/DB_구축_원칙.md §6):
- 검증을 여러 파일로 분열시키지 않는다. 이것 하나다.
- 각 항목은 PASS / WARN / FAIL. WARN 은 "설계상 예상된 불완전", FAIL 은 무결성 위반.
- FAIL 이 있으면 종료코드 1.
- **균형 항등식은 상대오차로 본다**(§1.2 마). ICIO 는 소수 4자리로 반올림돼 배포되므로
  절대 허용치로 재면 무조건 실패한다.
- **개수만 보지 않는다.** 미매칭은 금액 비중으로도 잰다(KCSDB2 봉인2 교훈).

실행:
  python scripts\\09_validate.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import DB_PATH, EDITION, setup_logging  # noqa: E402

log = setup_logging("validate")

# 균형 항등식 허용치 (§1.2 마). 실측으로 정했다 — 2026-09-04 로그 참조.
REL_TOL = 1e-4          # 규모 있는 칸에 적용하는 상대 허용치
ABS_TOL = 0.05          # 작은 칸에 적용하는 절대 허용치 (백만 USD = 5만 달러)
REL_TOL_FAIL = 1e-3     # 이보다 크면 FAIL. ICIO 원표 자체가 1e-3 안에서만 균형한다
MATERIAL = 1000.0       # 산출 10억 달러 초과 칸만 판정에 쓴다

results: list[tuple[str, str, str, str]] = []


def record(cat: str, name: str, status: str, detail: str = "") -> None:
    results.append((cat, name, status, detail))
    mark = {"PASS": "✓", "WARN": "△", "FAIL": "✗", "INFO": "·"}[status]
    log.info(f"  {mark} [{status}] {name}: {detail}")


def check_schema(con, ed: str) -> None:
    log.info("\n[1] 스키마·구조")
    expect = {
        "fact_icio_z": {"edition", "year", "src_cty", "src_ind", "dst_cty", "dst_ind", "value"},
        "fact_icio_fd": {"edition", "year", "src_cty", "src_ind", "dst_cty", "fd_type", "value"},
        "fact_icio_va": {"edition", "year", "cty", "ind", "tls", "va", "out"},
    }
    for t, exp in expect.items():
        cols = {r[0] for r in con.execute(f"DESCRIBE {t}").fetchall()}
        if cols == exp:
            record("schema", f"{t} 스키마", "PASS", f"{len(cols)}컬럼, 파생 없음")
        else:
            record("schema", f"{t} 스키마", "FAIL",
                   f"과잉 {sorted(cols - exp)} 누락 {sorted(exp - cols)}")


def check_grid(con, ed: str) -> None:
    log.info("\n[2] 격자 완전성")
    n_ent = con.execute("SELECT count(*) FROM dim_icio_entity WHERE edition=?", [ed]).fetchone()[0]
    n_ind = con.execute("SELECT count(*) FROM dim_icio_ind WHERE edition=?", [ed]).fetchone()[0]
    n_fd = con.execute("SELECT count(*) FROM dim_icio_fd").fetchone()[0]
    n_econ = con.execute(
        "SELECT count(DISTINCT economy) FROM dim_icio_entity WHERE edition=?", [ed]).fetchone()[0]
    ok = n_ent > 0 and n_ind == 50 and n_fd == 6
    record("grid", "dim 크기", "PASS" if ok else "WARN",
           f"개체 {n_ent}, 경제권 {n_econ}, 산업 {n_ind}, 최종수요유형 {n_fd}")
    if n_econ != n_ent:
        split = con.execute(
            "SELECT code, economy FROM dim_icio_entity WHERE edition=? AND code<>economy ORDER BY code",
            [ed]).fetchall()
        record("grid", "  분할 개체", "INFO", str(split))

    bad = con.execute("""
        SELECT year, count(*) FROM fact_icio_va WHERE edition=? GROUP BY year
        HAVING count(*) <> ? ORDER BY year""", [ed, n_ent * n_ind]).fetchall()
    record("grid", "연도별 국가×산업 칸 수", "PASS" if not bad else "FAIL",
           f"모든 연도 {n_ent * n_ind}칸" if not bad else f"어긋난 연도 {bad[:5]}")

    years = [r[0] for r in con.execute(
        "SELECT DISTINCT year FROM fact_icio_va WHERE edition=? ORDER BY year", [ed]).fetchall()]
    gaps = [y for y in range(min(years), max(years) + 1) if y not in years] if years else []
    record("grid", "연도 연속성", "PASS" if not gaps else "FAIL",
           f"{min(years)}–{max(years)} {len(years)}개 연도, 빠짐 없음" if not gaps else f"빠진 해 {gaps}")


def _balance(con, ed: str, name: str, sql_lhs: str) -> None:
    """열/행 균형을 한 규칙으로 잰다.

    한 칸이 통과하는 조건: |오차| <= max(ABS_TOL, REL_TOL x 산출)
      - 작은 칸은 상대오차가 과장된다. 산출 400달러짜리 칸(STP C301)의 0.0003 오차가 75%다.
      - 큰 칸은 절대오차가 커 보인다. 4,536개 항을 더하니 반올림이 쌓인다.
    판정은 **규모 있는 칸**(산출 > 10억 달러)으로만 한다. 등급은 그 칸들의 최대 상대오차로 매긴다.
    """
    d = con.execute(f'''
        WITH j AS ({sql_lhs})
        SELECT count(*), 
               max(abs(lhs-out)),
               max(CASE WHEN out > {MATERIAL} THEN abs(lhs-out)/out END),
               sum(CASE WHEN abs(lhs-out) > greatest({ABS_TOL}, {REL_TOL}*out) THEN 1 ELSE 0 END),
               sum(CASE WHEN out > {MATERIAL} THEN 1 ELSE 0 END),
               sum(CASE WHEN out > {MATERIAL}
                        AND abs(lhs-out) > greatest({ABS_TOL}, {REL_TOL}*out) THEN 1 ELSE 0 END)
        FROM j''').fetchone()
    n_all, max_abs, max_rel_big, viol_all, n_big, viol_big = d
    status = "PASS" if (max_rel_big or 0) <= REL_TOL_FAIL else "FAIL"
    if status == "PASS" and viol_big:
        status = "WARN"
    record("balance", name, status,
           f"{n_all:,}칸(규모 있는 칸 {n_big:,}), 절대오차 최대 {max_abs:.2f}, "
           f"규모 칸 상대오차 최대 {max_rel_big:.2e}, 허용 규칙 위반 {viol_big:,}칸(전체 {viol_all:,})")

    if viol_big:
        top = con.execute(f'''
            WITH j AS ({sql_lhs})
            SELECT ind, count(*) n FROM j
            WHERE out > {MATERIAL} AND abs(lhs-out) > greatest({ABS_TOL}, {REL_TOL}*out)
            GROUP BY 1 ORDER BY 2 DESC LIMIT 5''').fetchall()
        record("balance", f"  {name} 위반이 몰린 산업", "INFO", str(top))


def check_balance(con, ed: str) -> None:
    log.info("\n[3] 균형 항등식")
    log.info(f"    규칙: |오차| <= max({ABS_TOL}, {REL_TOL:g} x 산출). 판정은 산출 > {MATERIAL:,.0f}(백만 달러) 칸으로.")

    col_sql = f"""
        SELECT v.year, v.cty, v.ind, v.out, COALESCE(zc.s,0) + v.tls + v.va AS lhs
        FROM fact_icio_va v
        LEFT JOIN (SELECT year, dst_cty c, dst_ind i, sum(value) s FROM fact_icio_z
                   WHERE edition='{ed}' GROUP BY 1,2,3) zc
               ON zc.year=v.year AND zc.c=v.cty AND zc.i=v.ind
        WHERE v.edition='{ed}'"""
    _balance(con, ed, "열 균형 ΣZ+TLS+VA=OUT", col_sql)

    row_sql = f"""
        SELECT v.year, v.cty, v.ind, v.out, COALESCE(zr.s,0)+COALESCE(fr.s,0) AS lhs
        FROM fact_icio_va v
        LEFT JOIN (SELECT year, src_cty c, src_ind i, sum(value) s FROM fact_icio_z
                   WHERE edition='{ed}' GROUP BY 1,2,3) zr
               ON zr.year=v.year AND zr.c=v.cty AND zr.i=v.ind
        LEFT JOIN (SELECT year, src_cty c, src_ind i, sum(value) s FROM fact_icio_fd
                   WHERE edition='{ed}' GROUP BY 1,2,3) fr
               ON fr.year=v.year AND fr.c=v.cty AND fr.i=v.ind
        WHERE v.edition='{ed}'"""
    _balance(con, ed, "행 균형 ΣZ+ΣFD=OUT", row_sql)

    w = con.execute(f"""
        WITH a AS (SELECT year, sum(va+tls) s FROM fact_icio_va WHERE edition='{ed}' GROUP BY 1),
             b AS (SELECT year, sum(value) s FROM fact_icio_fd WHERE edition='{ed}' GROUP BY 1)
        SELECT max(abs(a.s-b.s)/b.s), count(*) FROM a JOIN b USING (year)""").fetchone()
    record("balance", "세계 Σ(VA+TLS)=Σ최종수요", "PASS" if w[0] < REL_TOL_FAIL else "WARN",
           f"{w[1]}개 연도, 상대오차 최대 {w[0]:.2e}")


def check_values(con, ed: str) -> None:
    log.info("\n[4] 값의 성질")
    neg_z = con.execute(f"SELECT count(*) FROM fact_icio_z WHERE edition='{ed}' AND value<0").fetchone()[0]
    record("values", "Z 음수 없음", "PASS" if neg_z == 0 else "FAIL", f"음수 {neg_z:,}칸")

    negfd = con.execute(f"""SELECT fd_type, count(*) FROM fact_icio_fd
                            WHERE edition='{ed}' AND value<0 GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    others = [r for r in negfd if r[0] != "INVNT"]
    record("values", "최종수요 음수는 INVNT 뿐", "PASS" if not others else "WARN",
           f"{dict(negfd)}" if negfd else "음수 없음")

    zero = con.execute(f"SELECT count(*) FROM fact_icio_z WHERE edition='{ed}' AND value=0").fetchone()[0]
    record("values", "정확한 0 은 저장하지 않음(§1.2 라)", "PASS" if zero == 0 else "FAIL",
           f"0 값 {zero:,}행")


def check_dim_match(con, ed: str) -> None:
    log.info("\n[5] fact → dim 매칭 (개수와 금액 둘 다)")
    for tbl, col, dim, dcol in [
        ("fact_icio_va", "cty", "dim_icio_entity", "code"),
        ("fact_icio_va", "ind", "dim_icio_ind", "icio_ind"),
        ("fact_icio_fd", "fd_type", "dim_icio_fd", "fd_type"),
    ]:
        where_ed = f"AND d.edition='{ed}'" if dim != "dim_icio_fd" else ""
        r = con.execute(f"""
            SELECT count(*) FILTER (WHERE d.{dcol} IS NULL), count(*)
            FROM {tbl} f LEFT JOIN {dim} d ON d.{dcol}=f.{col} {where_ed}
            WHERE f.edition='{ed}'""").fetchone()
        record("match", f"{tbl}.{col} → {dim}", "PASS" if r[0] == 0 else "FAIL",
               f"미매칭 {r[0]:,} / {r[1]:,}행")


def check_maps(con, ed: str) -> None:
    log.info("\n[6] 분류 연계 (§4.4)")
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "map_ksic_icio" not in tables:
        record("map", "map_ksic_icio", "WARN", "아직 만들지 않았다 — 05_build_maps.py")
        return

    for rev in ("11", "10"):
        r = con.execute("""
            SELECT count(DISTINCT ksic) FILTER (WHERE n_icio > 1), count(DISTINCT ksic)
            FROM map_ksic_icio WHERE ksic_rev=? AND ksic_level='세세분류'""", [rev]).fetchone()
        record("map", f"KSIC {rev}차 세세분류 1:1 매핑", "PASS" if r[0] == 0 else "WARN",
               f"{r[1]:,}개 중 1:n {r[0]}개")

    # ICIO 대응이 없는 KSIC — ISIC 99 는 ICIO 50산업에 없다. 설계상 예상된 것이므로 WARN.
    for rev in ("11", "10"):
        n_all = con.execute("SELECT count(*) FROM dim_ksic WHERE ksic_rev=?", [rev]).fetchone()[0]
        n_map = con.execute("""SELECT count(DISTINCT ksic) FROM map_ksic_icio
                               WHERE ksic_rev=? AND ksic_level='세세분류'""", [rev]).fetchone()[0]
        record("map", f"KSIC {rev}차 세세분류 커버리지", "PASS" if n_all - n_map <= 2 else "WARN",
               f"{n_map:,}/{n_all:,} 매핑 (미매핑 {n_all - n_map}개 = 국제·외국기관 99xxx)")

    r = con.execute("""
        SELECT count(*) FROM dim_icio_ind i WHERE i.edition=?
          AND NOT EXISTS (SELECT 1 FROM map_ksic_icio m WHERE m.icio_ind=i.icio_ind)""", [ed]).fetchone()[0]
    record("map", "KSIC 이 닿지 않는 ICIO 산업", "PASS" if r == 0 else "WARN", f"{r}개")

    r = con.execute("""
        SELECT count(*) FROM dim_ksic d WHERE NOT EXISTS
          (SELECT 1 FROM map_ksic_vintage v WHERE v.from_rev=d.ksic_rev AND v.from_code=d.ksic)
    """).fetchone()[0]
    record("map", "KSIC 차수 브릿지 커버리지", "PASS" if r == 0 else "WARN", f"브릿지 없는 코드 {r}개")


def check_mart(con, ed: str) -> None:
    log.info("\n[7] mart 지표")
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "mart_gvc_core" not in tables:
        record("mart", "mart_gvc_core", "WARN", "아직 만들지 않았다 — 06_compute_gvc.py")
        return

    r = con.execute(f"""SELECT count(*), count(DISTINCT year), count(DISTINCT method_version)
                        FROM mart_gvc_core WHERE edition='{ed}'""").fetchone()
    record("mart", "mart_gvc_core 적재", "PASS" if r[0] > 0 else "FAIL",
           f"{r[0]:,}행, {r[1]}개 연도, method {r[2]}종")

    # DVA + FVA = 1 은 산식상 항등이지만 수치 오차를 확인한다
    r = con.execute(f"""SELECT max(abs(dva_share+fva_share-1)) FROM mart_gvc_core
                        WHERE edition='{ed}' AND dva_share IS NOT NULL""").fetchone()[0]
    record("mart", "DVA비중 + FVA비중 = 1", "PASS" if (r or 0) < 1e-9 else "FAIL", f"최대 편차 {r:.2e}")

    r = con.execute(f"""SELECT count(*) FROM mart_gvc_core WHERE edition='{ed}'
                        AND (dva_share < -1e-9 OR dva_share > 1+1e-9)""").fetchone()[0]
    record("mart", "DVA비중이 [0,1] 안", "PASS" if r == 0 else "WARN", f"벗어난 칸 {r}개")

    r = con.execute(f"""SELECT count(*) FROM mart_gvc_core
                        WHERE edition='{ed}' AND upstreamness IS NOT NULL AND upstreamness < 1-1e-6""").fetchone()[0]
    record("mart", "상류도 ≥ 1", "PASS" if r == 0 else "WARN", f"1 미만 {r}칸")

    # 총수출의 세계 합이 Z·FD 의 국경 넘는 합과 맞는가.
    # **경계는 개체가 아니라 경제권이다.** 확장판에서 CN1→CN2 는 국경을 넘지 않고,
    # CN1→CHN 최종수요도 국내 거래다(확장판은 생산 쪽만 쪼개고 최종수요 열은 CHN 에 그대로 둔다).
    r = con.execute(f"""
        WITH a AS (SELECT year, sum(exgr) s FROM mart_gvc_core WHERE edition='{ed}' GROUP BY 1),
             zx AS (SELECT z.year, sum(z.value) s FROM fact_icio_z z
                    JOIN dim_icio_entity p ON p.edition=z.edition AND p.code=z.src_cty
                    JOIN dim_icio_entity q ON q.edition=z.edition AND q.code=z.dst_cty
                    WHERE z.edition='{ed}' AND p.economy<>q.economy GROUP BY 1),
             fx AS (SELECT f.year, sum(f.value) s FROM fact_icio_fd f
                    JOIN dim_icio_entity p ON p.edition=f.edition AND p.code=f.src_cty
                    JOIN dim_icio_entity q ON q.edition=f.edition AND q.code=f.dst_cty
                    WHERE f.edition='{ed}' AND p.economy<>q.economy GROUP BY 1)
        SELECT max(abs(a.s-(zx.s+fx.s))/a.s) FROM a JOIN zx USING(year) JOIN fx USING(year)""").fetchone()[0]
    record("mart", "총수출 = 경제권 국경 넘는 Z + FD", "PASS" if (r or 1) < 1e-9 else "FAIL",
           f"상대오차 최대 {r:.2e}")

    if "mart_gvc_loo" in tables:
        r = con.execute("SELECT count(*), min(n_others), max(n_others) FROM mart_gvc_loo").fetchone()
        record("mart", "mart_gvc_loo 적재", "PASS" if r[0] > 0 else "WARN",
               f"{r[0]:,}행, 타국 수 {r[1]}–{r[2]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", default=EDITION)
    args = ap.parse_args()

    if not DB_PATH.exists():
        log.error(f"없음: {DB_PATH}")
        return 1
    con = duckdb.connect(str(DB_PATH), read_only=True)
    log.info(f"검증 대상: {DB_PATH}  edition={args.edition}  ({DB_PATH.stat().st_size / 1e9:.2f} GB)")

    for fn in (check_schema, check_grid, check_balance, check_values,
               check_dim_match, check_maps, check_mart):
        try:
            fn(con, args.edition)
        except Exception as e:  # noqa: BLE001
            record(fn.__name__, "검증 자체 실패", "FAIL", f"{type(e).__name__}: {e}")
    con.close()

    n = {s: sum(1 for r in results if r[2] == s) for s in ("PASS", "WARN", "FAIL", "INFO")}
    log.info(f"\n결과: PASS {n['PASS']}, WARN {n['WARN']}, FAIL {n['FAIL']}")
    if n["FAIL"]:
        for r in results:
            if r[2] == "FAIL":
                log.error(f"  FAIL — {r[1]}: {r[3]}")
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

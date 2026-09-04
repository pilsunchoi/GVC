"""
02_icio_to_parquet.py — ICIO 원본 CSV(zip 안) → 연도별 parquet (long)

설계 원칙 (docs/DB_구축_원칙.md §3, §5.1):
- 값을 변형하지 않는다. 반올림·균형화·보정 없음.
- 정확히 0인 칸만 뺀다. 격자는 dim 으로 완전히 복원되므로 무손실이다(§1.2 라).
  미세값은 자르지 않는다.
- 행에 있던 TLS·VA·OUT 은 국가×산업 축으로 돌려 담는다. 재배치이지 파생이 아니다.

원본 구조 (실측, §3.1):
  행 4,053 = 국가·산업 4,050 + TLS + VA + OUT
  열 4,538 = 라벨(V1) + 국가·산업 4,050 + 최종수요 486(81개체×6유형) + OUT

출력 (data/interim/icio2025/):
  z/z_<year>.parquet    src_cty, src_ind, dst_cty, dst_ind, value
  fd/fd_<year>.parquet  src_cty, src_ind, dst_cty, fd_type, value
  va/va_<year>.parquet  cty, ind, tls, va, out
  _labels.json          연도별 개체·산업·최종수요유형 목록 (04 가 dim 을 만들 때 쓴다)

실행:
  python scripts\\02_icio_to_parquet.py
  python scripts\\02_icio_to_parquet.py --years 2021 2022
  python scripts\\02_icio_to_parquet.py --variant EXT
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import FD_TYPES, RAW_DIR, interim_of, setup_logging  # noqa: E402

YEAR_GROUPS = {
    "1995-2000": range(1995, 2001),
    "2001-2005": range(2001, 2006),
    "2006-2010": range(2006, 2011),
    "2011-2015": range(2011, 2016),
    "2016-2022": range(2016, 2023),
}

log = setup_logging("icio_to_parquet")


def split_label(label: str) -> tuple[str, str]:
    """'AGO_A01' → ('AGO','A01'). 산업코드에도 밑줄이 있으므로(C17_18, J62_63) 왼쪽에서 한 번만 자른다."""
    cty, ind = label.split("_", 1)
    return cty, ind


def member_name(zf: zipfile.ZipFile, year: int, variant: str) -> str:
    """zip 안 파일 이름이 판마다 다르다. SML 은 '2022_SML.csv', EXT 는 '2022.csv' 다(실측)."""
    names = set(zf.namelist())
    for cand in (f"{year}_{variant}.csv", f"{year}.csv"):
        if cand in names:
            return cand
    raise KeyError(f"{year}년 CSV 를 찾지 못했다. zip 안: {sorted(names)[:5]}…")


def read_year(zip_path: Path, year: int, variant: str) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member_name(zf, year, variant)) as f:
            return pd.read_csv(io.TextIOWrapper(f, encoding="utf-8-sig"), index_col=0)


def convert_year(df: pd.DataFrame, year: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    cols = list(df.columns)
    fd_cols = [c for c in cols if c.rsplit("_", 1)[-1] in FD_TYPES]
    ci_cols = [c for c in cols if c != "OUT" and c not in fd_cols]
    ci_rows = [r for r in df.index if r not in ("TLS", "VA", "OUT")]

    if ci_rows != ci_cols:
        raise ValueError(f"{year}: 행 라벨과 열 라벨이 다르다 (행 {len(ci_rows)}, 열 {len(ci_cols)})")
    if "OUT" not in cols or "TLS" not in df.index or "VA" not in df.index:
        raise ValueError(f"{year}: TLS/VA/OUT 이 없다")

    src_cty = np.array([split_label(r)[0] for r in ci_rows])
    src_ind = np.array([split_label(r)[1] for r in ci_rows])

    # --- Z: 중간재 거래 ---
    Z = df.loc[ci_rows, ci_cols].to_numpy(dtype="float64")
    ri, ci = np.nonzero(Z)
    z = pd.DataFrame(
        {
            "src_cty": src_cty[ri],
            "src_ind": src_ind[ri],
            "dst_cty": src_cty[ci],
            "dst_ind": src_ind[ci],
            "value": Z[ri, ci],
        }
    )

    # --- FD: 최종수요 ---
    F = df.loc[ci_rows, fd_cols].to_numpy(dtype="float64")
    fd_cty = np.array([c.rsplit("_", 1)[0] for c in fd_cols])
    fd_typ = np.array([c.rsplit("_", 1)[1] for c in fd_cols])
    ri, ci = np.nonzero(F)
    fd = pd.DataFrame(
        {
            "src_cty": src_cty[ri],
            "src_ind": src_ind[ri],
            "dst_cty": fd_cty[ci],
            "fd_type": fd_typ[ci],
            "value": F[ri, ci],
        }
    )

    # --- VA: TLS·VA·OUT 을 국가×산업 축으로 (재배치) ---
    va = pd.DataFrame(
        {
            "cty": src_cty,
            "ind": src_ind,
            "tls": df.loc["TLS", ci_cols].to_numpy(dtype="float64"),
            "va": df.loc["VA", ci_cols].to_numpy(dtype="float64"),
            "out": df.loc["OUT", ci_cols].to_numpy(dtype="float64"),
        }
    )
    # 열의 OUT 과 행의 OUT 이 같은지 확인 (원본 무결성)
    out_row = df.loc[ci_rows, "OUT"].to_numpy(dtype="float64")
    if not np.array_equal(out_row, va["out"].to_numpy()):
        n = int((out_row != va["out"].to_numpy()).sum())
        log.warning(f"  {year}: 행 OUT 과 열 OUT 이 {n}칸 다르다 — 열 OUT 을 쓴다")

    labels = {
        "entities": sorted(set(src_cty.tolist())),
        "industries": sorted(set(src_ind.tolist())),
        "fd_types": sorted(set(fd_typ.tolist())),
        "n_ci": len(ci_rows),
    }
    return z, fd, va, labels


def write(df: pd.DataFrame, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pandas(df, preserve_index=False),
        path,
        compression="zstd",
        compression_level=9,
    )
    return path.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["SML", "EXT"], default="SML")
    ap.add_argument("--years", type=int, nargs="*", help="지정하지 않으면 전 연도")
    ap.add_argument("--out", help="지정하지 않으면 판별 폴더(data/interim/icio2025_<판>)")
    args = ap.parse_args()

    raw_dir = RAW_DIR / "icio2025"
    # 판마다 다른 폴더에 쌓는다. 같은 폴더를 쓰면 SML parquet 를 EXT 가 덮어쓴다.
    out_dir = Path(args.out) if args.out else interim_of(args.variant)
    want = set(args.years) if args.years else None

    labels_path = out_dir / "_labels.json"
    all_labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}

    total_bytes = 0
    done = 0
    for group, years in YEAR_GROUPS.items():
        targets = [y for y in years if want is None or y in want]
        if not targets:
            continue
        zip_path = raw_dir / f"{group}_{args.variant}.zip"
        if not zip_path.exists():
            log.error(f"없음: {zip_path} — 01_fetch_icio.py 를 먼저 돌릴 것")
            return 1
        for year in targets:
            t0 = time.time()
            df = read_year(zip_path, year, args.variant)
            z, fd, va, labels = convert_year(df, year)
            b = 0
            b += write(z, out_dir / "z" / f"z_{year}.parquet")
            b += write(fd, out_dir / "fd" / f"fd_{year}.parquet")
            b += write(va, out_dir / "va" / f"va_{year}.parquet")
            total_bytes += b
            all_labels[str(year)] = labels
            done += 1
            log.info(
                f"  {year}: z {len(z):>10,}행  fd {len(fd):>8,}행  va {len(va):>6,}행  "
                f"개체 {len(labels['entities'])} 산업 {len(labels['industries'])}  "
                f"{b / 1e6:5.1f}MB  {time.time() - t0:4.1f}s"
            )

    labels_path.parent.mkdir(parents=True, exist_ok=True)
    labels_path.write_text(json.dumps(all_labels, ensure_ascii=False, indent=1), encoding="utf-8")

    # 연도 간 라벨 일관성 — 다르면 격자가 흔들린다는 뜻이므로 반드시 본다
    keys = {k: (tuple(v["entities"]), tuple(v["industries"]), tuple(v["fd_types"])) for k, v in all_labels.items()}
    distinct = set(keys.values())
    if len(distinct) == 1:
        log.info(f"라벨 일관성: 전 연도 동일 (개체 {len(next(iter(distinct))[0])}, 산업 {len(next(iter(distinct))[1])})")
    else:
        log.warning(f"라벨 일관성: 연도별로 다르다 — 서로 다른 조합 {len(distinct)}종")
        for k, v in keys.items():
            log.warning(f"    {k}: 개체 {len(v[0])} 산업 {len(v[1])} FD {len(v[2])}")

    log.info(f"완료 {done}개 연도, parquet 합계 {total_bytes / 1e6:.0f}MB → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
common.py — 경로·로깅·해시 공통 유틸 (docs/DB_구축_원칙.md §7)

모든 빌드 스크립트가 이것을 쓴다. 경로를 각자 정의하지 않는다.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

RAW_DIR = PROJECT_ROOT / "data" / "raw"
EXTERNAL_DIR = PROJECT_ROOT / "data" / "external"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
LOG_DIR = PROJECT_ROOT / "logs"

DB_PATH = PROCESSED_DIR / "gvc.duckdb"

# 현행 판. 판이 바뀌면 여기가 아니라 스크립트 인자로 넘긴다 (판 간 결합 금지, §0-5)
EDITION = "ICIO2025"

# 브라우저 UA 없이는 OECD가 막는다 (§2)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

FD_TYPES = ("HFCE", "NPISH", "GGFC", "GFCF", "INVNT", "DPABR")


def setup_logging(name: str) -> logging.Logger:
    """콘솔 + logs/<name>_<타임스탬프>.log 동시 출력."""
    for d in (RAW_DIR, EXTERNAL_DIR, INTERIM_DIR, PROCESSED_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger(name)
    log.info(f"로그: {path}")
    return log


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ── 판(variant) 별 규약 ────────────────────────────────────────────────────
# SML = 표준판(80개국+ROW = 81개체), EXT = 확장판(중국·멕시코를 가공무역 기준으로 쪼갠 85개체).
# 둘은 서로 다른 자료다. edition 을 달리 붙이고 결합하지 않는다(§0-5).
VARIANTS = {"SML": "ICIO2025", "EXT": "ICIO2025_EXT"}


def edition_of(variant: str) -> str:
    return VARIANTS[variant]


def variant_of(edition: str) -> str:
    for v, e in VARIANTS.items():
        if e == edition:
            return v
    raise KeyError(f"모르는 edition: {edition}")


def interim_of(variant: str) -> "Path":
    return INTERIM_DIR / f"icio2025_{variant.lower()}"


def readme_of(variant: str) -> "Path":
    name = "ReadMe_ICIO_small.xlsx" if variant == "SML" else "ReadMe_ICIO_extended.xlsx"
    return RAW_DIR / "icio2025" / name

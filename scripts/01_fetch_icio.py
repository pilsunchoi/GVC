"""
01_fetch_icio.py — OECD ICIO 원본 내려받기 + 출처 기록

설계 원칙 (docs/DB_구축_원칙.md §2, §5.2):
- 원본은 data/raw/<판>/ 에 그대로 보존한다. 받은 뒤 수정하지 않는다.
- 무엇을 언제 어디서 받았는지 sha256과 함께 남긴다 (meta_source).
  원본이 웹에서 사라지거나 조용히 바뀌어도 우리가 쓴 것이 무엇인지 알 수 있어야 한다.
- 이미 있고 크기가 맞으면 다시 받지 않는다.

접근 주의: www.oecd.org 는 Cloudflare 가 막는다(403). 파일은 webfs-sti.oecd.org 에 있고
여기는 막히지 않으나 브라우저 User-Agent 는 필요하다.

입력: 없음 (URL 은 아래 상수)
출력: data/raw/icio2025/*.zip, *.xlsx, *.pdf
      data/raw/icio2025/_manifest.json  (meta_source 의 원천)

실행:
  python scripts\\01_fetch_icio.py                 # SML(표준판) 받기
  python scripts\\01_fetch_icio.py --variant EXT   # 확장판(CN1/CN2·MX1/MX2)
  python scripts\\01_fetch_icio.py --check         # 받지 않고 원격 크기만 대조
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import RAW_DIR, USER_AGENT, setup_logging, sha256_of  # noqa: E402

BASE = "https://webfs-sti.oecd.org/files/STI-PIE/ICIO/2025"
YEAR_GROUPS = ("1995-2000", "2001-2005", "2006-2010", "2011-2015", "2016-2022")

# 판별로 공통으로 받는 문서
DOCS = {
    "SML": ["ICIO2025annex.pdf", "ReadMe_ICIO_small.xlsx"],
    "EXT": ["ICIO2025annex.pdf", "ReadMe_ICIO_extended.xlsx"],
}

log = setup_logging("fetch_icio")


def file_list(variant: str) -> list[str]:
    return DOCS[variant] + [f"{g}_{variant}.zip" for g in YEAR_GROUPS]


def _open(url: str, headers: dict[str, str] | None = None, method: str = "GET", timeout: int = 3600):
    """403 이 간헐적으로 난다(빠른 연속 요청). 지수 백오프로 재시도한다."""
    # UA 만으로는 간헐적으로 403 이 난다. 브라우저가 보내는 헤더를 갖춰 준다.
    h = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
        "Referer": "https://www.oecd.org/",
        "Connection": "keep-alive",
    }
    if headers:
        h.update(headers)
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers=h, method=method)
            return urllib.request.urlopen(req, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            last = e
            wait = 2 ** attempt
            log.warning(f"    재시도 {attempt + 1}/5 ({type(e).__name__}: {e}) — {wait}s 대기")
            time.sleep(wait)
    raise RuntimeError(f"{url} 접근 실패: {last}")


def remote_size(url: str) -> int | None:
    try:
        with _open(url, method="HEAD", timeout=60) as r:
            v = r.headers.get("Content-Length")
            return int(v) if v else None
    except Exception as e:  # noqa: BLE001
        log.warning(f"  크기 확인 실패 {url}: {e}")
        return None


def download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with _open(url) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    tmp.replace(dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["SML", "EXT"], default="SML")
    ap.add_argument("--check", action="store_true", help="받지 않고 크기만 대조")
    args = ap.parse_args()

    out_dir = RAW_DIR / "icio2025"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    log.info(f"판={args.variant}  대상 {len(file_list(args.variant))}개  → {out_dir}")
    stale = 0
    failed: list[str] = []
    for name in file_list(args.variant):
        url = f"{BASE}/{name}"
        dest = out_dir / name
        rsize = remote_size(url)

        if dest.exists() and rsize is not None and dest.stat().st_size == rsize:
            if name not in manifest:  # 손으로 받아 둔 파일도 기록에 넣는다
                manifest[name] = {
                    "url": url,
                    "bytes": dest.stat().st_size,
                    "sha256": sha256_of(dest),
                    "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "note": "기존 파일 확인 후 기록",
                }
                log.info(f"  = {name}  이미 있음 ({dest.stat().st_size:,}) — 기록만 추가")
            else:
                log.info(f"  = {name}  이미 있음 ({dest.stat().st_size:,})")
            continue

        if dest.exists() and rsize is not None:
            log.warning(f"  ! {name} 크기 불일치 로컬 {dest.stat().st_size:,} ≠ 원격 {rsize:,}")
            stale += 1

        if args.check:
            log.info(f"  ? {name}  원격 {rsize:,} bytes (받지 않음)")
            continue

        log.info(f"  ↓ {name}  ({rsize:,} bytes)" if rsize else f"  ↓ {name}")
        try:
            download(url, dest)
        except Exception as e:  # noqa: BLE001
            # 원격이 막혀도 이미 받아 둔 파일이 있으면 기록만 남기고 계속한다.
            # 매니페스트를 못 쓰고 죽는 것이 가장 나쁘다.
            if dest.exists():
                log.warning(f"  ! {name} 내려받기 실패({e}) — 기존 파일을 그대로 쓴다")
                failed.append(name)
            else:
                log.error(f"  ✗ {name} 내려받기 실패, 로컬 파일도 없음: {e}")
                failed.append(name)
                continue
        manifest[name] = {
            "url": url,
            "bytes": dest.stat().st_size,
            "sha256": sha256_of(dest),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        log.info(f"    완료 {dest.stat().st_size:,} bytes  sha256={manifest[name]['sha256'][:16]}…")

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"매니페스트 {len(manifest)}건 → {manifest_path}")
    if stale:
        log.warning(f"크기 불일치 {stale}건. 원본이 갱신됐을 수 있다 — 판 갱신 여부를 확인할 것.")
    if failed:
        log.warning(f"내려받기 실패 {len(failed)}건: {failed}")
        missing = [n for n in failed if not (out_dir / n).exists()]
        if missing:
            log.error(f"로컬에도 없는 파일 {len(missing)}건: {missing}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

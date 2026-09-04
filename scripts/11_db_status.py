"""
11_db_status.py — 대시보드(docs/index.html)의 자동 구간을 DB 실물로 채운다

왜 (KCSDB2 의 06_db_status.py 와 같은 역할):
- 대시보드가 "우리가 가진 것"이 아니라 **"실제 DB 에 있는 것"** 을 말해야 한다.
  손으로 적은 행수는 표를 하나 더 만드는 순간 낡는다.
- 채우는 구간은 셋이다.
    <!-- DB_STATUS:START -->     … 개요 탭의 숫자 카드
    <!-- DB_INVENTORY:START -->  … 받기·사용 탭의 표 전체 목록
    <!-- DB_VERSION:START -->    … 푸터의 배포본 배지
- **표를 새로 만들면 아래 INVENTORY 사전에 한 줄을 더해야 한다.** 없으면 "(설명 미등록)"으로 찍히고,
  목록 순서도 이 사전의 순서를 따른다(알파벳순이면 본체인 fact_icio_z 가 가운데 묻힌다).

배포본 배지: GitHub Releases API 로 실제 게시본을 읽는다. 못 읽으면 "로컬 DB 기준"으로 낮춘다.
게시본이 로컬보다 낡았으면 경고한다 — 배지는 "받을 수 있는 것"을 말해야 하기 때문이다.

입력: data/processed/gvc.duckdb, docs/index.html
출력: docs/index.html (제자리 갱신)

실행:
  python scripts\\11_db_status.py
  python scripts\\11_db_status.py --no-release   # Releases 조회 건너뛰기
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.common import DB_PATH, PROCESSED_DIR, PROJECT_ROOT, USER_AGENT, setup_logging  # noqa: E402

log = setup_logging("db_status")

INDEX_HTML = PROJECT_ROOT / "docs" / "index.html"
REPO = "pilsunchoi/GVC"

# 표 목록. (계층 제목, [(표 이름, 설명, 단위·주의)]) — **순서가 곧 화면 순서다.**
INVENTORY: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("원표 (fact) — OECD 값 그대로", [
        ("fact_icio_z", "중간재 거래. 국가·산업 → 국가·산업", "백만 USD 경상. 정확한 0은 담지 않음"),
        ("fact_icio_fd", "최종수요. 국가·산업 → 국가·6유형", "백만 USD 경상. INVNT는 음수 가능"),
        ("fact_icio_va", "생산물세－보조금(TLS), 부가가치(VA), 총산출(OUT)", "백만 USD 경상"),
    ]),
    ("참조 (dim)", [
        ("dim_icio_entity", "ICIO 개체와 그 경제권(economy)", "확장판에서 CN1·CN2 → CHN"),
        ("dim_icio_ind", "ICIO 50산업과 대응 ISIC Rev.4", "판별로 따로 있다"),
        ("dim_icio_fd", "최종수요 6유형", "HFCE·NPISH·GGFC·GFCF·INVNT·DPABR"),
        ("dim_isic4", "ISIC Rev.4 전 계층", "UNSD 공식 구조 파일"),
        ("dim_ksic", "KSIC 11차·10차 세세분류", "11차 1,205개 · 10차 1,196개"),
    ]),
    ("연계 (map)", [
        ("map_isic_icio", "ISIC Rev.4 → ICIO 50산업", "OECD ReadMe 에서 기계적으로 폄"),
        ("map_ksic_isic", "KSIC 세세분류 → ISIC Rev.4", "**우리가 만든 대응**. 공표표 아님"),
        ("map_ksic_icio", "KSIC → ICIO. **핵심 산출물**", "세분류 아래 1:n 0개 → 가중치 불필요"),
        ("map_ksic_vintage", "KSIC 11차 ↔ 10차", "통계청 공식 연계표. 분할은 가중치 없음"),
    ]),
    ("지표 (mart) — 계산된 값", [
        ("mart_gvc_core", "DVA/FVA 비중, 후방·전방참여도, 상류도·하류도", "국가×산업×연도. edition+method_version 키"),
        ("mart_gvc_bilateral", "상대국별 수출과 상대국 부가가치 함량", "상대는 **경제권** 단위"),
        ("mart_gvc_loo", "같은 산업·연도의 자국 제외 타국 평균", "도구변수용. 빼는 단위는 경제권"),
        ("mart_tiva_check", "자체 산출치 ↔ OECD TiVA 공표치 대조", "받은 사람이 직접 다시 볼 수 있다"),
    ]),
    ("이력 (meta)", [
        ("meta_edition", "판 메타데이터", "개체·산업 수, 커버리지, 단위"),
        ("meta_source", "내려받은 원본의 URL·바이트·sha256", "무엇을 써서 만들었는지의 근거"),
    ]),
]


def fmt(n: int) -> str:
    return f"{n:,}"


def gather(con) -> dict:
    """카드에 쓸 수치를 DB 에서 뽑는다."""
    ed = "ICIO2025"
    y0, y1, ny = con.execute(
        "SELECT min(year), max(year), count(DISTINCT year) FROM fact_icio_va WHERE edition=?", [ed]
    ).fetchone()
    n_ent = con.execute("SELECT count(*) FROM dim_icio_entity WHERE edition=?", [ed]).fetchone()[0]
    n_ind = con.execute("SELECT count(*) FROM dim_icio_ind WHERE edition=?", [ed]).fetchone()[0]
    n_z = con.execute("SELECT count(*) FROM fact_icio_z").fetchone()[0]
    n_core = con.execute("SELECT count(*) FROM mart_gvc_core").fetchone()[0]
    n_ksic = con.execute("SELECT count(*) FROM dim_ksic WHERE ksic_rev='11'").fetchone()[0]
    n_ed = con.execute("SELECT count(*) FROM meta_edition").fetchone()[0]
    return dict(y0=y0, y1=y1, ny=ny, n_ent=n_ent, n_ind=n_ind,
                n_z=n_z, n_core=n_core, n_ksic=n_ksic, n_ed=n_ed)


def status_html(s: dict) -> str:
    cards = [
        (fmt(s["n_z"]), "원표 중간재 거래 행 (두 판 합)"),
        (f"{s['y0']}–{s['y1']}", f"연도 ({s['ny']}개, 빠짐 없음)"),
        (f"{s['n_ent']}", "개체 (80개국 + ROW)"),
        (f"{s['n_ind']}", "ICIO 산업 (ISIC Rev.4)"),
        (fmt(s["n_core"]), "GVC 지표 칸 (국가×산업×연도)"),
        (fmt(s["n_ksic"]), "KSIC 11차 세세분류 (전부 매핑됨)"),
    ]
    out = ['    <div class="stats">']
    for n, k in cards:
        out.append(f'      <div class="stat"><div class="n">{n}</div><div class="k">{k}</div></div>')
    out.append("    </div>")
    return "\n".join(out)


def inventory_html(con) -> str:
    counts, periods = {}, {}
    for (t,) in con.execute("SELECT table_name FROM duckdb_tables()").fetchall():
        counts[t] = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        cols = {c[0] for c in con.execute(f'DESCRIBE "{t}"').fetchall()}
        if "year" in cols:
            a, b = con.execute(f'SELECT min(year), max(year) FROM "{t}"').fetchone()
            periods[t] = f"{a}–{b}" if a is not None else ""

    listed = {t for _, rows in INVENTORY for t, _, _ in rows}
    missing = sorted(set(counts) - listed)
    if missing:
        log.warning(f"  ! INVENTORY 사전에 없는 표 {len(missing)}개: {missing} — 사전에 줄을 더할 것")

    out = ['    <div class="tblwrap">', '      <table class="inv">',
           '        <thead><tr><th>표</th><th>내용</th><th class="r">행수</th>'
           '<th class="r">기간</th><th>단위·주의</th></tr></thead>', "        <tbody>"]
    for group, rows in INVENTORY:
        out.append(f'          <tr class="grp"><td colspan="5">{group}</td></tr>')
        for t, desc, note in rows:
            if t not in counts:
                log.warning(f"  ! {t}: DB 에 없다 — 목록에서 뺀다")
                continue
            desc_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", desc)
            note_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", note)
            out.append(
                f'          <tr><td><code>{t}</code></td><td>{desc_html}</td>'
                f'<td class="r">{fmt(counts[t])}</td><td class="r s">{periods.get(t, "")}</td>'
                f'<td class="s">{note_html}</td></tr>'
            )
    for t in missing:
        out.append(
            f'          <tr><td><code>{t}</code></td><td>(설명 미등록)</td>'
            f'<td class="r">{fmt(counts[t])}</td><td class="r s">{periods.get(t, "")}</td><td class="s"></td></tr>'
        )
    out += ["        </tbody>", "      </table>", "    </div>"]
    return "\n".join(out)


def release_badge(no_release: bool) -> tuple[str, list[str]]:
    """게시본을 읽어 배지 문구를 만든다. 로컬이 더 새것이면 경고를 함께 낸다."""
    warns: list[str] = []
    local_mtime = datetime.fromtimestamp(DB_PATH.stat().st_mtime, timezone.utc)
    local_gb = DB_PATH.stat().st_size / 1e9
    if no_release:
        return f"로컬 DB 기준 {local_gb:.1f}GB (게시본 확인 안 함)", warns

    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases",
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            rel = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        log.warning(f"  Releases 조회 실패({e}) — 배지를 로컬 기준으로 낮춘다")
        return f"로컬 DB 기준 {local_gb:.1f}GB (게시본 확인 못 함)", warns

    if not rel:
        warns.append("게시된 릴리스가 없다. DB 를 아직 배포하지 않았다면 정상이다.")
        return "내려받기 준비 중", warns

    latest = rel[0]
    tag = latest.get("tag_name", "?")
    published = (latest.get("published_at") or "")[:10]
    assets = latest.get("assets", [])
    asset_time = max(
        (datetime.fromisoformat(a["updated_at"].replace("Z", "+00:00")) for a in assets),
        default=None,
    )
    dl = sum(a.get("download_count", 0) for a in assets)

    # 태그만 견주면 같은 달 안에서 표가 늘어난 것을 놓친다. 자산 업로드 시각과 로컬 수정 시각을 함께 본다.
    if asset_time and local_mtime > asset_time:
        warns.append(
            f"게시본({tag}, 자산 {asset_time:%Y-%m-%d})보다 로컬 DB 가 새것이다"
            f"({local_mtime:%Y-%m-%d}). 릴리스를 갱신할 것. 내려받기 {dl}회."
        )
    return f"내려받기 {tag} · {published} 게시", warns


def replace_block(html: str, name: str, body: str) -> str:
    pat = re.compile(f"(<!-- {name}:START -->).*?(<!-- {name}:END -->)", re.S)
    if not pat.search(html):
        raise ValueError(f"{name} 표시가 index.html 에 없다")
    return pat.sub(lambda m: m.group(1) + "\n" + body + "\n" + m.group(2), html)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-release", action="store_true")
    args = ap.parse_args()

    if not INDEX_HTML.exists():
        log.error(f"없음: {INDEX_HTML}")
        return 1

    con = duckdb.connect(str(DB_PATH), read_only=True)
    s = gather(con)
    inv = inventory_html(con)
    con.close()

    badge, warns = release_badge(args.no_release)

    html = INDEX_HTML.read_text(encoding="utf-8")
    html = replace_block(html, "DB_STATUS", status_html(s))
    html = replace_block(html, "DB_INVENTORY", inv)
    html = re.sub(
        r"(<!-- DB_VERSION:START -->).*?(<!-- DB_VERSION:END -->)",
        lambda m: m.group(1) + badge + m.group(2), html, flags=re.S,
    )
    INDEX_HTML.write_text(html, encoding="utf-8")

    log.info(f"  개요 카드 6장, 표 목록 갱신 → {INDEX_HTML}")
    log.info(f"  배지: {badge}")
    lite = PROCESSED_DIR / "gvc_lite.duckdb"
    log.info(f"  본 DB {DB_PATH.stat().st_size / 1e9:.2f} GB"
             + (f", 축소판 {lite.stat().st_size / 1e9:.2f} GB" if lite.exists() else ""))
    for w in warns:
        log.warning(f"  ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# GVC DB — OECD ICIO 기반 글로벌 가치사슬 지표 데이터베이스

OECD 국가간 산업연관표(ICIO) 2025판을 원표 그대로 담고, 표준 GVC 지표를 산업×국가×연도 수준에서
재현 가능하게 산출하며, 한국표준산업분류(KSIC)와 ICIO 산업분류를 잇는 연계표를 제공한다.
연구와 교육에 쓰기 위한 인프라이며 특정 연구주제를 위한 것이 아니다.

**소개 대시보드: [docs/index.html](docs/index.html)** — 무엇이 들어 있고, 어떻게 만들었고,
무엇을 조심해야 하는지를 다섯 탭으로 정리했다(개요 · DB 구축 · 분류 연계 · 데이터 함정 · 받기·사용).

설계 헌법은 [`docs/DB_구축_원칙.md`](docs/DB_구축_원칙.md), 지표 산식은 [`docs/method.md`](docs/method.md)다.

---

## 무엇이 들어 있나

| 계층 | 테이블 | 행수 | 내용 |
|---|---|---:|---|
| **fact** | `fact_icio_z` | 255,015,034 | 중간재 거래. 국가·산업 → 국가·산업, 1995–2022 |
| | `fact_icio_fd` | 31,217,889 | 최종수요. 6유형(HFCE·NPISH·GGFC·GFCF·INVNT·DPABR) |
| | `fact_icio_va` | 113,400 | 생산물세－보조금(TLS), 부가가치(VA), 총산출(OUT) |
| **dim** | `dim_icio_entity` | 81 | 80개국 + ROW |
| | `dim_icio_ind` | 50 | ICIO 산업과 대응 ISIC Rev.4 |
| | `dim_icio_fd` | 6 | 최종수요 유형 |
| | `dim_isic4` | 766 | ISIC Rev.4 전 계층 (UNSD 공식) |
| | `dim_ksic` | 2,401 | KSIC 11차·10차 세세분류 |
| **map** | `map_isic_icio` | 98 | ISIC Rev.4 → ICIO 50산업 |
| | `map_ksic_isic` | 2,401 | KSIC 세세분류 → ISIC Rev.4 |
| | **`map_ksic_icio`** | 4,031 | **KSIC → ICIO. 핵심 산출물** |
| | `map_ksic_vintage` | 2,462 | KSIC 11차 ↔ 10차 (통계청 공식) |
| **mart** | `mart_gvc_core` | 113,400 | DVA/FVA 비중, 후방·전방참여도, 상류도·하류도 |
| | `mart_gvc_bilateral` | 8,797,680 | 상대국별 수출과 상대국 부가가치 함량 |
| | `mart_gvc_loo` | 108,448 | 자국 제외 타국 평균 (도구변수용) |
| | `mart_tiva_check` | 553,280 | 자체 산출치 ↔ OECD TiVA 공표치 대조 |
| **meta** | `meta_edition`, `meta_source` | — | 판 정보, 내려받은 원본의 URL·크기·sha256 |

- 단위: **경상 백만 미국달러**. 실질화되어 있지 않다.
- 범위: **1995–2022**, 50산업(ISIC Rev.4).
- 값이 **정확히 0**인 칸은 담지 않는다. 격자가 dim 으로 완전히 복원되므로 무손실이다.
  없는 조합은 0으로 읽으면 된다.
- 위 행수는 표준판(`ICIO2025`) 기준이다. 확장판(`ICIO2025_EXT`)이 같은 표에 함께 들어 있다.

### 판이 둘이다 — `edition` 을 반드시 걸 것

| | `ICIO2025` (표준판) | `ICIO2025_EXT` (확장판) |
|---|---|---|
| 개체 | 81 (80개국 + ROW) | 85. **생산 쪽만 쪼갠다** — `CHN`·`MEX` 행은 0이지만 최종수요 열은 그대로 |
| 중국·멕시코 | 하나씩 | 가공무역(`CN2`·`MX2`)과 그 밖(`CN1`·`MX1`)으로 갈림 |
| OECD TiVA 재현 | 78개국+ROW 재현. **중국·멕시코는 +3% 어긋남** | **중국·멕시코까지 재현** |

**판 간 결합은 금지다.** 논문에는 쓴 판(`edition`)과 산식 판(`method_version`)을 함께 적는다.

확장판에서 「자국」의 경계는 개체가 아니라 **경제권**(`dim_icio_entity.economy`)이다.
`CN1`·`CN2` → `CHN`. 그래야 CN2 부가가치가 CN1 수출에서 외국분으로 잡히지 않는다.
나라 단위 수치가 필요하면 **비중이 아니라 금액을 합치고 다시 나눈다**(`docs/method.md` §8.2).

---

## 처음 쓰는 사람

```python
import duckdb
con = duckdb.connect("data/processed/gvc.duckdb", read_only=True)

# 한국 반도체·전자(C26)의 후방참여도 추이
con.execute("""
    SELECT year, round(exgr,0) AS 수출_백만달러,
           round(dva_share,4) AS DVA비중, round(backward,4) AS 후방참여도
    FROM mart_gvc_core
    WHERE cty='KOR' AND ind='C26' AND edition='ICIO2025' AND method_version='v1'
    ORDER BY year
""").df()
```

기업 미시자료(기업활동조사·TEC)의 KSIC 코드에 산업 지표를 붙이려면:

```sql
SELECT f.*, c.backward, c.forward, c.upstreamness
FROM firm_panel f
JOIN map_ksic_icio m ON m.ksic = f.ksic5 AND m.ksic_level='세세분류' AND m.ksic_rev='11'
JOIN mart_gvc_core c ON c.cty='KOR' AND c.ind = m.icio_ind AND c.year = f.year
```

**KSIC 세세분류에서 ICIO 산업은 정확히 하나로 정해진다**(1,205개 중 1:n 은 0개).
가중치를 곱할 필요가 없다. 자세한 결합 절차는 [`rdc/join_spec.sql`](rdc/join_spec.sql).

---

## 다시 만들려면

```bash
python scripts/01_fetch_icio.py        # OECD 원본 내려받기 (약 590MB) + sha256 기록
python scripts/02_icio_to_parquet.py   # zip CSV → 연도별 parquet (약 550MB, 약 6분)
python scripts/03_parquet_to_duckdb.py # fact_icio_* 적재 (약 1분, DB 1.9GB)
python scripts/04_build_dims.py        # dim_* + meta_edition
python scripts/04b_fetch_ksic.py       # KSIC 전 단계 코드·명칭 (통계분류포털)
python scripts/05_build_maps.py        # map_isic_icio, map_ksic_isic, map_ksic_icio
python scripts/06_compute_gvc.py       # mart_gvc_core, mart_gvc_bilateral (약 13분)
python scripts/07_compute_loo.py       # mart_gvc_loo
python scripts/08_validate_tiva.py     # mart_tiva_check (OECD TiVA 대조)
python scripts/09_validate.py          # 통합 검증 (PASS/WARN/FAIL)
python scripts/10_export_lite.py       # 교육·배포용 축소판 (원표 Z·FD 제외)
python scripts/11_db_status.py         # docs/index.html 의 숫자·표 목록·배지 갱신
```

확장판도 같은 파이프라인으로 들어간다. `02`·`04` 는 `--variant EXT`, 나머지는 `--edition ICIO2025_EXT`:

```bash
python scripts/01_fetch_icio.py --variant EXT
python scripts/02_icio_to_parquet.py --variant EXT
python scripts/03_parquet_to_duckdb.py --edition ICIO2025_EXT
python scripts/04_build_dims.py --variant EXT
python scripts/05_build_maps.py --edition ICIO2025_EXT
python scripts/06_compute_gvc.py --edition ICIO2025_EXT
python scripts/07_compute_loo.py --edition ICIO2025_EXT
python scripts/08_validate_tiva.py --edition ICIO2025_EXT
python scripts/09_validate.py --edition ICIO2025_EXT
```

각 스크립트는 **그 판의 행만 갈아 끼운다.** 한쪽을 다시 돌려도 다른 쪽이 지워지지 않는다.

Python 환경은 conda env `kcsdb` 를 함께 쓴다(duckdb·pandas·numpy·pyarrow).
모든 스크립트는 처음부터 다시 돌려도 같은 결과를 낸다.

### 접근이 막히는 곳

- `www.oecd.org` 는 Cloudflare 가 스크립트 접근을 막는다(403). 파일은 `webfs-sti.oecd.org` 에 있고 여기는 열려 있다. 브라우저 User-Agent 는 붙여야 한다.
- TiVA 는 `sdmx.oecd.org/sti-public/rest/...` 로 받는다. `sdmx.oecd.org/public` 은 404 다.
- `https://kssc.kostat.go.kr` 는 인증서 주체가 맞지 않아 TLS 검증이 실패한다. `http://` 로 받는다. 사이트가 `kssc.mods.go.kr` 로 이전 중이므로 URL 은 깨질 수 있다 — 그때는 `04b_fetch_ksic.py` 의 `FILES` 상수를 고친다.

---

## 얼마나 믿을 수 있나

**OECD TiVA 2025판 공표치와 대조했다**(`mart_tiva_check`). 규모 있는 칸(TiVA 값 10억 달러 초과)에서
허용치(상대오차 0.1%)를 넘는 칸의 수다.

| 측정치 | 표준판, 78개국+ROW | 표준판, 중국·멕시코 | **확장판, 전체** |
|---|---:|---:|---:|
| 총산출 `PROD` | 0 / 70,036 | 0 / 2,700 | **0** |
| 총수출 `EXGR` | 0 / 33,163 | 0 / 1,705 | **0** |
| 부가가치 `VALU` | 0 / 56,602 | 0 / 2,617 | **0** |
| 수출 내 자국 부가가치 `EXGR_DVA` | 26 / 29,182 | **1,321 / 1,631** | **0** |
| 수출 내 외국 부가가치 `EXGR_FVA` | 506 / 14,056 | **910 / 911** | **0** |

**확장판은 모든 측정치에서 어긋나는 칸이 0이다** — 최대 상대오차 0.008%.
OECD가 공표 TiVA를 확장판에서 계산하기 때문이며, 중국·멕시코뿐 아니라 다른 나라의 DVA/FVA도
확장판에서라야 정확히 맞는다(다른 나라의 수출에도 중국산 투입이 실려 있기 때문이다).

**TiVA 지표를 그대로 재현해야 하는 연구는 `ICIO2025_EXT`를 쓴다.**
표준판은 다루기 쉬운 대신 부가가치 분해에서 중국 관련 오차를 안는다.

두 판의 세계 총수출은 소수점까지 같다(2022년 27,110,736.5 대 27,110,736.9).
판이 달라도 국경을 넘는 거래는 같고, 달라지는 것은 그 거래의 부가가치 원천 배분뿐이다.

무결성 검증(`09_validate.py`)은 **두 판 모두 PASS 24, WARN 3, FAIL 0**이다.
WARN 셋은 모두 원표의 성질이다 — 균형 항등식이 `L`(부동산)·`T`(가구내 고용)에서 1e-3 안쪽으로만
맞는 것, 그리고 키프로스 항공운송 2칸의 부가가치가 음수인 것.

---

## 쓰기 전에 알아야 할 것

- **ICIO 의 연도별 표는 추정치다.** 기준연도 공급사용표 사이를 보간·균형화한 것이며, 나라·연도별로 원자료가 실제로 있었는지는 `data/raw/icio2025/ICIO2025annex.pdf` Table 3 이 보여 준다. 연도 간 변동을 실제 구조 변화로 해석하는 연구는 이 사실을 밝혀야 한다.
- **판 간 비교·연결을 하지 말 것.** 판이 갱신되면 전 기간이 재추정된다. 논문에는 사용 판(`edition`)과 산식 판(`method_version`)을 함께 적는다.
- **균형 항등식은 정확히 성립하지 않는다.** 소수 4자리로 반올림돼 배포되는 데다, 원표 자체가 1e-3 안쪽에서만 균형한다. 어긋남은 `L`(부동산)·`T`(가구내 고용)처럼 시장가격 없는 산출을 품는 산업에 몰린다.
- **관측단위는 국가×산업이다.** 산업 지표를 기업에 부여하면 shift-share 구조이고, 처리 분산이 산업 수준(50개 이하)에 있다. 표준오차는 산업 수준에서 군집화한다.
- **경상가격이다.** 비율 지표는 그대로 쓸 수 있으나 금액 수준의 연도 간 비교는 디플레이터가 필요하다.
- **HS 품목분류는 이 DB에 없다.** 품목 층위가 필요하면 자매 DB [KCSDB2](https://pilsunchoi.github.io/KCSDB2/)를 쓰고 KSIC 으로 결합한다. 이유는 원칙 문서 §1.1 (라).

---

## 출처

| 자료 | 제공 | 이용조건 |
|---|---|---|
| OECD ICIO 2025판 | OECD Directorate for Science, Technology and Innovation | OECD 이용약관 |
| OECD TiVA 2025판 | 같음 (SDMX) | 같음 |
| ISIC Rev.4 구조 | UN Statistics Division | 공개 |
| KSIC 11차·10차, 차수 연계표 | 국가데이터처(옛 통계청) 통계분류포털 | 공공누리 |

인용할 때는 OECD ICIO 2025판을 함께 밝힌다.
이 저장소는 원표를 재배포하지 않는다 — 스크립트가 OECD 에서 직접 받는다.

---

## 라이선스

코드와 문서는 [MIT 라이선스](LICENSE)를 따른다. 원자료의 조건은 [`NOTICE.md`](NOTICE.md)에 있다.

**원자료는 이 라이선스의 대상이 아니다.** OECD ICIO·TiVA는 OECD 이용약관, ISIC Rev.4는 UN 통계국,
KSIC과 차수 연계표는 공공누리(출처표시)를 각각 따른다. 이 저장소는 OECD 원표를 재배포하지 않으며
스크립트가 제공처에서 직접 받는다.

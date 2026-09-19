# KSIC–ISIC–ICIO 연계 DB 구축 계획서

## 1. 목적과 범위

본 DB는 특정 연구주제를 위한 것이 아니라, GVC 실증연구 전반에 재사용되는 **인프라**다. 목적은 세 가지다.

1. OECD ICIO 원표를 장기 저장·버전 관리하고, 표준 GVC 지표를 산업×국가×연도 수준에서 재현 가능하게 산출한다.
2. 한국 산업분류(KSIC)와 ICIO 산업분류 사이의 연계표를 가중치 포함 형태로 확정하여, 한국 미시자료(기업활동조사, TEC 등)에 ICIO 지표를 결합할 수 있게 한다.
3. 다른 DB(무역분쟁 DB 등)와는 KSIC 코드를 공통 키로 결합한다. HS 품목 연계는 본 DB 범위 밖이다.

범위에서 제외하는 것: 미시자료 원본 저장. 기업활동조사·TEC는 통계청 RDC/원격접근 내에서만 사용 가능하므로, DB에는 **연계 키와 결합 스크립트**만 둔다.

## 2. 자료원

| 층 | 자료 | 제공처 | 형태 | 비고 |
|---|---|---|---|---|
| 국제 | OECD ICIO 2025판 | OECD | CSV (연도별 행렬) | 1995–2022, 80개국+ROW, 50산업, ISIC Rev.4 |
| 국제 | OECD TiVA 지표 2025판 | OECD | CSV | 자체 산출 지표 검증용 |
| 분류 | KSIC 10차 | 통계청 KSSC | 표 | 5자리 |
| 분류 | KSIC10–ISIC Rev.4 연계표 | 통계청 KSSC | 표 | 공식 연계표 |
| 분류 | ISIC Rev.4–ICIO 50산업 집계표 | OECD ICIO 문서 | 표 | OECD 산업정의 부록 |
| 가중치 | 광업제조업조사·경제총조사 산업별 산출액·고용 | 통계청 KOSIS | 표 | 다대일 매핑 비례배분용 |
| 검증 | 한국은행 산업연관표 | 한국은행 ECOS | 표 | ICIO 한국 블록 대조용 |

## 3. DB 구조

저장소는 DuckDB(분석) + Parquet(원본 보존)로 한다. 계층은 다음과 같다.

### L0. 코드·연계표

- `dim_country` — ISO3, ICIO 코드, 지역 집계 소속
- `dim_isic4`, `dim_ksic10`, `dim_icio_ind` — 각 분류 코드·명칭·상위코드
- `map_ksic_isic` — KSIC5 → ISIC4, 1:1 또는 1:n, 가중치
- `map_isic_icio` — ISIC4 → ICIO50, 집계 규칙(항상 n:1)
- `map_ksic_icio` — 위 둘의 합성. **핵심 산출물.** 컬럼: `ksic5, icio_ind, weight, weight_basis(output/employment), year_basis, note`
- `edition` — ICIO 판 메타데이터(공개일, 커버리지, 수정판 여부)

### L1. ICIO 원표 (long format)

- `icio_z` — 중간거래: `edition, year, src_c, src_i, dst_c, dst_i, value`
- `icio_fd` — 최종수요: `edition, year, src_c, src_i, dst_c, fd_type, value`
- `icio_va` — 부가가치·산출: `edition, year, c, i, va, output`
- 판이 바뀌면 `edition` 키로 별도 적재. 판 간 결합 금지.

### L2. 파생 GVC 지표 (산업×국가×연도)

- `gvc_core` — DVA 수출비중, FVA 비중, 후방참여도, 전방참여도, 상류도(upstreamness), 하류도
- `gvc_bilateral` — 양자 의존도: `c, i, partner, year, fva_share_from_partner, dva_absorbed_in_partner`
- `gvc_loo` — 한국 제외(leave-one-out) 재계산 지표. 한국 미시자료 결합 시 도구변수용
- `gvc_decomp` — KWW / WWZ 분해 항목(선택적, 계산 비용 큼)

모든 L2 테이블은 `edition`, `method_version` 키를 가진다.

### L3. 한국 미시자료 결합 인터페이스

- `bridge_ksic_year` — KSIC 개정 시점(9차→10차, 2017) 브릿지
- `join_spec.sql` — 기업활동조사·TEC의 산업코드를 `map_ksic_icio`로 결합하는 표준 쿼리. RDC 내부에서 실행
- 결합 결과는 DB에 저장하지 않는다.

## 4. 연계표 구축 절차

### 4.1 KSIC10 → ISIC4
통계청 공식 연계표를 그대로 적재한다. 1:n 관계(KSIC 한 코드가 ISIC 두 코드에 걸침)는 통계청 표에 표시된 대로 기록하고, 가중치는 4.3에서 부여한다.

### 4.2 ISIC4 → ICIO50
OECD ICIO 산업정의는 ISIC4 2자리·중분류 묶음이다. n:1이므로 가중치 없이 매핑한다. ICIO 판마다 산업 수가 바뀌므로(2023판 45개 → 2025판 50개) `edition`별로 별도 표를 유지한다.

### 4.3 가중치 부여
KSIC5 → ICIO50에서 1:n이 남는 코드에 대해 산출액 기준 비례배분 가중치를 부여한다. 산출액 출처는 경제총조사(5년) 및 광업제조업조사(연간)이며, 기준연도를 `year_basis`에 기록한다. 고용 기준 가중치를 대안으로 병기한다. 두 가중치의 차이가 큰 코드는 `note`에 표시한다.

### 4.4 HS 품목분류를 넣지 않는 이유
HS→KSIC는 제품분류→활동분류 변환이라 오차가 구조적이고, HS 5년 개정마다 브릿지표 유지가 필요하며, 기업 미시자료 결합은 기업통계등록부의 KSIC로 완결되므로 HS 경로가 필요 없다. 특정 품목의 ICIO 산업 소속은 연구별로 개별 조회한다. 관세·무역구제 연구용 HS 인프라는 무역분쟁 DB에 두고 KSIC로 결합한다.

### 4.5 검증
- 연계표 완전성: 모든 KSIC5 코드가 정확히 가중치 합 1로 매핑되는지 확인.
- ICIO 한국 블록 총산출·부가가치를 한국은행 산업연관표(동일 연도)와 산업 집계 수준에서 대조. 괴리율 기록.
- L2 지표를 OECD TiVA 공개 지표(EXGR_DVASH, EXGR_FVASH 등)와 대조. 소수점 오차 이내로 재현되어야 한다.

## 5. 지표 산식 (요약)

Leontief 역행렬 $B=(I-A)^{-1}$, 부가가치 계수 $v$, 수출 벡터 $e$에 대해:

- DVA 수출 = $\hat{v}_s B_{ss} e_s$ 항의 합, FVA = 총수출 − DVA − 순수 이중계산 항
- 후방참여도 = FVA / 총수출, 전방참여도 = 제3국 수출에 체화된 자국 DVA / 총수출
- 상류도(Antràs–Chor–Fally–Hillberry) = $[I-\Delta]^{-1}\mathbf{1}$, $\Delta_{ij}=Z_{ij}/Y_i$
- 양자 의존도 = 국가 s 산업 i 수출에 체화된 국가 r의 부가가치 / 총수출
- Leave-one-out: 한국 행·열을 ROW에 흡수한 축소 ICIO에서 동일 지표 재계산

산식 구현은 OECD TiVA Guide 2025 부록을 기준으로 하고, 코드에 참조 절 번호를 명기한다.

## 6. 구현

```
gvc_db/
  raw/          # ICIO 원본 CSV, 연계표 원본 (수정 금지)
  build/
    00_load_icio.py
    01_load_classifications.py
    02_build_maps.py       # map_ksic_icio 생성
    03_compute_gvc.py      # L2 지표
    04_compute_loo.py
    05_validate.py
  db/gvc.duckdb
  parquet/
  docs/
    method.md              # 산식·판 정보·가중치 결정 기록
    changelog.md
  rdc/join_spec.sql        # RDC 반입용
```

- 언어: Python(pandas/polars, numpy). 역행렬 계산은 연도별 4,000×4,000 규모이므로 메모리 문제 없음.
- 원본은 수정하지 않고 `raw/`에 판별 폴더로 보존.
- 모든 빌드 스크립트는 처음부터 재실행 시 동일 결과를 내야 한다.

## 7. 알려진 한계

- ICIO 연도별 표는 기준연도 SUT 사이를 보간·균형화한 추정치다. 연도 간 변동을 실제 구조 변화로 해석하는 연구에는 이 사실을 명시해야 한다.
- 판 갱신 시 전 기간이 재추정된다. 판 간 비교·연결은 금지하며, 연구 논문은 사용 판을 명기한다.
- 관측단위는 국가×산업이다. 산업 수준 지표를 기업에 부여하는 설계는 shift-share 구조이며, 처리 분산이 산업 수준(50개 이하)에 있다는 점을 추론 단계에서 반영해야 한다.
- 2022년 이후 자료 없음. OECD nowcast(41개국·24부문, 2023–24)는 별도 테이블로 두고 본표와 섞지 않는다.
- 품목 수준 처리변수를 산업 수준 ICIO 지표와 직접 비교하는 설계는 지양한다.
- 미시자료 결합은 통계청 승인 절차에 따라 수개월이 소요된다. DB 구축 일정과 별도로 진행한다.

## 8. 단계별 일정

| 단계 | 내용 | 산출물 |
|---|---|---|
| 1 | ICIO 2025판 적재, TiVA 지표 재현 검증 | L1, `gvc_core`, 검증 보고 |
| 2 | KSIC–ISIC–ICIO 연계표 구축·가중치 부여 | `map_ksic_icio`, `method.md` |
| 3 | 양자 의존도·상류도·LOO 지표 | `gvc_bilateral`, `gvc_loo` |
| 4 | RDC 결합 스펙 작성, 통계청 이용 신청 | `join_spec.sql` |
| 5 | (선택) KWW/WWZ 분해 | `gvc_decomp` |

1–3단계가 완료되면 산업 수준 연구는 즉시 가능하다. 4단계는 미시 연구의 전제조건이다.

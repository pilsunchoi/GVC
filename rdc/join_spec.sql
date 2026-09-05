-- join_spec.sql — 기업 미시자료에 ICIO 산업 지표를 붙이는 표준 결합
--
-- 어디서 도는가: 국가데이터처(옛 통계청) 원격접근서비스(RAS) 안.
--     기업활동조사·TEC 원본은 그 환경 밖으로 나오지 않는다.
--
-- 무엇을 반입하는가: CSV 두 장뿐이다. DB 파일을 통째로 넣지 않는다.
--     rdc_industry_panel.csv   400 행   ICIO 산업 x 연도의 대일 의존도와 leave-one-out
--     rdc_ksic_icio.csv      2,401 행   KSIC 세세분류 -> ICIO 산업 (10차·11차)
--     둘 다 기업 정보가 없고 공개 자료(OECD ICIO, 통계분류포털)에서 파생된 값이다.
--     0 절이 이 두 파일을 만든다. 반입 신청은 MDIS 의
--     「MY서비스 - 데이터이용현황 - 원격접근서비스 - 신청서 상세 - 반입/반출」에서 한다.
--     자료마다 개별 승인을 받아야 하므로 반입 대상은 작을수록 좋다.
--     승인이 나지 않으면 두 표를 논문 부록에 싣고 환경 안에서 손으로 입력한다.
--
-- 무엇을 반출하는가: 결합 결과가 아니라 추정 결과(계수표)뿐이다.
--     결합 결과는 DB 에 저장하지 않는다 (docs/DB_구축_원칙.md §5.4).
--
-- 전제 (05_build_maps.py 실측, 2026-09-04):
--   KSIC 세세분류(5자리)와 세분류(4자리)에서 ICIO 산업은 **정확히 하나**로 정해진다.
--   11차 1,205개·10차 1,196개 중 1:n 은 0개다. 따라서 가중치 없이 그냥 조인하면 된다.
--   예외는 99001·99009(국제 및 외국기관)뿐이고 ICIO 에 대응 산업이 없다 — 표본에서 빠진다.
--
-- 차수 주의:
--   KSIC 11차는 2024-07-01 시행이다. 조사연도가 그 앞뒤로 걸치면 ksic_rev 를 연도에 맞춰 고르고,
--   시계열을 하나로 이으려면 map_ksic_vintage 로 한쪽 차수에 맞춘다.
--   **분할(1:n)에는 가중치가 없다.** 어떤 규칙으로 이을지는 분석자의 판단이며 논문에 밝힌다.

------------------------------------------------------------------------------
-- 0. 반입 파일 만들기 (RAS 밖, gvc.duckdb 에 붙어 실행)
------------------------------------------------------------------------------
-- 아래 두 COPY 는 반입 전에 로컬에서 한 번 돌린다. 산출물만 반입한다.
-- 판과 지표 판본을 여기서 고정하고 논문에 밝힌다.

-- 0-1. 산업 x 연도 패널 (400 행 = 50 산업 x 8 연도)
--      dep_jpn : 한국 수출에 실린 일본 부가가치 몫  (이질성 상호작용용)
--      loo_*   : 한국을 뺀 동일 산업 타국 가중평균  (대안 명세의 통제항)
COPY (
    SELECT c.ind                       AS icio_ind,
           c.year,
           b.fva_from_partner_share    AS dep_jpn,
           l.loo_w_backward,
           l.loo_w_forward,
           l.loo_w_dva_share,
           l.n_others
    FROM mart_gvc_core c
    LEFT JOIN mart_gvc_bilateral b
           ON b.edition = c.edition AND b.method_version = c.method_version
          AND b.year = c.year AND b.cty = c.cty AND b.ind = c.ind
          AND b.partner = 'JPN'
    LEFT JOIN mart_gvc_loo l
           ON l.edition = c.edition AND l.method_version = c.method_version
          AND l.year = c.year AND l.cty = c.cty AND l.ind = c.ind
    WHERE c.edition = 'ICIO2025_EXT' AND c.method_version = 'v1'
      AND c.cty = 'KOR' AND c.year BETWEEN 2015 AND 2022
    ORDER BY c.ind, c.year
) TO 'rdc_industry_panel.csv' (HEADER, DELIMITER ',');

-- 0-2. KSIC 세세분류 → ICIO 산업 (2,401 행 = 10차 1,196 + 11차 1,205)
--      세세분류에서 1:n 은 0 개라 가중치가 필요 없다 (봉인1).
COPY (
    SELECT ksic_rev, ksic, icio_ind
    FROM map_ksic_icio
    WHERE ksic_level = '세세분류'
    ORDER BY ksic_rev, ksic
) TO 'rdc_ksic_icio.csv' (HEADER, DELIMITER ',');

-- 반입 전 점검: 두 파일에 기업을 가리키는 열이 하나도 없어야 한다.
--   rdc_industry_panel.csv : icio_ind, year, dep_jpn, loo_*, n_others
--   rdc_ksic_icio.csv      : ksic_rev, ksic, icio_ind

------------------------------------------------------------------------------
-- 1. 기업 → ICIO 산업
------------------------------------------------------------------------------
-- firm_panel(firm_id, year, ksic5, ...) 가 RAS 환경 안에 있다고 본다.

CREATE OR REPLACE VIEW v_firm_icio AS
SELECT
    f.*,
    m.icio_ind
FROM firm_panel f
LEFT JOIN rdc_ksic_icio m          -- 반입한 CSV (0-2)
       ON m.ksic     = f.ksic5
      AND m.ksic_rev = CASE WHEN f.year >= 2024 THEN '11' ELSE '10' END;

-- 붙지 않은 기업을 반드시 센다. 개수와 함께 **매출·수출 기준 비중**도 본다.
-- 개수만 보면 착시가 생긴다(KCSDB2 봉인2 교훈).
SELECT
    year,
    count(*)                                          AS n_firm,
    count(*) FILTER (WHERE icio_ind IS NULL)          AS n_unmatched,
    sum(sales)                                        AS sales_total,
    sum(sales) FILTER (WHERE icio_ind IS NULL)        AS sales_unmatched
FROM v_firm_icio
GROUP BY year ORDER BY year;

------------------------------------------------------------------------------
-- 2. 산업 수준 GVC 지표 붙이기 (한국 = KOR)
------------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_firm_gvc AS
SELECT
    v.*,
    p.dep_jpn,                      -- 산업별 사전 대일 의존도
    p.loo_w_backward, p.loo_w_forward, p.loo_w_dva_share, p.n_others
FROM v_firm_icio v
LEFT JOIN rdc_industry_panel p     -- 반입한 CSV (0-1)
       ON p.icio_ind = v.icio_ind AND p.year = v.year;

------------------------------------------------------------------------------
-- 3. 상대국별 사전 의존도 (예: 2019년 일본 수출규제 연구)
------------------------------------------------------------------------------
-- 규제 이전 기준연도의 산업별 대일 후방 의존도.
-- **이 값은 "일본에서 오는 투입"의 성질이지 "일본으로 가는 수출"이 아니다** (method.md §6).
CREATE OR REPLACE VIEW v_dep_jpn_pre AS
SELECT icio_ind, avg(dep_jpn) AS dep_jpn_pre
FROM rdc_industry_panel
WHERE year BETWEEN 2017 AND 2018
GROUP BY icio_ind;

------------------------------------------------------------------------------
-- 4. 도구변수용 leave-one-out
------------------------------------------------------------------------------
-- 같은 산업·연도의 한국 제외 타국 지표. 한국 기업의 내생적 반응과 무관하다.
-- 0-1 에 이미 들어 있으므로 v_firm_gvc 가 이 역할을 겸한다.
-- 별도 뷰가 필요하면 rdc_industry_panel 의 loo_* 열을 쓴다.

------------------------------------------------------------------------------
-- 5. 반출 전 점검
------------------------------------------------------------------------------
-- 산업×연도 셀의 기업 수. 처리 분산이 산업 수준(50개 이하)에 있으므로
-- 표준오차는 산업 수준에서 군집화해야 한다 (docs/DB_구축_원칙.md §5.4, §9).
SELECT icio_ind, year, count(*) AS n_firm
FROM v_firm_gvc
GROUP BY icio_ind, year
HAVING count(*) < 10          -- 셀이 너무 작으면 비밀보호 규정에 걸린다
ORDER BY n_firm;

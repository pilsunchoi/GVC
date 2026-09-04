-- join_spec.sql — 기업 미시자료에 ICIO 산업 지표를 붙이는 표준 결합
--
-- 어디서 도는가: 통계청 RDC(원격접근) 안. 기업활동조사·TEC 원본은 밖으로 나오지 않는다.
-- 무엇을 반입하는가: 아래 세 표만. 전부 산업×국가×연도 수준이라 기업 정보가 없다.
--     dim_ksic, map_ksic_icio, mart_gvc_core, mart_gvc_bilateral, mart_gvc_loo
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
-- 1. 기업 → ICIO 산업
------------------------------------------------------------------------------
-- firm_panel(firm_id, year, ksic5, ...) 가 RDC 안에 있다고 본다.

CREATE OR REPLACE VIEW v_firm_icio AS
SELECT
    f.*,
    m.icio_ind
FROM firm_panel f
LEFT JOIN map_ksic_icio m
       ON m.ksic       = f.ksic5
      AND m.ksic_level = '세세분류'
      AND m.ksic_rev   = CASE WHEN f.year >= 2024 THEN '11' ELSE '10' END;

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
    c.exgr, c.dva_share, c.fva_share, c.backward, c.forward,
    c.upstreamness, c.downstreamness
FROM v_firm_icio v
LEFT JOIN mart_gvc_core c
       ON c.cty = 'KOR' AND c.ind = v.icio_ind AND c.year = v.year
      AND c.edition = 'ICIO2025' AND c.method_version = 'v1';

------------------------------------------------------------------------------
-- 3. 상대국별 사전 의존도 (예: 2019년 일본 수출규제 연구)
------------------------------------------------------------------------------
-- 규제 이전 기준연도의 산업별 대일 후방 의존도.
-- **이 값은 "일본에서 오는 투입"의 성질이지 "일본으로 가는 수출"이 아니다** (method.md §6).
CREATE OR REPLACE VIEW v_dep_jpn_pre AS
SELECT ind AS icio_ind,
       avg(fva_from_partner_share) AS dep_jpn_pre
FROM mart_gvc_bilateral
WHERE cty = 'KOR' AND partner = 'JPN'
  AND year BETWEEN 2017 AND 2018
  AND edition = 'ICIO2025' AND method_version = 'v1'
GROUP BY ind;

------------------------------------------------------------------------------
-- 4. 도구변수용 leave-one-out
------------------------------------------------------------------------------
-- 같은 산업·연도의 한국 제외 타국 지표. 한국 기업의 내생적 반응과 무관하다.
CREATE OR REPLACE VIEW v_firm_loo AS
SELECT v.*, l.loo_w_backward, l.loo_w_forward, l.loo_w_upstreamness, l.n_others
FROM v_firm_icio v
LEFT JOIN mart_gvc_loo l
       ON l.cty = 'KOR' AND l.ind = v.icio_ind AND l.year = v.year;

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

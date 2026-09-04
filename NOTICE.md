# NOTICE — 자료에 관한 고지

[`LICENSE`](LICENSE)(MIT)는 **이 저장소의 코드와 문서에만** 적용된다.
아래 원자료는 그 대상이 아니며 각 제공기관의 조건을 따른다.

| 자료 | 제공 | 조건 |
|---|---|---|
| OECD ICIO 2025판, TiVA 2025판 | OECD (STI) | OECD 이용약관 |
| ISIC Rev.4 분류 구조 | UN 통계국 | 공개 |
| KSIC 11차·10차, 차수 간 연계표 | 국가데이터처(옛 통계청) | 공공누리 — 출처표시 의무 |

**이 저장소는 OECD 원표를 재배포하지 않는다.** 빌드 스크립트가 제공처에서 직접 받는다.
인용할 때는 OECD ICIO 2025판을 함께 밝힌다.

## 우리가 만든 대응

`map_ksic_isic`와 `map_ksic_icio`는 공표된 연계표가 아니다. 통계청이 KSIC↔ISIC 연계표를
내지 않으므로 이 저장소가 만든 대응이며, 통계청·OECD의 공식 판단이 아니다.
규칙과 근거는 [`scripts/05_build_maps.py`](scripts/05_build_maps.py)의 `RULES`에 있고,
만든 방법은 [`docs/DB_구축_원칙.md`](docs/DB_구축_원칙.md) §4에 있다.
재배포하거나 인용할 때는 추정임을 함께 밝힌다.

---

This NOTICE accompanies the MIT licence in `LICENSE`, which covers the source code and
documentation only. The underlying statistical data remains subject to the terms of its
providers (OECD, UN Statistics Division, Statistics Korea / MODS). The OECD source tables
are not redistributed here; the build scripts download them from the provider.
The KSIC-to-ISIC/ICIO concordances are this repository's own estimate, not an official
determination.

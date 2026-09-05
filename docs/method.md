# GVC 지표 산식

작성일: 2026-09-04
대상: `mart_gvc_core`, `mart_gvc_bilateral`, `mart_gvc_loo`
구현: `scripts/06_compute_gvc.py`, `scripts/07_compute_loo.py`
현재 `method_version`: **v1**

산식을 고치면 `method_version`을 올리고 이 문서에 무엇을 왜 바꿨는지 적는다.
같은 `edition` 안에서도 `method_version`이 다르면 다른 계열이다. 섞어 쓰지 않는다.

---

## 0. 기호

한 해의 ICIO를 다음과 같이 둔다. 국가(개체) $G=81$, 산업 $N=50$, $GN=4{,}050$.

| 기호 | 크기 | 내용 | 출처 |
|---|---|---|---|
| $Z$ | $GN \times GN$ | 중간재 거래 | `fact_icio_z` |
| $F$ | $GN \times G$ | 최종수요(6유형 합계) | `fact_icio_fd` |
| $X$ | $GN$ | 총산출 | `fact_icio_va.out` |
| $VA$ | $GN$ | 부가가치 | `fact_icio_va.va` |
| $TLS$ | $GN$ | 생산물세 − 보조금 | `fact_icio_va.tls` |

단위는 경상 백만 달러다. 없는 조합은 0이다(원칙 §1.2 라).

---

## 1. 기본 행렬

$$
A_{kj} = \frac{Z_{kj}}{X_j}, \qquad B = (I - A)^{-1}
$$

$X_j = 0$인 열은 $A_{\cdot j} = 0$으로 둔다.

$$
v_j = 1 - \sum_k A_{kj} = \frac{VA_j + TLS_j}{X_j}
$$

### TLS를 부가가치에 넣는 이유 — TiVA 대조로 확정 (2026-09-04)

ICIO의 열 균형은 $\sum_k Z_{kj} + TLS_j + VA_j = X_j$다.
따라서 $v_j = VA_j / X_j$로 두면 $v'B \ne \mathbf{1}'$이 되어 **총수출이 남김없이 분해되지 않는다**
(DVA + FVA < 총수출). $v_j = (VA_j + TLS_j)/X_j$로 두어야 $v'B = \mathbf{1}'(I-A)B = \mathbf{1}'$이 성립한다.

이 선택이 옳다는 것을 **OECD TiVA 공표치가 확인해 준다.**
TiVA의 `VALU`(Value added)를 ICIO의 `VA + TLS`와 맞대 보면 규모 있는 칸 59,219개에서
**상대오차 중위 4e-6%, 최대 0.0%**다. 즉 **OECD가 공표하는 "부가가치"는 ICIO의 VA 행에 TLS를 더한 것이다.**
`VA`만으로 비교하면 중위 2.86%가 어긋난다. 열려 있던 문제가 아니라 확정된 사실이다.

`mart_gvc_core`는 `va`, `tls`, `v`를 모두 담으므로 이용자가 차이를 직접 볼 수 있다.

### 부가가치가 음수인 칸

원표에 부가가치가 음수인 국가·산업이 있다(실측: 2017·2018년 키프로스 항공운송 `CYP_H51`, VA = −19.6, −33.6).
그런 칸은 $v_j < 0$이 되어 DVA 비중이 음수, FVA 비중이 1을 넘는다. **원표의 성질이며 고치지 않는다.**
`09_validate.py`가 이 칸을 WARN으로 보고한다.

---

## 2. 총수출

국가 $s$, 산업 $i$의 총수출은 중간재 수출과 최종재 수출의 합이다.

$$
E_{si} = \underbrace{\sum_{r \ne s}\sum_j Z_{si,\,rj}}_{\texttt{exgr\_int}} + \underbrace{\sum_{r \ne s} F_{si,\,r}}_{\texttt{exgr\_fin}}
$$

국내 거래는 빠진다. 재수출·재수입 조정은 하지 않는다(ICIO가 이미 반영한 값이다).

---

## 3. 수출의 부가가치 원천

$v_k B_{k,\,si}$는 $(s,i)$가 총산출 1단위를 낼 때 실리는 $k$의 부가가치다.
$\sum_k v_k B_{k,si} = 1$이므로 다음 둘은 정확히 1로 합쳐진다.

$$
\texttt{dva\_share}_{si} = \sum_{k \in s} v_k B_{k,\,si}, \qquad
\texttt{fva\_share}_{si} = 1 - \texttt{dva\_share}_{si}
$$

- **후방참여도**(`backward`) $= \texttt{fva\_share}$. 수출에 실린 외국 부가가치의 몫이다.
- OECD TiVA의 `EXGR_DVASH` / `EXGR_FVASH`에 대응한다.

`dva_share`는 되돌아온 자국 부가가치(returned domestic value added)를 자국분에 포함한다.
그것을 떼려면 KWW/WWZ 분해가 필요하고, 그것은 `mart_gvc_decomp`(후순위)의 몫이다.

---

## 4. 전방참여도

$(s,i)$의 부가가치가 **다른 나라의 수출**에 실려 나간 몫이다.

$$
DVX_{si} = v_{si}\left[\sum_{r \ne s}\sum_j B_{si,\,rj}\, E_{rj}\right], \qquad
\texttt{forward}_{si} = \frac{DVX_{si}}{E_{si}}
$$

구현은 $B E$를 한 번 구하고 자국 블록 $\sum_{j \in s} B_{si,sj} E_{sj}$를 빼는 방식이다.

---

## 5. 상류도와 하류도

**상류도**(Antràs–Chor–Fally–Hillberry 2012). 최종수요까지의 평균 단계 수.

$$
U = (I - \Delta)^{-1}\mathbf{1}, \qquad \Delta_{ij} = \frac{Z_{ij}}{X_i}
$$

$\Delta$는 $i$의 산출 중 $j$의 중간투입으로 가는 몫이다(행 기준). $U_i \ge 1$이다.

**하류도**(Antràs–Chor 2018). 원시 투입으로부터의 평균 단계 수.

$$
D_j = \sum_i B_{ij}
$$

$U$는 $GN$ 크기 선형계 한 번, $D$는 $B$의 열 합이라 추가 비용이 거의 없다.

---

## 6. 양자 지표 (`mart_gvc_bilateral`)

$$
\texttt{exgr\_to\_partner}_{si,r} = \sum_j Z_{si,\,rj} + F_{si,\,r} \quad (r \ne s)
$$

$$
\texttt{fva\_from\_partner\_share}_{si,r} = \sum_{k \in r} v_k B_{k,\,si} \quad (r \ne s), \qquad
\texttt{fva\_from\_partner} = \text{그 값} \times E_{si}
$$

**주의**: `fva_from_partner_share`는 $(s,i)$ 총수출에 실린 $r$의 부가가치 몫이다.
$r$**로 가는** 수출의 성질이 아니라 $r$**에서 오는** 투입의 성질이다.
2019년 일본 수출규제 예제의 「사전 대일 후방 의존도」가 바로 이 값이다($r=\text{JPN}$).

두 값이 모두 0인 $(si, r)$ 조합은 저장하지 않는다.

---

## 7. leave-one-out (`mart_gvc_loo`)

같은 산업·같은 연도에서 자국을 뺀 나머지 나라들의 지표다.

$$
\texttt{loo\_w}\_x_{s,i,t} = \frac{\sum_r w_{r,i,t}\,x_{r,i,t} - w_{s,i,t}\,x_{s,i,t}}{\sum_r w_{r,i,t} - w_{s,i,t}},
\qquad w = E \ (\text{총수출})
$$

단순평균 `loo_m_x`도 함께 낸다(가중치 1).

- **ROW는 합계에서 뺀다.** 잔차 지역이라 한 나라로 볼 수 없다.
- 수출이 0인 칸은 가중평균에 뜻이 없으므로 뺀다.
- 대상 지표: `dva_share`, `fva_share`, `backward`, `forward`, `upstreamness`, `downstreamness`.

**원 계획서의 정의를 쓰지 않은 이유**는 `docs/DB_구축_원칙.md` §1.2 (다)에 적었다.
한국을 ROW에 흡수한 축소 ICIO에서는 한국 산업의 지표 자체가 나오지 않으므로 도구변수가 될 수 없다.

---

## 7b. 수입에 체화된 상대국 부가가치 (`mart_gvc_import_va`)

§6의 `fva_from_partner`는 **수출**에 실린 상대국 부가가치다. 총액 기준 양자 의존도와 견주려는
경우 이것으로는 모자란다. 두 척도의 모집단이 다르기 때문이다. 총액 기준의 분모는 한 나라의 총수입
전체인데, 수출 쪽 부가가치 척도는 그 수입 가운데 다시 수출에 실려 나가는 부분만 담고 게다가 수출
구성으로 가중된다. 그래서 두 값을 나란히 놓으면 「총액 대 부가가치」 외에 「전체 수입 대 수출에
실린 부분」과 「수출 구성 가중」이라는 차이가 함께 섞인다.

이 표는 분해의 대상을 수출에서 **수입**으로 옮겨 그 두 차이를 없앤다. 남는 차이는 총액 대 부가가치
하나뿐이므로 두 척도를 직접 대조할 수 있다.

$$
M_{si,\,d} = \sum_j Z_{si,\,dj} + F_{si,\,d} \quad (d \ne s)
$$

$$
\texttt{imp\_from\_partner}_{d,\,p} = \sum_{si \,\in\, p} M_{si,\,d}, \qquad
\texttt{va\_from\_partner}_{d,\,p} = \sum_{si} M_{si,\,d} \left( \sum_{k \in p} v_k B_{k,\,si} \right)
$$

$d$는 수입국, $p$는 부가가치가 발생한 경제권이다. 둘 다 경제권 단위로 낸다(§8.2).

**주의**: 안쪽 괄호는 §6의 `fva_from_partner_share`와 같은 식이지만 **자기 경제권을 지우지 않은
값**이다. §6은 자국 부가가치가 FVA가 아니므로 $p = s$를 0으로 두는데, 여기서는 일본에서 직접 들여온
물량에 담긴 일본 부가가치가 바로 그 항이라 지우면 안 된다.

$\sum_p \sum_{k \in p} v_k B_{k,si} = 1$이므로 $\sum_p \texttt{va\_from\_partner}_{d,p}$는 $d$의
총수입과 같다. 적재할 때마다 확인하며 실측 상대오차는 $10^{-15}$ 수준이다. $p = d$인 항은 수입에
실려 돌아온 자국 부가가치이며 지우지 않고 저장한다. 분모를 외국 부가가치로 잡으려면 이 항을 뺀다.

산출은 `06b_compute_import_va.py`다. 06의 `load_year`·`compute`를 그대로 쓰므로 산식이 갈라지지
않는다. 계산 비용은 06과 같고(연도당 약 1분, 병목은 $Z$ 적재) 결과는 경제권 × 경제권이라 작다.

---

## 8. 두 판: 표준판(SML)과 확장판(EXT)

### 8.1 확장판이 무엇인가

확장판은 중국을 `CN1`(가공무역 제외)·`CN2`(가공무역)로, 멕시코를 `MX1`·`MX2`로 쪼갠 표다. 개체는 85개다.

**쪼개는 것은 생산 쪽뿐이다.** 실측(2022년)으로 확인한 구조는 이렇다.

- **행(생산)**: `CHN`·`MEX` 행은 값이 전부 0이다. 총산출 0, 중간재 거래 0건.
  실제 생산은 `CN1`·`CN2`(`MX1`·`MX2`)가 진다. `CN1 + CN2` 총산출이 표준판 `CHN`과 **정확히 일치**한다.
- **열(최종수요)**: 중국의 최종수요 열은 여전히 **`CHN`** 이다(`CN1`·`CN2` 열이 아니다).
  실측: 확장판 `CHN` 최종수요 합 16,650,453.9 = 표준판 `CHN` 합, 소수점까지 같다.

그래서 `CN1 → CHN` 최종수요 흐름은 **국내 거래**이지 수출이 아니다.
개체 이름만 보고 `src_cty <> dst_cty` 로 수출을 세면 2022년 세계 수출이 27.1조에서 45.2조로 부풀어 오른다.

### 8.2 경제권(economy)으로 센다

`dim_icio_entity.economy`가 개체를 경제권으로 묶는다. 표준판에서는 `economy = code`이고,
확장판에서는 `CN1·CN2 → CHN`, `MX1·MX2 → MEX`다.

**지표를 셀 때 "자국"과 "수출"의 경계를 개체가 아니라 이 경제권으로 잡는다.** 그러지 않으면

- `CN2`가 만든 부가가치가 `CN1` 수출에서 **외국 부가가치**로 잡히고,
- `CN1 → CN2` 중간재 거래(2022년 78만 백만 달러)가 **수출**로 잡히며,
- `CN1 → CHN` 최종수요(§8.1)가 통째로 **수출**로 잡힌다.

셋 다 틀렸다. 중국은 한 나라다.
경제권 기준으로 세면 확장판의 세계 총수출이 표준판과 소수점까지 맞는다
(2022년 27,110,736.5 대 27,110,736.9). `09_validate.py`가 이것을 검사한다. `mart_gvc_bilateral`의 `partner`도 경제권 단위로 낸다
(표준판에서는 개체가 곧 경제권이라 달라지는 것이 없다 — 실측으로 확인했다).

`mart_gvc_core`의 행은 개체 단위(`CN1`, `CN2`)로 남는다. 나라 단위 수치가 필요하면 합친다.
비중이 아니라 **금액을 합치고 다시 나눈다**(비중의 평균은 뜻이 없다).

```sql
SELECT c.year, e.economy, c.ind,
       sum(c.exgr) AS exgr,
       sum(c.dva_share * c.exgr) / sum(c.exgr) AS dva_share
FROM mart_gvc_core c JOIN dim_icio_entity e ON e.edition=c.edition AND e.code=c.cty
WHERE c.edition='ICIO2025_EXT' GROUP BY 1,2,3
```

### 8.3 어느 판을 쓸 것인가

| | 표준판 `ICIO2025` | 확장판 `ICIO2025_EXT` |
|---|---|---|
| 개체 | 81 (80개국 + ROW) | 85 (`CHN`·`MEX`는 빈 자리) |
| 중국·멕시코 | 하나씩 | 가공무역/비가공무역으로 갈림 |
| OECD 공표 TiVA 재현 | 78개국 + ROW 는 재현. **중국·멕시코는 안 맞음** | 중국·멕시코까지 재현 |
| 쓰기 편한 정도 | 단순 | 개체를 경제권으로 접어야 함 |

**중국·멕시코가 분석의 중심이면 확장판**, 그 밖에는 표준판을 쓴다.
한국 GVC 연구에서 중국이 차지하는 비중을 생각하면 확장판을 함께 두는 편이 낫다.

**판 간 결합은 금지다.** 두 판을 한 회귀에 섞지 말고, 어느 판을 썼는지 논문에 밝힌다.

## 9. 하지 않은 것

- **실질화**: ICIO는 경상가격이다. 디플레이터를 붙이지 않는다. 비율 지표는 그대로 쓸 수 있으나 금액 수준의 연도 간 비교는 분석 계층에서 처리한다.
- **KWW/WWZ 분해**: `mart_gvc_decomp`로 미룬다. 되돌아온 자국 부가가치와 순수 이중계산 항을 가르려면 필요하다.
- **판 간 연결**: 하지 않는다. 판이 바뀌면 전 기간이 재추정된다.
- **부가가치의 요소별 분해**(노동/자본): ICIO 본표에 없다.

---

## 10. 계산 비용 (실측, 2026-09-04)

연도당 적재 약 20초, 계산 약 2초. 28년 전체 약 10분.
$4{,}050 \times 4{,}050$ 역행렬은 배정도 실수로 131MB이며 메모리 문제가 없다.
병목은 행렬 연산이 아니라 DuckDB에서 $Z$를 읽어 오는 부분이다.

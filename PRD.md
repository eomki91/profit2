# [PRD] AI 기반 철강 손익·스프레드 분석 챗봇 (Steel Margin Copilot)

## 1. 프로젝트 개요 (Overview)
* **목적**: 철강 제조/영업 부문의 복잡한 손익 및 스프레드 실적을 자연어 질의(Text-to-SQL + Few-Shot Vector Retrieval)를 통해 신속하고 정확하게 조회·분석·시각화하는 사내 분석 챗봇 구축.
* **주요 구동 환경**: 사내 폐쇄망 / 온프레미스 / 로컬 PC (외부 인터넷 독립 구동 가능 구조).
* **개발 패러다임**: 바이브코딩(Vibe Coding)을 고려한 고효율 모듈형 아키텍처.

---

## 2. 데이터 스키마 명세 (Data Contract)

시계열 롤링 분석(MoM, QoQ, YoY)의 연산 효율화를 위해 `연도(연)`, `분기` 파생 필드를 정형 테이블에 기본 포함합니다.

### 2.1. 테이블 정의 (`profit_center_master`)
| 컬럼명 | 영문 매핑 (DB) | 데이터 타입 | 설명 및 예시 값 |
| :--- | :--- | :--- | :--- |
| **연도** | `year` | INTEGER | 2026 |
| **분기** | `quarter` | VARCHAR(2) | Q1, Q2, Q3, Q4 |
| **연월** | `year_month` | VARCHAR(7) | 2026-01 ~ 2026-12 |
| **고로구분** | `division` | VARCHAR(10) | 판재, 봉형강 |
| **손익센터** | `profit_center`| VARCHAR(20) | 열연, 냉연, 후판, 철근, H형강, 특수강 |
| **고로구분2**| `sub_division_2`| VARCHAR(20)| 글로벌, HMG, NULL |
| **고로구분3**| `sub_division_3`| VARCHAR(20)| 자동차용, 조선용, 실수요, 유통, NULL |
| **공장구분**| `plant` | VARCHAR(20) | 당진, 순천, 인천, 포항 |
| **매출량** | `sales_vol` | BIGINT | 단위: 톤 (Ton) |
| **매출액** | `sales_amt` | BIGINT | 단위: 원 |
| **판매단가**| `unit_price` | BIGINT | 단위: 원/톤 (매출액 / 매출량) |
| **원부재료**| `raw_material`| BIGINT | 단위: 원/톤 |
| **스프레드**| `spread` | BIGINT | 단위: 원/톤 (판매단가 - 원부재료) |
| **기타비용**| `other_cost` | BIGINT | 단위: 원/톤 |
| **영업이익**| `op_profit` | BIGINT | 단위: 원 |
| **경상이익**| `ord_profit` | BIGINT | 단위: 원 |

### 2.2. 집계 및 계산 절대 룰 (Prompt Injected Rules)
1. **단가/스프레드 단순 산술평균 금지**: 여러 행 집계 시 반드시 **매출량 가중평균** 적용.
   * `가중평균 판매단가` = `SUM(sales_amt) / SUM(sales_vol)`
   * `가중평균 원부재료` = `SUM(raw_material * sales_vol) / SUM(sales_vol)`
   * `가중평균 스프레드` = `가중평균 판매단가 - 가중평균 원부재료`
2. **영업이익률**: `(SUM(op_profit) / SUM(sales_amt)) * 100`

---

## 3. 시스템 아키텍처 & Few-Shot SQL Vector 체인

```
[사용자 자연어 입력]
       │
       ▼
[1. Few-Shot SQL Vector Search] ◄── [Vector DB (FAISS/Chroma)]
- 사용자 질문과 유사한 SQL 템플릿 Top-K 검색  - 사전 등록된 검증된 SQL Few-Shot
       │
       ▼
[2. Text-to-SQL LLM Generator]
- LLM에 [스키마 정보 + Few-Shot 예시 + 사용자 질문] 주입
       │
       ▼
[3. Local In-Memory SQL Execution] ──► [DuckDB / SQLite]
       │
       ▼
[4. 결과 후처리 및 차트/텍스트 렌더링]
- (차후 확장 슬롯) ──► [PPT 시황 장표 RAG 검색기 (Future Plug-in)]
```

---

## 4. Few-Shot SQL Vector DB 사전 구축 데이터셋

Vector DB에 사전 임베딩하여 질의 정확도를 보장할 Few-Shot 데이터셋 명세입니다.

```json
[
  {
    "question": "3월 품목별 스프레드 및 손익 실적 요약해줘.",
    "sql": "SELECT profit_center, SUM(sales_vol) AS total_vol, ROUND(SUM(sales_amt)/SUM(sales_vol)) AS avg_price, ROUND(SUM(sales_amt)/SUM(sales_vol) - SUM(raw_material*sales_vol)/SUM(sales_vol)) AS avg_spread, SUM(op_profit) AS total_op_profit, ROUND((SUM(op_profit)*1.0/SUM(sales_amt))*100, 2) AS op_margin FROM profit_center_master WHERE year_month = '2026-03' GROUP BY profit_center ORDER BY total_op_profit DESC;"
  },
  {
    "question": "5월 전월(4월) 대비 스프레드가 가장 많이 하락한 품목이 뭐야?",
    "sql": "WITH monthly_spread AS (SELECT year_month, profit_center, ROUND(SUM(sales_amt)/SUM(sales_vol) - SUM(raw_material*sales_vol)/SUM(sales_vol)) AS spread FROM profit_center_master WHERE year_month IN ('2026-04', '2026-05') GROUP BY year_month, profit_center) SELECT m5.profit_center, m4.spread AS apr_spread, m5.spread AS may_spread, (m5.spread - m4.spread) AS spread_diff FROM (SELECT * FROM monthly_spread WHERE year_month = '2026-05') m5 JOIN (SELECT * FROM monthly_spread WHERE year_month = '2026-04') m4 ON m5.profit_center = m4.profit_center ORDER BY spread_diff ASC;"
  },
  {
    "question": "8월 자동차용 스프레드는 얼마정도야?",
    "sql": "SELECT sub_division_2 AS client_group, profit_center, SUM(sales_vol) AS total_vol, ROUND(SUM(sales_amt)/SUM(sales_vol)) AS avg_price, ROUND(SUM(sales_amt)/SUM(sales_vol) - SUM(raw_material*sales_vol)/SUM(sales_vol)) AS avg_spread, SUM(op_profit) AS op_profit FROM profit_center_master WHERE year_month = '2026-08' AND sub_division_3 = '자동차용' GROUP BY sub_division_2, profit_center;"
  },
  {
    "question": "상반기 분기별(Q1 vs Q2) 봉형강 공장별 영업이익 비교해줘.",
    "sql": "SELECT quarter, plant, SUM(sales_vol) AS total_vol, SUM(op_profit) AS total_op_profit, ROUND((SUM(op_profit)*1.0/SUM(sales_amt))*100, 2) AS op_margin FROM profit_center_master WHERE division = '봉형강' AND quarter IN ('Q1', 'Q2') GROUP BY quarter, plant ORDER BY plant, quarter;"
  }
]
```

---

## 5. 향후 확장 포인트 (Future Market RAG Plug-in)
* **목적**: 주간 시황 PPT/PDF 파일이 입고될 경우, SQL 수치 결과와 시황 원인 분석 텍스트를 자동 결합.
* **설계 원칙**: 현재 시스템의 `analysis_service` 계층에 `MarketContextRetriever` 인터페이스를 비워두어(Stub/Mock), 추후 문서 파서만 연결하면 즉시 가동되도록 설계.

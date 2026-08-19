# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

철강(현대제철) 손익/스프레드 분석 챗봇. 자연어 질문 → Text-to-SQL → DuckDB 실행 → LLM이 FP&A 브리핑으로
해석하는 Streamlit 앱. 전체 요구사항/스키마/절대 룰의 1차 소스는 `PRD.md`이며, 그 룰은 코드에서
`src/config.py`의 `SCHEMA_PROMPT`로 그대로 구현되어 있다.

## Commands

```bash
pip install -r requirements.txt      # 의존성 설치
cp .env.example .env                 # 필요시 OPENAI_API_KEY 등 채워넣기 (없어도 로컬 Ollama로 동작)
streamlit run app.py                 # 앱 실행 (기본 포트 8501)

pytest                                # 전체 테스트
pytest tests/test_sql_accuracy.py -v -k test_mom_rolling_consistency   # 단일 테스트
```

`pytest.ini`가 `pythonpath = .`를 지정하므로 `from src import ...`가 루트 어디서 실행해도 동작한다.
테스트는 LLM/임베딩을 전혀 호출하지 않고 실제 DuckDB 데이터로 SQL 로직만 검증하므로 API 키 없이도
항상 실행 가능하다.

로컬 LLM을 쓰려면 Ollama가 떠 있어야 한다 (`OLLAMA_MODEL_NAME` 기본값 `qwen2.5-coder:7b`, 임베딩은
`LOCAL_EMBEDDING_MODEL` 기본값 `BAAI/bge-m3`). 둘 다 미리 `ollama pull` 되어 있지 않으면 첫 질문 시
다운로드가 시작되어 매우 느릴 수 있다.

**개발 중 앱을 재시작해야 하는 경우**: Streamlit은 `app.py`는 매 rerun마다 새로 읽지만, `src/` 아래
서브모듈은 프로세스에 이미 로드된 상태로 캐시되어 브라우저 새로고침만으로는 반영되지 않을 때가 있다.
`src/*.py`를 고친 뒤 동작이 이상하면(특히 새 함수를 추가했는데 `AttributeError`가 나면) 서버를
완전히 종료하고 `streamlit run app.py`를 다시 실행한다.

## Architecture

### 데이터 계층
`data/raw_data.xlsx`는 한글 컬럼명 원본 파일이다. `src/db_engine.py`의 `COLUMN_RENAME_MAP`이 이를
영문 스키마(`year, quarter, year_month, division, profit_center, sub_division_2, sub_division_3,
plant, sales_vol, sales_amt, unit_price, raw_material, spread, other_cost, op_profit, ord_profit`)로
바꾸고 `year`/`quarter`를 `year_month`에서 파생시켜 in-memory DuckDB의 단일 테이블
`profit_center_master`(288행, 2026년 1개 연도만 존재)에 적재한다. `get_connection()`은
`@st.cache_resource`로 프로세스당 한 번만 실행된다. `sub_division_2/3`는 서로 배타적인 채널
세그먼트라 필터 없이 GROUP BY로 합산해도 중복 집계가 아니다 — division↔profit_center,
plant↔profit_center 매핑도 고정되어 있다(둘 다 DB 제약이 아니라 `config.py`의 딕셔너리 + LLM
프롬프트 가이드로만 강제됨).

### Text-to-SQL 파이프라인 (`src/sql_agent.py`)
질문 하나가 SQL로 바뀌는 경로는 3단계 우선순위다:
1. **템플릿 매칭** `match_sql_template()` — 추천 질문 5종(월별 요약/MoM/자동차용/상반기 공장비교/적자
   탐색)에 대한 정규식·키워드 매처가 `SQL_TEMPLATES`의 사전 검증된 SQL에 파라미터를 채워 즉시 반환.
   LLM을 전혀 호출하지 않는다.
2. **SQL 캐시** `_SQL_CACHE` (plain dict, `(engine, temperature, question)` 키) — 같은 질문 재입력 시
   LLM 재호출 생략.
3. **LLM Fallback** — `vector_store.get_similar_sql_examples()`가 `data/few_shot_sqls.json`을 FAISS로
   검색해 유사 예시 2개를 뽑고, `config.SCHEMA_PROMPT`(가중평균 절대 룰 포함)와 함께 LLM에 주입 →
   `extract_sql()`로 ```sql 블록만 파싱.

실행은 `db_engine.run_query()`가 맡고, `duckdb.Error`가 나면 `run_with_self_correction()`이 오류
메시지를 프롬프트에 재주입해 최대 `MAX_SELF_CORRECTION_RETRIES`(2)회까지 LLM에게 스스로 SQL을
고치게 한다.

SQL 실행 결과는 그대로 화면에 나가지 않고, `stream_synthesis_tokens()`가 이를 다시 LLM에 넣어
📌 핵심 결론 / 💡 손익 요인 분석 / ⚠️ 주의 필요 세그먼트 3단 브리핑을 생성한다. 이때 `_format_result_table()`
이 표의 숫자를 먼저 "천 톤/천 원/톤/억(조) 원" 문자열로 변환해 LLM에 넘긴다(LLM이 큰 원 단위 숫자를
직접 축약하다 자릿수를 착각하는 사례가 실측되어 파이썬에서 미리 끝낸다). 증감 표현은 개선/상승=
`:red[...]`, 악화/하락=`:blue[...]` Streamlit 컬러 마크다운으로 감싸도록 프롬프트에서 강제한다.

**절대 룰(반드시 지킬 것)**: 단가/원가/스프레드는 `AVG()`가 아니라 매출량 가중평균
(`SUM(sales_amt)/SUM(sales_vol)` 등)이어야 한다. 이 룰은 `config.SCHEMA_PROMPT`에 명시돼 있고,
`tests/test_sql_accuracy.py`가 `few_shot_sqls.json`과 실제 DuckDB 결과를 대상으로 정적/동적으로
검증한다. 새 few-shot이나 SQL 템플릿을 추가할 때 이 룰을 깨면 테스트가 실패한다.

### 한글 라벨/단위 변환 계층
SQL 결과 컬럼은 항상 영문 별칭(`total_op_profit`, `avg_spread` 등)으로 온다. `sql_agent.py`의
`COLUMN_KOREAN_MAP` + `get_display_label()` + `humanize_dataframe()`가 화면 표시용으로 컬럼명을
한글로 바꾸고 값을 단위 규칙대로 축약한 **사본**을 만든다(원본 df는 차트 계산 등에 그대로 재사용).
매핑에 없는(LLM이 새로 지어낸) 별칭은 `get_unit_family()`의 이름 패턴 휴리스틱으로 "{원본}(단위)"
형태로 자동 보완된다. `app.py`의 `build_chart()`는 카테고리/지표 컬럼 판별을 **원본 영문명** 기준
휴리스틱으로 한 뒤, 실제 플로팅만 한글화된 데이터프레임으로 그린다 — 이 순서를 바꾸면(한글화된
컬럼명에 영문 키워드 매칭을 시도하면) 차트 로직이 조용히 깨진다.

### LLM 엔진 / 캐싱
`config.get_llm(engine_type, temperature, api_key)`가 로컬 Ollama와 OpenAI를 전환한다
(`@st.cache_resource`로 동일 인자 조합은 재사용). 기본 엔진은 `.env`의 `USE_OPENAI` + `OPENAI_API_KEY`
존재 여부로 결정되지만, 실제로는 항상 Streamlit UI에서 사용자가 고른 값이 우선한다. `db_engine`의
DuckDB 커넥션, `vector_store`의 FAISS 인덱스, `config`의 LLM 클라이언트는 모두 `st.cache_resource`
기반 싱글턴이고, `sql_agent._SQL_CACHE`는 이와 별개의 가벼운 모듈 dict 캐시다.

### UI (`app.py`)
ChatGPT/Claude 스타일: `st.chat_input()`을 어떤 `st.container(height=...)`에도 속하지 않게 호출해
뷰포트 최하단에 고정시킨다(컨테이너 안에 넣으면 그 컨테이너 바닥에만 고정되니 주의). 채팅 기록은
별도의 고정 높이 스크롤 컨테이너에 쌓이고, 우측 컬럼에는 추천 질문 칩이 있다 — 칩 클릭은
`st.session_state["pending_query"]`를 세팅해 직접 타이핑한 것과 동일하게 처리된다. 관리자 설정
(엔진 선택/API Key/Temperature/DB 상태)은 사이드바가 아니라 헤더 톱니바퀴 → `@st.dialog` 모달에
있다(사이드바는 의도적으로 제거됨). `@st.dialog` 함수는 열려야만 실행되므로, 모달을 한 번도 안 열어도
채팅이 동작하도록 `engine_type`/`temperature`/`openai_api_key`는 위젯 렌더링 전에
`st.session_state.get(key, fallback_default)`로 먼저 읽는다 — fallback 기본값은 모달 위젯의
기본값과 반드시 일치시켜야 한다.

### 확장 포인트
`src/market_rag_stub.py`는 향후 시황 PPT/PDF RAG용 인터페이스(ABC + Mock)로, 아직 `sql_agent`에
연결되어 있지 않다. 새 채널을 붙일 때는 `MarketContextRetriever`를 상속한 클래스만 추가하면 된다.

## 알아두면 시간 아끼는 것들

- pandas 3.0에서 `Styler.applymap`이 제거됐다 — `.map()`을 쓴다.
- numpy/pandas `.round()`(은행원 반올림)와 Python 기본 `round()`/f-string 포맷팅은 .5 경계에서
  결과가 다를 수 있다(예: 536.85 → 536.8 vs 536.9). `humanize_dataframe()`과
  `_format_result_table()`처럼 같은 값을 두 경로로 보여줄 때는 반드시 같은 반올림 방식을 써야
  화면 표와 LLM 브리핑 문구의 숫자가 어긋나지 않는다.
- `st.chat_input`은 `st.container(height=...)`의 마지막 요소로 호출할 때만 그 컨테이너 바닥에
  고정되고, 그 외에는 항상 뷰포트 진짜 최하단으로 이동한다.

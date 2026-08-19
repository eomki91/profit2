import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"

RAW_DATA_PATH = DATA_DIR / "raw_data.xlsx"
FEW_SHOT_PATH = DATA_DIR / "few_shot_sqls.json"

# ---- LLM 설정 ----
# USE_OPENAI=false 로 지정하면 OPENAI_API_KEY가 있어도 초기 기본값이 로컬 Ollama가 된다.
# (실제 사용 엔진은 Streamlit 사이드바에서 사용자가 engine_type으로 직접 선택한다.)
USE_OPENAI = os.getenv("USE_OPENAI", "true").strip().lower() == "true"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "gpt-5.6-luna")
DEFAULT_OPENAI_TEMPERATURE = max(0.0, min(0.7, float(os.getenv("OPENAI_TEMPERATURE", "0.0"))))

OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL_NAME", "qwen2.5-coder:7b")  # 대안: "gemma2"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

ENGINE_LOCAL = "local"
ENGINE_OPENAI = "openai"


def resolve_default_engine() -> str:
    """UI 최초 렌더링 시 라디오 버튼의 기본 선택값을 결정한다."""
    if USE_OPENAI and OPENAI_API_KEY:
        return ENGINE_OPENAI
    return ENGINE_LOCAL


@st.cache_resource(show_spinner=False)
def get_llm(engine_type: str, temperature: float = 0.0, api_key: str | None = None):
    """사용자가 사이드바에서 선택한 engine_type에 따라 LLM 인스턴스를 동적으로 생성한다.

    engine_type: "local" (Ollama) | "openai" (ChatGPT)
    api_key: OpenAI 선택 시 사용할 키. 없으면 .env의 OPENAI_API_KEY로 fallback한다.
    (engine_type, temperature, api_key) 조합별로 캐싱되어 동일한 설정으로는
    LLM 클라이언트를 다시 생성하지 않는다.
    """
    if engine_type == ENGINE_OPENAI:
        resolved_key = (api_key or OPENAI_API_KEY).strip()
        if not resolved_key:
            raise ValueError(
                "OpenAI 엔진을 선택했지만 API 키가 없습니다. "
                "사이드바에서 OpenAI API Key를 입력하거나 로컬 엔진으로 전환해주세요."
            )
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=OPENAI_MODEL_NAME,
            api_key=resolved_key,
            temperature=temperature,
        )

    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=OLLAMA_MODEL_NAME,
        base_url=OLLAMA_BASE_URL,
        temperature=temperature,
    )


def get_engine_status(engine_type: str) -> dict:
    """Streamlit 사이드바 뱃지 표시용 현재 선택된 엔진 정보."""
    if engine_type == ENGINE_OPENAI:
        return {"provider": ENGINE_OPENAI, "label": f"🟢 OpenAI ({OPENAI_MODEL_NAME})"}
    return {"provider": ENGINE_LOCAL, "label": f"🟡 Local Ollama ({OLLAMA_MODEL_NAME})"}


# ---- 임베딩 설정 (오프라인 기본값: BAAI/bge-m3 / 선택형: OpenAI) ----
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local")  # "local" | "openai"

LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "BAAI/bge-m3")
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")

# ---- 도메인 고정 매핑 (검증/프롬프트 그라운딩용) ----
DIVISION_PROFIT_CENTERS = {
    "판재": ["열연", "냉연", "후판"],
    "봉형강": ["철근", "H형강", "특수강"],
}

PLANT_PROFIT_CENTERS = {
    "당진": ["열연", "냉연", "후판", "특수강"],
    "순천": ["냉연"],
    "인천": ["철근", "H형강"],
    "포항": ["철근", "H형강"],
}

PROFIT_CENTER_CHANNELS = {
    "열연": ["자동차용", "실수요", "유통", None],
    "냉연": ["자동차용", "실수요", "유통", None],
    "후판": ["조선용", "실수요", "유통", None],
    "철근": [None],
    "H형강": [None],
    "특수강": [None],
}

# ---- Text-to-SQL 경량 System Prompt ----
SCHEMA_PROMPT = """
너는 철강 손익 분석 SQL 생성기다. 아래 스키마와 절대 룰을 반드시 지켜서 DuckDB 문법의 SQL만 생성해라.

[테이블: profit_center_master]
- year INTEGER, quarter VARCHAR('Q1'~'Q4'), year_month VARCHAR('YYYY-MM')
- division VARCHAR('판재','봉형강')
- profit_center VARCHAR('열연','냉연','후판' | '철근','H형강','특수강')
- sub_division_2 VARCHAR('글로벌','HMG', NULL) - 열연/냉연의 '자동차용' 물량에만 존재
- sub_division_3 VARCHAR('자동차용','조선용','실수요','유통', NULL) - NULL은 기타 채널
- plant VARCHAR('당진','순천','인천','포항')
- sales_vol BIGINT(톤), sales_amt BIGINT(원)
- unit_price / raw_material / spread / other_cost BIGINT(원/톤)
- op_profit / ord_profit BIGINT(원)

[절대 룰]
1. 단가/원부재료/스프레드는 절대 단순평균(AVG)하지 말고 매출량 가중평균으로 계산한다.
   AVG(unit_price), AVG(raw_material), AVG(spread) 형태의 SQL은 절대 생성하지 않는다.
   - 가중평균 판매단가 = SUM(sales_amt) / SUM(sales_vol)
   - 가중평균 원부재료 = SUM(raw_material * sales_vol) / SUM(sales_vol)
   - 가중평균 스프레드 = 가중평균 판매단가 - 가중평균 원부재료
   이 룰은 profit_center/division/plant/quarter 등 어떤 GROUP BY 기준으로 묶어도 동일하게 적용된다.
2. 영업이익률 = (SUM(op_profit) / SUM(sales_amt)) * 100
3. sub_division_2/3는 서로 배타적인 채널 세그먼트다. 특정 채널로 필터링하지 않는 한
   GROUP BY 시 모든 행을 그대로 합산해야 총량과 일치한다 (중복 합산 아님, 필터링해서 빼면 안 됨).
4. division-profit_center, plant-profit_center 조합은 고정되어 있다:
   판재={열연,냉연,후판}, 봉형강={철근,H형강,특수강}.
5. 현재 데이터는 2026년 단일 연도만 존재하므로 YoY(전년 대비) 비교는 계산할 수 없다.
   YoY성 질문에는 무리하게 쿼리를 생성하지 말고 데이터 부재를 알리는 주석을 SQL 앞에 남긴다.
6. 기간 대비 변화량(MoM: 전월 대비, QoQ: 전분기 대비) 질문은 반드시 다음 패턴으로 작성한다:
   ① CTE(WITH)로 각 기간(year_month 또는 quarter)별 가중평균 지표를 먼저 계산하고,
   ② 비교 대상 두 기간을 self-join(동일 dimension 컬럼, 예: profit_center 기준)하여 한 행에 나란히 놓고,
   ③ (이후 기간 값 - 이전 기간 값)으로 diff 컬럼을 만들어 ORDER BY diff로 정렬한다.
   단일 GROUP BY만으로는 두 기간 값을 한 행에서 비교할 수 없으므로 반드시 이 self-join 패턴을 사용한다.
7. 시계열 컬럼은 질문의 시간 단위에 맞는 것을 정확히 골라 쓴다.
   - 특정 '월' 질문(예: '3월', '8월') → year_month = 'YYYY-MM'
   - 특정 '분기' 질문(Q1~Q4, QoQ) → quarter = 'Qn' 또는 quarter IN ('Qn', ...)
   - '상반기/하반기/YTD/누적'처럼 여러 달에 걸친 구간 질문 → year_month BETWEEN 'YYYY-MM' AND 'YYYY-MM'
     (예: 상반기 = BETWEEN '2026-01' AND '2026-06') 또는 quarter IN ('Q1','Q2')로 범위를 지정한다.
   - 연간 전체를 다뤄야 하면 year를 사용하되, 현재 데이터는 2026년 단일 연도뿐이라는 점(룰 5)에 유의한다.
   - year_month/quarter를 혼용하거나 생략해서 의도한 기간보다 넓거나 좁은 범위를 집계하지 않도록 주의한다.
"""

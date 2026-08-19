import re
from collections.abc import Iterator

import duckdb
import pandas as pd

from src import config, db_engine, vector_store

MAX_SELF_CORRECTION_RETRIES = 2

# ---------------------------------------------------------------------------
# 단위 표준화 (경영진 보고용 가독성 향상)
# 판매량은 천 톤, 단가/원가/스프레드는 천 원/톤, 매출액/영업이익/경상이익은 억 원(1조 이상은 조+억 원)
# 으로 축약한다. LLM 프롬프트(텍스트)와 화면 표(app.py)가 이 규칙을 공유해서 쓴다.
# ---------------------------------------------------------------------------
_VOLUME_COL_HINTS = ("vol",)
_PRICE_COL_HINTS = ("price", "spread", "material", "cost")
_CURRENCY_COL_HINTS = ("amt", "profit")
_UNIT_EXCLUDE_HINTS = ("ratio", "margin", "_pct", "percent")  # 비율/마진 컬럼은 단위 변환 대상이 아님

UNIT_SUFFIX = {"volume": "천 톤", "price": "천 원/톤", "currency": "억 원"}
UNIT_DIVISOR = {"volume": 1_000, "price": 1_000, "currency": 100_000_000}
UNIT_DECIMALS = {"volume": 1, "price": 0, "currency": 0}


def get_unit_family(col_name: str) -> str | None:
    """컬럼명 패턴으로 판매량/단가·원가·스프레드/금액 단위 계열을 판별한다."""
    lower = col_name.lower()
    if any(h in lower for h in _UNIT_EXCLUDE_HINTS):
        return None
    if any(h in lower for h in _VOLUME_COL_HINTS):
        return "volume"
    if any(h in lower for h in _PRICE_COL_HINTS):
        return "price"
    if any(h in lower for h in _CURRENCY_COL_HINTS):
        return "currency"
    return None


def format_volume(value: float) -> str:
    """판매량을 '천 톤' 단위로 축약한다. 예: 536850 -> '536.9천 톤'"""
    return f"{value / 1000:,.1f}천 톤"


def format_price(value: float) -> str:
    """단가/원가/스프레드를 '천 원/톤' 단위로 축약한다. 예: 1250000 -> '1,250천 원/톤'"""
    return f"{round(value / 1000):,.0f}천 원/톤"


def format_currency(value: float) -> str:
    """매출액/영업이익 등 금액을 '억 원'(1조 이상은 '조 X억 원')으로 축약한다.
    예: -3965650000 -> '-40억 원', 1234000000000 -> '1조 2,340억 원'
    """
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1e12:
        jo = int(v // 1e12)
        rem_eok = round((v - jo * 1e12) / 1e8)
        if rem_eok >= 10000:
            jo += 1
            rem_eok -= 10000
        return f"{sign}{jo}조 {rem_eok:,.0f}억 원"
    eok = round(v / 1e8)
    if eok == 0:
        sign = ""
    return f"{sign}{eok:,.0f}억 원"


_UNIT_FORMATTERS = {"volume": format_volume, "price": format_price, "currency": format_currency}


# ---------------------------------------------------------------------------
# 영문 컬럼명 -> 한글 필드명 매핑 (화면 표/차트 렌더링용)
# SQL_TEMPLATES와 few_shot_sqls.json에서 실제로 쓰이는 별칭(alias)을 모두 포함한다.
# 여기 없는(LLM이 새로 만든) 별칭은 get_display_label()이 "{원본}(단위)" 형태로 자동 보완한다.
# ---------------------------------------------------------------------------
COLUMN_KOREAN_MAP: dict[str, str] = {
    # 차원(카테고리) 컬럼
    "year": "연도",
    "quarter": "분기",
    "year_month": "연월",
    "division": "본부(구분)",
    "profit_center": "손익센터",
    "plant": "공장",
    "sub_division_1": "고로구분1",
    "sub_division_2": "고로구분2",
    "sub_division_3": "고로구분3",
    "client_group": "고객구분",
    # 판매량 (천 톤)
    "sales_vol": "판매량(천 톤)",
    "total_vol": "판매량(천 톤)",
    "auto_vol": "자동차용 판매량(천 톤)",
    # 단가/원가/스프레드 (천 원/톤)
    "unit_price": "판매단가(천 원/톤)",
    "avg_price": "평균단가(천 원/톤)",
    "raw_material": "원료단가(천 원/톤)",
    "avg_raw_material": "평균원료단가(천 원/톤)",
    "other_cost": "기타비용(천 원/톤)",
    "spread": "스프레드(천 원/톤)",
    "avg_spread": "평균스프레드(천 원/톤)",
    "spread_diff": "스프레드 변화(천 원/톤)",
    "spread_gap": "스프레드 차이(천 원/톤)",
    "prev_spread": "전기 스프레드(천 원/톤)",
    "cur_spread": "당기 스프레드(천 원/톤)",
    "apr_spread": "4월 스프레드(천 원/톤)",
    "may_spread": "5월 스프레드(천 원/톤)",
    "q1_spread": "1분기 스프레드(천 원/톤)",
    "q2_spread": "2분기 스프레드(천 원/톤)",
    "dangjin_spread": "당진 스프레드(천 원/톤)",
    "suncheon_spread": "순천 스프레드(천 원/톤)",
    "auto_spread": "자동차용 스프레드(천 원/톤)",
    # 금액 (억 원)
    "sales_amt": "매출액(억 원)",
    "total_sales_amt": "매출액(억 원)",
    "op_profit": "영업이익(억 원)",
    "total_op_profit": "영업이익(억 원)",
    "ord_profit": "경상이익(억 원)",
    "ordinary_profit": "경상이익(억 원)",
    # 비율(%) - 단위 변환 없이 라벨만 붙인다
    "op_margin": "영업이익률(%)",
    "auto_vol_ratio": "자동차용 판매비중(%)",
    "sales_amt_ratio": "매출액 비중(%)",
}


def get_display_label(col_name: str) -> str:
    """컬럼명을 화면 표시용 라벨로 변환한다. COLUMN_KOREAN_MAP에 있으면 한글 라벨,
    없지만 단위 계열이 감지되면 '{원본명}(단위)', 둘 다 아니면 원본명 그대로 반환한다.
    """
    if col_name in COLUMN_KOREAN_MAP:
        return COLUMN_KOREAN_MAP[col_name]
    family = get_unit_family(col_name)
    if family:
        return f"{col_name}({UNIT_SUFFIX[family]})"
    return col_name


def humanize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """화면 표시용으로 컬럼명을 한글(또는 '영문(단위)')로 바꾸고 값을 단위 규칙에 맞게
    축약한 사본을 반환한다. 원본 df(차트 계산/원본 분석용)는 변경하지 않는다.
    """
    display_df = df.copy()
    rename_map = {}
    for col in df.columns:
        family = get_unit_family(col)
        if family is not None and pd.api.types.is_numeric_dtype(df[col]):
            divisor, decimals = UNIT_DIVISOR[family], UNIT_DECIMALS[family]
            # pandas/numpy의 round()는 은행원 반올림(round-half-to-even)이라 부동소수점 오차로
            # Python 기본 round()·문자열 포맷팅과 결과가 갈릴 수 있다(예: 536.85 -> 536.8 vs 536.9).
            # _format_result_table의 LLM 프롬프트 문자열과 화면 표 수치가 어긋나지 않도록
            # 같은 Python round()를 명시적으로 써서 두 경로의 반올림 결과를 일치시킨다.
            display_df[col] = df[col].map(lambda v, d=divisor, n=decimals: round(v / d, n))
        rename_map[col] = get_display_label(col)
    return display_df.rename(columns=rename_map)


# ---------------------------------------------------------------------------
# 템플릿 기반 라우팅 (Template Routing)
# 자주 쓰이는 추천 질문은 LLM 호출 없이 사전 정의된 SQL 템플릿으로 즉시 응답해 지연을 없앤다.
# 모두 가중평균 룰(SUM(sales_amt)/SUM(sales_vol) 등)을 완비한 검증된 SQL이다.
# ---------------------------------------------------------------------------
SQL_TEMPLATES: dict[str, str] = {
    "monthly_summary": (
        "SELECT profit_center, SUM(sales_vol) AS total_vol, "
        "ROUND(SUM(sales_amt)/SUM(sales_vol)) AS avg_price, "
        "ROUND(SUM(sales_amt)/SUM(sales_vol) - SUM(raw_material*sales_vol)/SUM(sales_vol)) AS avg_spread, "
        "SUM(op_profit) AS total_op_profit, "
        "ROUND((SUM(op_profit)*1.0/SUM(sales_amt))*100, 2) AS op_margin "
        "FROM profit_center_master WHERE year_month = '{year_month}' "
        "GROUP BY profit_center ORDER BY total_op_profit DESC;"
    ),
    "mom_spread_gap": (
        "WITH monthly_spread AS ("
        "SELECT year_month, profit_center, "
        "ROUND(SUM(sales_amt)/SUM(sales_vol) - SUM(raw_material*sales_vol)/SUM(sales_vol)) AS spread "
        "FROM profit_center_master WHERE year_month IN ('{prev_ym}', '{cur_ym}') "
        "GROUP BY year_month, profit_center) "
        "SELECT cur.profit_center, prev.spread AS prev_spread, cur.spread AS cur_spread, "
        "(cur.spread - prev.spread) AS spread_diff "
        "FROM (SELECT * FROM monthly_spread WHERE year_month = '{cur_ym}') cur "
        "JOIN (SELECT * FROM monthly_spread WHERE year_month = '{prev_ym}') prev "
        "ON cur.profit_center = prev.profit_center ORDER BY spread_diff ASC;"
    ),
    "auto_margin": (
        "SELECT sub_division_2 AS client_group, profit_center, SUM(sales_vol) AS total_vol, "
        "ROUND(SUM(sales_amt)/SUM(sales_vol)) AS avg_price, "
        "ROUND(SUM(sales_amt)/SUM(sales_vol) - SUM(raw_material*sales_vol)/SUM(sales_vol)) AS avg_spread, "
        "SUM(op_profit) AS op_profit "
        "FROM profit_center_master WHERE year_month = '{year_month}' AND sub_division_3 = '자동차용' "
        "GROUP BY sub_division_2, profit_center;"
    ),
    "h1_plant_compare": (
        "SELECT quarter, plant, SUM(sales_vol) AS total_vol, SUM(op_profit) AS total_op_profit, "
        "ROUND((SUM(op_profit)*1.0/SUM(sales_amt))*100, 2) AS op_margin "
        "FROM profit_center_master WHERE division = '{division}' AND quarter IN ('Q1', 'Q2') "
        "GROUP BY quarter, plant ORDER BY plant, quarter;"
    ),
    "deficit_search": (
        "SELECT year_month, profit_center, SUM(op_profit) AS total_op_profit "
        "FROM profit_center_master GROUP BY year_month, profit_center "
        "HAVING SUM(op_profit) < 0 ORDER BY year_month, total_op_profit ASC;"
    ),
}

_MONTH_RE = re.compile(r"(\d{1,2})\s*월")
_DIVISIONS = ("판재", "봉형강")


def _extract_months(text: str) -> list[int]:
    """텍스트에서 'N월' 패턴의 월(1~12)을 순서대로 추출한다."""
    return [m for m in (int(g) for g in _MONTH_RE.findall(text)) if 1 <= m <= 12]


def _match_deficit_search(text: str, months: list[int]) -> str | None:
    if "적자" in text:
        return SQL_TEMPLATES["deficit_search"]
    return None


def _match_mom_spread_gap(text: str, months: list[int]) -> str | None:
    if len(months) == 1 and any(k in text for k in ("전월", "MoM", "mom")):
        month = months[0]
        if month <= 1:
            return None  # 1월은 전월(전년도) 데이터가 없어 템플릿 적용 불가 -> LLM Fallback
        return SQL_TEMPLATES["mom_spread_gap"].format(
            cur_ym=f"2026-{month:02d}", prev_ym=f"2026-{month - 1:02d}"
        )
    return None


def _match_auto_margin(text: str, months: list[int]) -> str | None:
    if "자동차" in text and len(months) == 1:
        return SQL_TEMPLATES["auto_margin"].format(year_month=f"2026-{months[0]:02d}")
    return None


def _match_h1_plant_compare(text: str, months: list[int]) -> str | None:
    if "상반기" in text and "공장" in text:
        division = next((d for d in _DIVISIONS if d in text), None)
        if division:
            return SQL_TEMPLATES["h1_plant_compare"].format(division=division)
    return None


def _match_monthly_summary(text: str, months: list[int]) -> str | None:
    if len(months) == 1 and not any(k in text for k in ("자동차", "전월", "MoM", "mom", "상반기")):
        return SQL_TEMPLATES["monthly_summary"].format(year_month=f"2026-{months[0]:02d}")
    return None


# 더 구체적인(오탐 위험이 적은) 매처를 먼저 검사하고, 범용 matcher(monthly_summary)는 마지막에 둔다.
_TEMPLATE_MATCHERS = (
    _match_deficit_search,
    _match_mom_spread_gap,
    _match_auto_margin,
    _match_h1_plant_compare,
    _match_monthly_summary,
)


def match_sql_template(user_query: str) -> str | None:
    """자주 쓰는 질문 패턴(추천 질문 등)을 감지해 검증된 SQL을 즉시 반환한다 (LLM 미호출)."""
    months = _extract_months(user_query)
    for matcher in _TEMPLATE_MATCHERS:
        sql = matcher(user_query, months)
        if sql:
            return sql
    return None


# ---------------------------------------------------------------------------
# SQL 캐시: 동일 질문(+엔진/temperature)을 다시 물으면 LLM 재생성을 건너뛴다.
# 템플릿은 사전 검증되어 있어 캐시가 필요 없고, LLM Fallback 결과만 캐시한다.
# (극히 드물게 캐시된 SQL이 실행 실패하면 run_with_self_correction의
#  Self-Correction 루프가 별도로 복구하므로 안전하다.)
# ---------------------------------------------------------------------------
_SQL_CACHE: dict[str, str] = {}


def _cache_key(user_query: str, engine_type: str, temperature: float) -> str:
    return f"{engine_type}|{temperature}|{user_query.strip()}"


SYNTHESIS_PROMPT_TEMPLATE = """
너는 철강회사의 FP&A(경영관리/손익분석) 담당자다. 아래 SQL 실행 결과만을 근거로
경영진에게 보고할 심층 분석 브리핑을 작성해라.

[사용자 질문]
{user_query}

[실행된 SQL]
{sql}

[SQL 실행 결과]
{result_table}

[작성 규칙]
- 반드시 아래 3개 섹션 제목을 그대로, 이 순서로 포함해서 작성한다. 섹션 제목 앞에 #, ## 같은
  마크다운 헤딩 기호는 붙이지 말고 이모지+텍스트 그대로 일반 줄로 작성한다.
- 표에 있는 숫자만 근거로 사용하고, 표에 없는 정보(실제 시황 원인 등)는 추정하지 않는다.
- 이번 SQL 결과에 전월/전분기 비교 컬럼이 없는 것은 "그 시점의 데이터가 시스템에 없다"는 뜻이 아니라
  "이번 조회가 단일 기간만 조회했다"는 뜻이다. "전월 데이터가 없어 산출할 수 없습니다"처럼 데이터
  부재로 오해할 문장을 쓰지 말고, "이번 조회는 3월 단일 월 기준이라 전월 대비 비교는 포함하지
  않았습니다"처럼 조회 범위의 한계로 정확히 표현한다.
- [단위 표준화] 표의 판매량은 '천 톤', 단가/원가/스프레드는 '천 원/톤', 매출액·영업이익·경상이익은
  '억 원'(1조 이상은 '조 X억 원')으로 이미 축약되어 있다. 표에 있는 문자열을 그대로 인용하고
  1,250,000,000원처럼 원 단위로 풀어서 다시 쓰지 않는다. 네가 직접 계산해서 새로 언급하는
  수치(두 값의 차이·합계 등)도 반드시 같은 방식(천 톤/천 원/톤/억 원/조 원)으로 축약해서 표기한다.
- [컬러 하이라이팅] 증감·변동을 나타내는 수치와 핵심 표현은 Streamlit 마크다운 컬러 문법으로
  감싼다. 스프레드 확대, 매출·이익 증가, 흑자 전환처럼 긍정적 변화는 **:red[+수치 및 표현]**,
  스프레드 축소, 매출·이익 감소, 적자 지속·전환처럼 악화 신호는 **:blue[-수치 및 표현]** 형태로
  작성한다. 예: 스프레드가 **:red[+14천 원/톤 상승]**했습니다. / 영업이익이 **:blue[-120억 원 적자]**로 전환됐습니다.
  증감 방향이 없는 단순 현황 수치(예: 이번 달 판매량)에는 색을 입히지 않는다.

📌 핵심 결론
(두괄식 1~2문장: 질문의 핵심 대상 중 1위/최대 변화 품목·세그먼트와 구체적인 수치(증감액·증감률)를
 위 단위 표준화·컬러 하이라이팅 규칙에 맞춰 먼저 제시)

💡 손익 요인 분석
(표에 판매단가/원부재료 관련 컬럼이 있으면, 판가 변동과 원가 변동 중 어느 쪽이 스프레드 변화를 주도했는지
 마진 믹스 관점에서 설명. 관련 컬럼이 없으면 "제공된 데이터에는 판가/원가 분해 정보가 없어 요인 분석이 제한적입니다"라고 명시)

⚠️ 주의 필요 세그먼트
(결과에 적자(음수 영업이익/경상이익) 또는 스프레드 급락 구간이 있으면 :blue[...]로 강조해 구체적으로
 짚어준다. 없다면 "특이 적자 구간 없음"이라고 명시)
"""


def _build_prompt(user_query: str) -> str:
    examples = vector_store.get_similar_sql_examples(user_query, k=2)
    example_block = "\n\n".join(
        f"Q: {ex['question']}\nSQL: {ex['sql']}" for ex in examples
    )
    return (
        f"{config.SCHEMA_PROMPT}\n\n"
        f"[참고 예시 (Few-Shot)]\n{example_block}\n\n"
        f"[사용자 질문]\n{user_query}\n\n"
        "위 스키마와 절대 룰을 준수하여 DuckDB 문법의 SQL 쿼리 1개만 생성해라. "
        "다른 설명 없이 ```sql ... ``` 코드 블록으로만 출력해라."
    )


def extract_sql(llm_output: str) -> str:
    match = re.search(r"```sql\s*(.*?)```", llm_output, re.DOTALL | re.IGNORECASE)
    sql = match.group(1) if match else llm_output
    sql = sql.strip().rstrip(";")
    return f"{sql};"


def stream_sql_tokens(
    user_query: str,
    engine_type: str,
    temperature: float = 0.0,
    api_key: str | None = None,
) -> Iterator[str]:
    """SQL 생성 과정을 토큰 단위로 스트리밍한다.

    템플릿 매칭 또는 캐시에 걸리면 LLM 호출 없이 SQL을 즉시 한 번에 반환하고,
    그렇지 않을 때만 실제 LLM 스트리밍으로 Fallback한다.
    Streamlit의 st.write_stream()과 함께 사용하며, 반환된 전체 텍스트는
    extract_sql()로 최종 SQL만 추출해 사용한다.
    """
    templated_sql = match_sql_template(user_query)
    if templated_sql:
        yield f"-- ⚡ 템플릿 매칭: LLM 생성 없이 즉시 실행\n```sql\n{templated_sql}\n```"
        return

    cache_key = _cache_key(user_query, engine_type, temperature)
    cached_sql = _SQL_CACHE.get(cache_key)
    if cached_sql:
        yield f"-- ⚡ 캐시된 SQL 재사용\n```sql\n{cached_sql}\n```"
        return

    llm = config.get_llm(engine_type, temperature=temperature, api_key=api_key)
    prompt = _build_prompt(user_query)
    full_text = ""
    for chunk in llm.stream(prompt):
        if chunk.content:
            full_text += chunk.content
            yield chunk.content
    _SQL_CACHE[cache_key] = extract_sql(full_text)


def generate_sql(
    user_query: str,
    engine_type: str,
    temperature: float = 0.0,
    api_key: str | None = None,
) -> str:
    """템플릿 매칭 → 캐시 → LLM 생성 순으로 SQL을 확보한다 (스트리밍 없는 단발 호출용)."""
    templated_sql = match_sql_template(user_query)
    if templated_sql:
        return templated_sql

    cache_key = _cache_key(user_query, engine_type, temperature)
    cached_sql = _SQL_CACHE.get(cache_key)
    if cached_sql:
        return cached_sql

    llm = config.get_llm(engine_type, temperature=temperature, api_key=api_key)
    prompt = _build_prompt(user_query)
    response = llm.invoke(prompt)
    sql = extract_sql(response.content)
    _SQL_CACHE[cache_key] = sql
    return sql


def _build_correction_prompt(user_query: str, failed_sql: str, error_message: str) -> str:
    examples = vector_store.get_similar_sql_examples(user_query, k=2)
    example_block = "\n\n".join(
        f"Q: {ex['question']}\nSQL: {ex['sql']}" for ex in examples
    )
    return (
        f"{config.SCHEMA_PROMPT}\n\n"
        f"[참고 예시 (Few-Shot)]\n{example_block}\n\n"
        f"[사용자 질문]\n{user_query}\n\n"
        f"[이전에 생성한 SQL (실행 실패)]\n{failed_sql}\n\n"
        f"[DuckDB 오류 메시지]\n{error_message}\n\n"
        "위 오류 메시지의 원인을 분석해서 문제를 수정한 새로운 DuckDB SQL 쿼리 1개만 다시 생성해라. "
        "스키마의 절대 룰(가중평균 등)은 그대로 유지하고, 다른 설명 없이 ```sql ... ``` 코드 블록으로만 출력해라."
    )


def regenerate_sql(
    user_query: str,
    failed_sql: str,
    error_message: str,
    engine_type: str,
    temperature: float = 0.0,
    api_key: str | None = None,
) -> str:
    llm = config.get_llm(engine_type, temperature=temperature, api_key=api_key)
    prompt = _build_correction_prompt(user_query, failed_sql, error_message)
    response = llm.invoke(prompt)
    return extract_sql(response.content)


def run_with_self_correction(
    user_query: str,
    engine_type: str,
    temperature: float = 0.0,
    api_key: str | None = None,
    max_retries: int = MAX_SELF_CORRECTION_RETRIES,
    initial_sql: str | None = None,
) -> tuple[str, pd.DataFrame, int]:
    """SQL 실행이 실패(Syntax Error 등)하면 오류 메시지를 프롬프트에 재주입해
    최대 max_retries회까지 (선택된 엔진의) LLM이 스스로 SQL을 재생성·재실행하는
    Self-Correction 루프.

    initial_sql이 주어지면 (예: UI에서 이미 스트리밍으로 생성해둔 SQL) 최초 생성 호출을
    건너뛰고 그 SQL부터 실행을 시도한다.

    반환값: (최종 실행에 성공한 SQL, 결과 DataFrame, 재시도 횟수)
    """
    sql = initial_sql or generate_sql(user_query, engine_type, temperature, api_key)
    last_error: str | None = None

    for attempt in range(max_retries + 1):
        try:
            df = db_engine.run_query(sql)
            return sql, df, attempt
        except duckdb.Error as e:
            last_error = str(e)
            if attempt == max_retries:
                break
            sql = regenerate_sql(user_query, sql, last_error, engine_type, temperature, api_key)

    raise RuntimeError(
        f"{max_retries + 1}회 시도했지만 SQL 실행에 계속 실패했습니다. "
        f"마지막 오류: {last_error}\n마지막 시도 SQL: {sql}"
    )


def _format_result_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    """SQL 결과를 프롬프트에 주입할 텍스트 표로 변환한다 (토큰 절약을 위해 상위 max_rows행만).

    판매량/단가·원가·스프레드/금액 컬럼은 미리 '천 톤·천 원/톤·억(조) 원' 문자열로 축약해서 넣는다.
    LLM이 원 단위 큰 숫자(예: 1.68e+11, 1,250,000,000원)를 직접 변환하다 자릿수를 착각하는
    사례가 실측되어, 변환을 파이썬에서 미리 끝내고 LLM은 그 문자열을 그대로 인용만 하게 한다.
    """
    display_df = df.head(max_rows) if len(df) > max_rows else df
    display_df = display_df.copy()

    for col in display_df.columns:
        if not pd.api.types.is_numeric_dtype(display_df[col]):
            continue
        family = get_unit_family(col)
        if family:
            display_df[col] = display_df[col].map(_UNIT_FORMATTERS[family])

    table_text = display_df.to_string(index=False, float_format=lambda x: f"{x:,.2f}")
    if len(df) > max_rows:
        return f"{table_text}\n... (총 {len(df)}행 중 상위 {max_rows}행만 표시)"
    return table_text


def _build_synthesis_prompt(user_query: str, sql: str, df: pd.DataFrame) -> str:
    return SYNTHESIS_PROMPT_TEMPLATE.format(
        user_query=user_query,
        sql=sql,
        result_table=_format_result_table(df),
    )


def stream_synthesis_tokens(
    user_query: str,
    sql: str,
    df: pd.DataFrame,
    engine_type: str,
    temperature: float = 0.0,
    api_key: str | None = None,
) -> Iterator[str]:
    """SQL 실행 결과를 FP&A 담당자 관점의 심층 브리핑으로 해석(Synthesis)하여
    토큰 단위로 스트리밍한다. st.write_stream()과 함께 사용한다.
    """
    if df.empty:
        yield "조건에 해당하는 데이터가 없습니다."
        return

    llm = config.get_llm(engine_type, temperature=temperature, api_key=api_key)
    prompt = _build_synthesis_prompt(user_query, sql, df)
    for chunk in llm.stream(prompt):
        if chunk.content:
            yield chunk.content


def summarize_result(df: pd.DataFrame) -> str:
    """DataFrame을 경량 규칙 기반으로 한 줄 요약한다 (추가 LLM 호출 없음)."""
    if df.empty:
        return "조건에 해당하는 데이터가 없습니다."

    top_row = df.iloc[0]
    pairs = ", ".join(f"{col}={top_row[col]}" for col in df.columns)
    return f"총 {len(df)}건 조회됨. 최상위 행 → {pairs}"


def ask(
    user_query: str,
    engine_type: str = config.ENGINE_LOCAL,
    temperature: float = 0.0,
    api_key: str | None = None,
) -> tuple[str, pd.DataFrame, str]:
    """[질문 -> Few-Shot 검색 -> SQL 생성(Self-Correction 포함) -> 결과+요약] 체인.

    engine_type/temperature/api_key는 Streamlit 사이드바에서 사용자가 선택한 값을 그대로 전달한다.
    스트리밍 UI 없이 한 번에 결과가 필요할 때(테스트, 배치 등) 사용한다.
    """
    sql, df, _retries = run_with_self_correction(user_query, engine_type, temperature, api_key)
    summary = summarize_result(df)
    return sql, df, summary

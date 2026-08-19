import base64
import html
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import config, db_engine, sql_agent

# ----------------------------------------------------------------------------
# 브랜드 아이덴티티
# ----------------------------------------------------------------------------
NAVY_DEEP = "#002B49"
NAVY = "#0A2540"
SLATE = "#3E5C76"
BORDER = "#DCE3EA"
BG_ASSISTANT = "#FFFFFF"
BG_APP = "#F4F6F9"

LOGO_PATH = Path(__file__).resolve().parent / "logo.png"

RECOMMENDED_QUESTIONS = {
    "📅 월별 손익 요약": "3월 품목별 스프레드 및 손익 실적 요약해줘",
    "📉 MoM 스프레드 갭": "5월 전월 대비 스프레드 최대 하락 품목은?",
    "🚗 자동차용 마진 현황": "8월 자동차용(HMG/글로벌) 스프레드 현황 알려줘",
    "🏭 상반기 공장별 비교": "상반기 분기별(Q1 vs Q2) 봉형강 공장별 영업이익 비교해줘",
    "⚠️ 적자 품목 탐색": "월별 영업이익 적자가 난 품목과 해당 월 목록 뽑아줘",
}

ENGINE_LABELS = {
    config.ENGINE_LOCAL: "🏠 로컬 LLM (Ollama)",
    config.ENGINE_OPENAI: "🌐 OpenAI (ChatGPT)",
}

st.set_page_config(
    page_title="Hyundai-Steel Margin Copilot",
    page_icon="📊",
    layout="wide",
)


# ----------------------------------------------------------------------------
# 스타일 (CSS)
# ----------------------------------------------------------------------------
def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        .stApp {{ background: {BG_APP}; }}
        .block-container {{ padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1400px; }}

        .hsmc-header {{
            background: linear-gradient(120deg, {NAVY_DEEP} 0%, {NAVY} 55%, {SLATE} 100%);
            border-radius: 14px;
            padding: 18px 26px;
            display: flex;
            align-items: center;
            gap: 16px;
            box-shadow: 0 4px 14px rgba(10, 37, 64, 0.28);
            height: 100%;
        }}
        .hsmc-header .logo-box {{
            background: #FFFFFF;
            border-radius: 8px;
            padding: 6px 12px;
            display: flex;
            align-items: center;
            flex-shrink: 0;
        }}
        .hsmc-header .logo-box img {{ height: 34px; display: block; }}
        .hsmc-header .logo-fallback {{ font-size: 26px; line-height: 1; }}
        .hsmc-header h1 {{
            color: #FFFFFF; font-size: 1.35rem; margin: 0; font-weight: 700; letter-spacing: 0.2px;
        }}
        .hsmc-header p {{ color: #C9D6E3; margin: 3px 0 0 0; font-size: 0.82rem; }}

        div[data-testid="stButton"] > button {{
            border-radius: 999px;
            border: 1px solid {SLATE};
            color: {NAVY};
            background: #FFFFFF;
            font-size: 0.86rem;
            padding: 6px 10px;
        }}
        div[data-testid="stButton"] > button:hover {{
            background: {NAVY};
            color: #FFFFFF;
            border-color: {NAVY};
        }}

        .chat-row {{ display: flex; margin: 10px 2px; }}
        .chat-row.user {{ justify-content: flex-end; }}
        .chat-row.assistant {{ justify-content: flex-start; align-items: flex-start; gap: 8px; }}

        .avatar {{
            width: 32px; height: 32px; min-width: 32px; border-radius: 50%;
            background: {NAVY}; color: #FFFFFF; display: flex; align-items: center; justify-content: center;
            font-size: 15px; margin-top: 2px;
        }}

        .bubble {{
            max-width: 74%;
            padding: 10px 16px;
            border-radius: 16px;
            line-height: 1.55;
            font-size: 0.92rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }}
        .bubble.user {{
            background: {NAVY};
            color: #FFFFFF;
            border-radius: 16px 16px 4px 16px;
            white-space: pre-wrap;
        }}
        .bubble.assistant {{
            background: {BG_ASSISTANT};
            color: #1C2B36;
            border: 1px solid {BORDER};
            border-radius: 16px 16px 16px 4px;
        }}
        /* 마크다운이 <p>/<ul> 등 블록 요소로 렌더링되므로 pre-wrap을 쓰지 않고
           블록 간 여백만 촘촘히 조정해 빈 줄이 이중으로 겹치는 것을 막는다. */
        .bubble.assistant p, .bubble.assistant ul, .bubble.assistant ol {{
            margin: 0.45em 0;
        }}
        .bubble.assistant > *:first-child {{ margin-top: 0; }}
        .bubble.assistant > *:last-child {{ margin-bottom: 0; }}
        /* LLM이 섹션 제목에 markdown # 문법을 섞어 쓸 때가 있어, h1~h3가 브라우저
           기본 크기로 커지지 않도록 본문 폰트 크기에 맞춰 통일한다. */
        .bubble.assistant h1, .bubble.assistant h2, .bubble.assistant h3 {{
            font-size: 1em;
            font-weight: 700;
            margin: 0.6em 0 0.3em 0;
            line-height: 1.4;
        }}
        .bubble.assistant.error {{
            background: #FDEDEC;
            border-color: #E9B6B1;
            color: #8A1F1F;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------------
# 헤더 / 말풍선 렌더링 헬퍼
# ----------------------------------------------------------------------------
def _load_logo_html() -> str:
    """로고가 진한 남색 계열이라 네이비 헤더 배경에 묻히므로, 흰 배경 칩으로 감싸 대비를 확보한다."""
    if LOGO_PATH.exists():
        b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode()
        inner = f'<img src="data:image/png;base64,{b64}" alt="logo" />'
    else:
        inner = '<span class="logo-fallback">🏭</span>'
    return f'<div class="logo-box">{inner}</div>'


def render_header() -> None:
    st.markdown(
        f"""
        <div class="hsmc-header">
            {_load_logo_html()}
            <div>
                <h1>현대제철 손익 분석 챗봇</h1>
                <p>Hyundai-Steel Margin Copilot · 매출량 가중평균 기반 FP&amp;A 분석</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_user_bubble(text: str) -> None:
    st.markdown(
        f'<div class="chat-row user"><div class="bubble user">{html.escape(text)}</div></div>',
        unsafe_allow_html=True,
    )


def render_assistant_bubble(text: str, is_error: bool = False) -> None:
    error_class = " error" if is_error else ""
    st.markdown(
        f"""
        <div class="chat-row assistant">
            <div class="avatar">🤖</div>
            <div class="bubble assistant{error_class}">{html.escape(text)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


_NETWORK_ERROR_HINTS = (
    "Cannot assign requested address", "Connection refused", "Max retries exceeded",
    "Failed to establish a new connection", "Name or service not known",
    "Errno 99", "Errno 111", "Errno 110", "getaddrinfo failed", "Connect call failed",
    "timed out", "ConnectError", "ConnectTimeout",
)


def _build_error_message(exc: Exception, engine_type: str) -> str:
    """예외 종류에 따라 서로 다른 원인의 오류를 다른 안내 문구로 구분해서 보여준다.

    네트워크/연결 오류(예: 배포 서버에서 로컬 Ollama에 접근 불가)를 SQL 생성 실패와
    같은 "질문을 구체적으로 표현하세요" 문구로 뭉뚱그리면 사용자가 오해하기 쉽다.
    """
    error_str = str(exc)
    is_network_error = isinstance(exc, (OSError, ConnectionError)) or any(
        hint in error_str for hint in _NETWORK_ERROR_HINTS
    )

    if is_network_error:
        if engine_type == config.ENGINE_LOCAL:
            guidance = (
                "🏠 로컬 LLM(Ollama)에 연결할 수 없습니다. 이 앱이 클라우드 등 다른 서버에 배포된 경우, "
                "그 서버는 여러분의 PC에서 실행 중인 Ollama에 접근할 방법이 없습니다 "
                "(localhost는 배포 서버 자기 자신을 가리킵니다). "
                "우측 상단 ⚙️ 설정에서 🌐 OpenAI 엔진으로 전환해 주세요."
            )
        else:
            guidance = (
                "🌐 OpenAI 엔진에 연결할 수 없습니다. 네트워크 상태나 API 키가 유효한지 "
                "우측 상단 ⚙️ 설정에서 확인해 주세요."
            )
        return f"{guidance}\n\n_(오류 상세: {error_str})_"

    return (
        "죄송합니다, 질문을 분석하는 중 문제가 발생했어요. "
        "질문을 조금 더 구체적으로(예: 기간·품목·지표를 명시) 표현해서 다시 시도해 주세요.\n"
        f"(오류 상세: {error_str})"
    )


# ----------------------------------------------------------------------------
# 데이터 포맷팅 / 시각화 헬퍼
# ----------------------------------------------------------------------------
def style_dataframe(df: pd.DataFrame):
    """단위 표준화(판매량=천 톤, 단가/원가/스프레드=천 원/톤, 금액=억 원) + 천 단위 쉼표 +
    음수 붉은색 강조를 적용한 Styler를 반환한다. 원본 df(차트용)는 변경하지 않는다.
    """
    display_df = sql_agent.humanize_dataframe(df)
    numeric_cols = [c for c in display_df.select_dtypes(include="number").columns if c != "year"]

    def _color_negative(val):
        if isinstance(val, (int, float)) and val < 0:
            return "color: #e74c3c; font-weight: 600;"
        return ""

    format_map = {col: ("{:,.1f}" if "천 톤" in col else "{:,.0f}") for col in numeric_cols}
    styler = display_df.style.format(format_map)
    if numeric_cols:
        styler = styler.map(_color_negative, subset=numeric_cols)
    return styler


def build_chart(df: pd.DataFrame):
    """비교 가능한 카테고리+수치 조합이 있을 때만 한글 라벨의 Bar/Waterfall 차트를 생성한다.

    카테고리/지표 컬럼 판별은 원본(영문) 컬럼명 기준 휴리스틱으로 하고, 실제 플로팅은
    단위 축약 + 한글 컬럼명이 적용된 humanize_dataframe() 결과로 그려 축·범례·툴팁이
    모두 한글/단위로 보이게 한다.
    """
    if df.empty or len(df) < 2:
        return None

    categorical_cols = [
        c for c in df.columns
        if (pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_string_dtype(df[c]))
        and 1 < df[c].nunique() <= 20
    ]
    numeric_cols = list(df.select_dtypes(include="number").columns)

    if not categorical_cols or not numeric_cols:
        return None

    display_df = sql_agent.humanize_dataframe(df)
    label = sql_agent.get_display_label

    # 전월/전분기 대비 diff 컬럼이 있으면 Waterfall로 변화량을 보여준다.
    diff_cols = [c for c in numeric_cols if "diff" in c.lower()]
    if diff_cols:
        metric_col, cat_col = diff_cols[0], categorical_cols[0]
        disp_metric, disp_cat = label(metric_col), label(cat_col)
        ordered = display_df.sort_values(disp_metric)
        fig = go.Figure(
            go.Waterfall(
                x=ordered[disp_cat].astype(str),
                y=ordered[disp_metric],
                connector={"line": {"color": "rgba(150,150,150,0.4)"}},
                increasing={"marker": {"color": "#2ecc71"}},
                decreasing={"marker": {"color": "#e74c3c"}},
            )
        )
        # disp_metric 라벨에 이미 "변화/차이"라는 의미가 들어있는 경우가 많아(예: 스프레드 변화),
        # 제목에 "변화"를 중복으로 덧붙이지 않는다.
        fig.update_layout(
            title=f"{disp_cat}별 {disp_metric}",
            xaxis_title=disp_cat, yaxis_title=disp_metric, height=380,
        )
        return fig

    priority_keywords = ["op_profit", "profit", "spread", "margin", "price", "vol"]
    metric_col = next(
        (c for kw in priority_keywords for c in numeric_cols if kw in c.lower()),
        numeric_cols[0],
    )
    disp_metric = label(metric_col)

    if len(categorical_cols) >= 2:
        cat_col, group_col = categorical_cols[0], categorical_cols[1]
        disp_cat, disp_group = label(cat_col), label(group_col)
        fig = px.bar(
            display_df, x=disp_cat, y=disp_metric, color=disp_group, barmode="group",
            text_auto=True, title=f"{disp_cat} x {disp_group}별 {disp_metric} 비교",
        )
    else:
        disp_cat = label(categorical_cols[0])
        fig = px.bar(
            display_df, x=disp_cat, y=disp_metric, color=disp_cat,
            text_auto=True, title=f"{disp_cat}별 {disp_metric} 비교",
        )
        fig.update_layout(showlegend=False)

    fig.update_layout(height=380)
    return fig


def render_result_card(entry: dict) -> None:
    """어시스턴트 말풍선(① 브리핑) 아래에 ② 집계 표 -> ③ 차트 -> ④ SQL expander 순으로
    카드를 렌더링한다.
    """
    key_suffix = str(id(entry))
    _, card_col = st.columns([0.05, 0.95])
    with card_col:
        with st.container(border=True):
            if entry.get("retries"):
                st.caption(f"⚠️ 최초 SQL 실행 실패 → {entry['retries']}회 자동 수정(Self-Correction) 후 성공")

            # ② 실적 집계 데이터 표 (단위 축약 + 한글 컬럼)
            st.markdown("📋 **집계 데이터**")
            st.dataframe(
                style_dataframe(entry["df"]),
                use_container_width=True,
                key=f"df_{key_suffix}",
            )

            # ③ Plotly 시각화 차트 (한글 라벨)
            if entry.get("chart") is not None:
                st.markdown("📊 **시각화**")
                st.plotly_chart(
                    entry["chart"],
                    use_container_width=True,
                    key=f"chart_{key_suffix}",
                )

            # ④ 생성된 SQL — 최하단에 접어서 배치
            with st.expander("🔍 생성된 분석 쿼리(SQL) 확인하기", expanded=False):
                st.code(entry["sql"], language="sql")


def render_history_entry(entry: dict) -> None:
    if entry["role"] == "user":
        render_user_bubble(entry["content"])
        return

    if entry.get("error"):
        render_assistant_bubble(entry["content"], is_error=True)
        return

    render_assistant_bubble(entry["summary"])
    render_result_card(entry)


# ----------------------------------------------------------------------------
# 관리자 모드: 헤더 톱니바퀴 클릭 시 뜨는 설정 모달 (하단 입력창을 가리지 않도록 분리)
# ----------------------------------------------------------------------------
@st.dialog("⚙️ 시스템 설정 및 DB 상태")
def render_settings_dialog() -> None:
    st.markdown("**🤖 LLM 엔진 설정**")
    engine_type = st.radio(
        "사용할 LLM 엔진을 선택하세요",
        options=[config.ENGINE_LOCAL, config.ENGINE_OPENAI],
        format_func=lambda opt: ENGINE_LABELS[opt],
        index=0 if config.resolve_default_engine() == config.ENGINE_LOCAL else 1,
        key="engine_type",
        horizontal=True,
    )

    if engine_type == config.ENGINE_OPENAI:
        openai_api_key = st.text_input(
            "OpenAI API Key",
            type="password",
            value=config.OPENAI_API_KEY,
            key="openai_api_key_input",
            help=".env에 키가 있으면 자동으로 채워집니다. 다른 키를 쓰려면 직접 입력/수정하세요.",
        )
        if not openai_api_key:
            st.warning("OpenAI API 키가 필요합니다. 키를 입력하거나 로컬 엔진으로 전환해주세요.")

    st.slider(
        "Temperature",
        min_value=0.0,
        max_value=0.7,
        value=0.0,
        step=0.05,
        key="temperature",
        help="값이 높을수록 SQL 생성이 다양해지지만 정확도는 낮아질 수 있습니다.",
    )

    engine_status = config.get_engine_status(engine_type)
    st.info(f"**현재 활성 엔진**: {engine_status['label']}")

    st.divider()
    st.markdown("**🗄️ DB 상태 (In-Memory DuckDB)**")
    try:
        status = db_engine.run_query(
            "SELECT COUNT(*) AS n, MIN(year_month) AS start_ym, MAX(year_month) AS end_ym "
            "FROM profit_center_master"
        ).iloc[0]
        st.metric("총 레코드 수", f"{int(status['n']):,} 건")
        st.caption(f"분석 가능 기간: {status['start_ym']} ~ {status['end_ym']}")
    except Exception as e:
        st.error(f"DB 초기화 실패: {e}")


# ----------------------------------------------------------------------------
# 세션 상태 초기화 + 설정값 선반영
# ----------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

# 설정 위젯 자체는 톱니바퀴를 눌러야 열리는 모달(render_settings_dialog) 안에서만 렌더링되지만,
# 모달을 한 번도 연 적이 없어도 채팅 로직이 즉시 동작해야 하므로 session_state에서 값을 먼저
# 읽고, 없으면(앱 최초 구동 / 모달 미사용) 모달 위젯의 기본값과 동일한 값으로 fallback한다.
# 모달에서 값을 한 번 바꾸면 session_state에 남아 다음부터는 그 값이 그대로 쓰인다.
engine_type = st.session_state.get("engine_type", config.resolve_default_engine())
temperature = st.session_state.get("temperature", 0.0)
openai_api_key = st.session_state.get("openai_api_key_input", config.OPENAI_API_KEY)

inject_css()

# ----------------------------------------------------------------------------
# 상단 헤더: 로고+타이틀 / 우측 상단 톱니바퀴(설정 모달 트리거)
# ----------------------------------------------------------------------------
header_col, gear_col = st.columns([0.94, 0.06], vertical_alignment="center")
with header_col:
    render_header()
with gear_col:
    if st.button("⚙️", key="open_settings", help="시스템 설정 및 DB 상태", use_container_width=True):
        render_settings_dialog()

# ----------------------------------------------------------------------------
# 중앙(채팅 스크롤) : 우측(추천 질문) = 7.5 : 2.5
# ----------------------------------------------------------------------------
col_chat, col_side = st.columns([7.5, 2.5])

with col_side:
    with st.container(border=True):
        st.markdown("#### 💡 추천 실무 분석 질문")
        st.caption("클릭하면 바로 질의가 실행됩니다.")
        for label, question in RECOMMENDED_QUESTIONS.items():
            if st.button(label, use_container_width=True, key=f"chip_{label}"):
                st.session_state["pending_query"] = question

with col_chat:
    chat_box = st.container(height=620, border=True)
    with chat_box:
        for entry in st.session_state.messages:
            render_history_entry(entry)

# 하단 고정 입력창(ChatGPT/Claude 스타일): 특정 컨테이너에 속하지 않은 채로 호출하면
# Streamlit이 자동으로 뷰포트 최하단에 고정 배치한다. 관리자 설정을 모달로 분리했기 때문에
# 이 입력창을 가리는 다른 UI가 없다.
user_input = st.chat_input("현대제철 손익 지표나 분석하고 싶은 내용을 질문하세요...")

pending_query = st.session_state.pop("pending_query", None)
query_to_run = user_input or pending_query

if query_to_run:
    st.session_state.messages.append({"role": "user", "content": query_to_run})

    with chat_box:
        render_user_bubble(query_to_run)

        if engine_type == config.ENGINE_OPENAI and not openai_api_key:
            error_text = (
                "OpenAI 엔진이 선택되어 있지만 API 키가 없습니다. "
                "우측 상단 ⚙️ 설정에서 API Key를 입력하거나 로컬 엔진(Ollama)으로 전환한 뒤 다시 질문해 주세요."
            )
            render_assistant_bubble(error_text, is_error=True)
            st.session_state.messages.append(
                {"role": "assistant", "error": True, "content": error_text}
            )
        else:
            try:
                with st.spinner("⏳ 데이터를 분석하고 손익 지표를 산출 중입니다..."):
                    initial_sql = sql_agent.generate_sql(
                        query_to_run,
                        engine_type=engine_type,
                        temperature=temperature,
                        api_key=openai_api_key,
                    )
                    sql, df, retries = sql_agent.run_with_self_correction(
                        query_to_run,
                        engine_type=engine_type,
                        temperature=temperature,
                        api_key=openai_api_key,
                        initial_sql=initial_sql,
                    )
                    chart = build_chart(df)

                # ① FP&A 요약 브리핑을 먼저 스트리밍으로 노출한 뒤, 최종 말풍선 스타일로 교체한다.
                stream_slot = st.empty()
                with stream_slot:
                    briefing = st.write_stream(
                        sql_agent.stream_synthesis_tokens(
                            query_to_run,
                            sql,
                            df,
                            engine_type=engine_type,
                            temperature=temperature,
                            api_key=openai_api_key,
                        )
                    )
                stream_slot.empty()

                entry = {
                    "role": "assistant",
                    "sql": sql,
                    "df": df,
                    "summary": briefing,
                    "chart": chart,
                    "retries": retries,
                }
                render_assistant_bubble(briefing)
                render_result_card(entry)
                st.session_state.messages.append(entry)

            except Exception as e:
                error_text = _build_error_message(e, engine_type)
                render_assistant_bubble(error_text, is_error=True)
                st.session_state.messages.append(
                    {"role": "assistant", "error": True, "content": error_text}
                )

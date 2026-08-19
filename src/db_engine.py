import duckdb
import pandas as pd
import streamlit as st

from src import config

COLUMN_RENAME_MAP = {
    "연도": "year",
    "분기": "quarter",
    "연월": "year_month",
    "고로구분": "division",
    "손익센터": "profit_center",
    "고로구분2": "sub_division_2",
    "고로구분3": "sub_division_3",
    "공장구분": "plant",
    "매출량 (톤)": "sales_vol",
    "매출액 (원)": "sales_amt",
    "판매단가 (원/톤)": "unit_price",
    "원부재료 (원/톤)": "raw_material",
    "스프레드 (원/톤)": "spread",
    "기타비용 (원/톤)": "other_cost",
    "영업이익 (원)": "op_profit",
    "경상이익 (원)": "ord_profit",
}


def _load_dataframe() -> pd.DataFrame:
    df = pd.read_excel(config.RAW_DATA_PATH)
    df = df.rename(columns=COLUMN_RENAME_MAP)

    # 시계열 롤링 분석(MoM, QoQ)을 위한 year/quarter 파생 컬럼 자동 생성
    year_month = pd.to_datetime(df["year_month"], format="%Y-%m")
    df["year"] = year_month.dt.year.astype(int)
    df["quarter"] = "Q" + year_month.dt.quarter.astype(str)

    return df


@st.cache_resource(show_spinner="DuckDB 초기화 및 데이터 적재 중...")
def get_connection() -> duckdb.DuckDBPyConnection:
    """엑셀 로드 + DuckDB 적재를 프로세스당 한 번만 수행하고 커넥션을 재사용한다."""
    conn = duckdb.connect(database=":memory:")
    df = _load_dataframe()
    conn.register("profit_center_master_df", df)
    conn.execute("CREATE TABLE profit_center_master AS SELECT * FROM profit_center_master_df")
    return conn


def run_query(sql: str) -> pd.DataFrame:
    conn = get_connection()
    return conn.execute(sql).fetchdf()

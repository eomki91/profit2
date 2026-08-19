import json
import re

import pytest

from src import config, db_engine

# unit_price/raw_material에 단순 AVG를 쓰는 것을 금지하는 정적 검사용 패턴
FORBIDDEN_NAIVE_AVG = re.compile(r"AVG\s*\(\s*(unit_price|raw_material)\b", re.IGNORECASE)


@pytest.fixture(scope="module")
def conn():
    return db_engine.get_connection()


def test_weighted_average_not_simple_average(conn):
    """[가중평균 검증]
    1) few_shot_sqls.json의 모든 템플릿이 unit_price/raw_material에 단순 AVG()를 쓰지 않고
       SUM(sales_amt)/SUM(sales_vol) 형태의 가중평균 공식을 유지하는지 정적으로 검사한다.
    2) 실제 데이터(2026-03)에서 SQL 가중평균 결과가 수기 계산(pandas)과 정확히 일치하고,
       단순 AVG(unit_price)와는 값이 달라짐을 확인한다 (두 공식이 실제로 다른 결과를 낸다는 증명).
    """
    with open(config.FEW_SHOT_PATH, encoding="utf-8") as f:
        few_shots = json.load(f)

    for item in few_shots:
        assert not FORBIDDEN_NAIVE_AVG.search(item["sql"]), (
            f"단순 평균(AVG) 사용 금지 위반: {item['question']!r}"
        )
        if "avg_price" in item["sql"] or "avg_spread" in item["sql"]:
            assert "SUM(sales_amt)" in item["sql"], (
                f"가중평균 판매단가 공식(SUM(sales_amt)/SUM(sales_vol)) 누락: {item['question']!r}"
            )

    raw = conn.execute(
        "SELECT profit_center, sales_vol, sales_amt, unit_price "
        "FROM profit_center_master WHERE year_month = '2026-03'"
    ).fetchdf()

    weighted = conn.execute(
        "SELECT profit_center, "
        "SUM(sales_amt) * 1.0 / SUM(sales_vol) AS weighted_price, "
        "AVG(unit_price) AS naive_avg_price "
        "FROM profit_center_master WHERE year_month = '2026-03' "
        "GROUP BY profit_center"
    ).fetchdf()

    checked_a_multi_row_group = False
    for _, row in weighted.iterrows():
        manual = raw[raw["profit_center"] == row["profit_center"]]
        expected_weighted = manual["sales_amt"].sum() / manual["sales_vol"].sum()
        assert row["weighted_price"] == pytest.approx(expected_weighted, rel=1e-9)

        if len(manual) > 1:
            checked_a_multi_row_group = True
            assert row["weighted_price"] != pytest.approx(row["naive_avg_price"], rel=1e-6), (
                f"{row['profit_center']}: 가중평균과 단순평균이 같으면 테스트 데이터로 부적합합니다."
            )

    assert checked_a_multi_row_group, "가중평균/단순평균 차이를 검증할 다중 세그먼트 그룹이 없습니다."


def test_negative_deficit_values_preserved(conn):
    """[음수/적자 처리]
    후판(2026-01, 당진)처럼 실제로 적자인 구간에서 SUM(op_profit) 집계가 부호를 잃지 않고
    음수로 정확히 반환되는지 검증한다.
    """
    raw = conn.execute(
        "SELECT op_profit FROM profit_center_master "
        "WHERE year_month = '2026-01' AND profit_center = '후판' AND plant = '당진'"
    ).fetchdf()
    assert (raw["op_profit"] < 0).any(), "테스트 전제 데이터에 적자 구간이 없습니다."

    agg = conn.execute(
        "SELECT SUM(op_profit) AS total_op_profit FROM profit_center_master "
        "WHERE year_month = '2026-01' AND profit_center = '후판' AND plant = '당진'"
    ).fetchdf().iloc[0]

    assert agg["total_op_profit"] == pytest.approx(raw["op_profit"].sum())
    assert agg["total_op_profit"] < 0


def test_mom_rolling_consistency(conn):
    """[시계열 롤링: MoM]
    전월(4월) 대비 당월(5월) 가중평균 스프레드 변화량이 SQL 집계와 수기 계산에서
    정확히 일치하는지 검증한다.
    """

    def weighted_spread(year_month: str, profit_center: str) -> float:
        df = conn.execute(
            "SELECT sales_vol, sales_amt, raw_material FROM profit_center_master "
            "WHERE year_month = ? AND profit_center = ?",
            [year_month, profit_center],
        ).fetchdf()
        weighted_price = df["sales_amt"].sum() / df["sales_vol"].sum()
        weighted_material = (df["raw_material"] * df["sales_vol"]).sum() / df["sales_vol"].sum()
        return weighted_price - weighted_material

    manual_diff = weighted_spread("2026-05", "열연") - weighted_spread("2026-04", "열연")

    sql_diff = conn.execute(
        "WITH monthly AS ("
        "  SELECT year_month, "
        "         SUM(sales_amt) * 1.0 / SUM(sales_vol) "
        "         - SUM(raw_material * sales_vol) * 1.0 / SUM(sales_vol) AS spread "
        "  FROM profit_center_master "
        "  WHERE year_month IN ('2026-04', '2026-05') AND profit_center = '열연' "
        "  GROUP BY year_month"
        ") "
        "SELECT "
        "  MAX(CASE WHEN year_month = '2026-05' THEN spread END) "
        "  - MAX(CASE WHEN year_month = '2026-04' THEN spread END) AS diff "
        "FROM monthly"
    ).fetchdf().iloc[0]["diff"]

    assert sql_diff == pytest.approx(manual_diff, rel=1e-9)


def test_qoq_rolling_consistency(conn):
    """[시계열 롤링: QoQ]
    quarter 파생 컬럼으로 집계한 값이 해당 분기에 속한 개별 월 데이터를
    직접 합산한 값과 정확히 일치하는지, 그리고 quarter-year_month 매핑에
    어긋나는 행이 없는지 검증한다.
    """
    q1_via_quarter = conn.execute(
        "SELECT SUM(op_profit) AS total FROM profit_center_master "
        "WHERE quarter = 'Q1' AND profit_center = '철근'"
    ).fetchdf().iloc[0]["total"]

    q1_via_months = conn.execute(
        "SELECT SUM(op_profit) AS total FROM profit_center_master "
        "WHERE year_month IN ('2026-01', '2026-02', '2026-03') AND profit_center = '철근'"
    ).fetchdf().iloc[0]["total"]

    assert q1_via_quarter == pytest.approx(q1_via_months)

    mismatched = conn.execute(
        "SELECT COUNT(*) AS n FROM profit_center_master "
        "WHERE (quarter = 'Q1' AND year_month NOT IN ('2026-01','2026-02','2026-03')) "
        "   OR (quarter = 'Q2' AND year_month NOT IN ('2026-04','2026-05','2026-06')) "
        "   OR (quarter = 'Q3' AND year_month NOT IN ('2026-07','2026-08','2026-09')) "
        "   OR (quarter = 'Q4' AND year_month NOT IN ('2026-10','2026-11','2026-12'))"
    ).fetchdf().iloc[0]["n"]

    assert mismatched == 0

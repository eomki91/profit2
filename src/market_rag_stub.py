"""향후 확장 슬롯: 주간 시황 PPT/PDF RAG 검색기.

현재는 문서 파서가 연결되지 않아 항상 빈 문자열을 반환하는 Mock 구현만 존재한다.
추후 PPT/PDF 파서를 구현할 때는 MarketContextRetriever를 상속한 새 클래스만 추가하면
sql_agent / app 쪽 변경 없이 즉시 가동된다.
"""

from abc import ABC, abstractmethod


class MarketContextRetriever(ABC):
    @abstractmethod
    def get_context(self, year_month: str, profit_center: str | None = None) -> str:
        ...


class MockMarketRetriever(MarketContextRetriever):
    def get_context(self, year_month: str, profit_center: str | None = None) -> str:
        # 향후 PPT/PDF 파서 연동 영역
        return ""


def get_market_context(year_month: str, profit_center: str | None = None) -> str:
    """analysis 서비스 계층에서 바로 호출 가능한 편의 함수."""
    return MockMarketRetriever().get_context(year_month, profit_center)

from models.market import MarketData
from services.openalgo_client import OpenAlgoService


def market_data_agent() -> MarketData:
    service = OpenAlgoService()
    data = service.get_market_data()
    return MarketData(**data)

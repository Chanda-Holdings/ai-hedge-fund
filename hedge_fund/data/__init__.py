"""v2 data pipeline — data provider protocol, provider clients, and response models."""

import os

from hedge_fund.data.cached import CachedDataClient
from hedge_fund.data.client import FDClient, FDClientError
from hedge_fund.data.fmp import FMPClient, FMPClientError
from hedge_fund.data.models import (
    CompanyFacts,
    CompanyNews,
    Earnings,
    EarningsData,
    EarningsRecord,
    Filing,
    FinancialMetrics,
    InsiderTrade,
    Price,
)
from hedge_fund.data.protocol import DataClient
from hedge_fund.paths import CACHE_DIR


def make_data_client(refresh: bool = False) -> CachedDataClient:
    """Build the configured provider behind the disk cache.

    HEDGE_FUND_DATA_SOURCE selects the provider: "financialdatasets" (the
    default) or "fmp". Each provider caches under its own directory — cache
    keys name only the method and params, so a shared directory would serve
    one provider's rows as the other's.
    """
    source = os.environ.get("HEDGE_FUND_DATA_SOURCE", "financialdatasets").lower()
    if source == "financialdatasets":
        return CachedDataClient(FDClient(), refresh=refresh)
    if source == "fmp":
        return CachedDataClient(FMPClient(), cache_dir=CACHE_DIR / "data-fmp",
                                refresh=refresh)
    raise ValueError(
        f"unknown HEDGE_FUND_DATA_SOURCE {source!r}; "
        "use 'financialdatasets' or 'fmp'"
    )


__all__ = [
    "CachedDataClient",
    "CompanyFacts",
    "CompanyNews",
    "DataClient",
    "Earnings",
    "EarningsData",
    "EarningsRecord",
    "FDClient",
    "FDClientError",
    "FMPClient",
    "FMPClientError",
    "Filing",
    "FinancialMetrics",
    "InsiderTrade",
    "Price",
    "make_data_client",
]

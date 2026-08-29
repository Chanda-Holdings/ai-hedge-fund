"""v2 data pipeline — data provider protocol, provider clients, and response models."""

import os

from hedge_fund.data.cached import CachedDataClient
from hedge_fund.data.client import CallMeter, FDClient, FDClientError, has_data_credential
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
    PriceSnapshot,
)
from hedge_fund.data.protocol import DataClient
from hedge_fund.data.screener import OrderBy, ScreenerFields, ScreenFilter, ScreenRow
from hedge_fund.paths import CACHE_DIR


def make_data_client(
    refresh: bool = False,
    live: bool = False,
    meter: CallMeter | None = None,
) -> CachedDataClient:
    """Build the configured provider behind the disk cache.

    HEDGE_FUND_DATA_SOURCE selects the provider: "financialdatasets" (the
    default) or "fmp". Each provider caches under its own directory — cache
    keys name only the method and params, so a shared directory would serve
    one provider's rows as the other's.

    *live* is CachedDataClient's: date-less "latest" answers go stale after
    ten minutes. *meter* counts the requests sent; only the Financial
    Datasets client reports to one.
    """
    source = os.environ.get("HEDGE_FUND_DATA_SOURCE", "financialdatasets").lower()
    if source == "financialdatasets":
        return CachedDataClient(FDClient(meter=meter), refresh=refresh, live=live)
    if source == "fmp":
        return CachedDataClient(FMPClient(), cache_dir=CACHE_DIR / "data-fmp",
                                refresh=refresh, live=live)
    raise ValueError(
        f"unknown HEDGE_FUND_DATA_SOURCE {source!r}; "
        "use 'financialdatasets' or 'fmp'"
    )


__all__ = [
    "CachedDataClient",
    "CallMeter",
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
    "OrderBy",
    "Price",
    "PriceSnapshot",
    "ScreenFilter",
    "ScreenRow",
    "ScreenerFields",
    "has_data_credential",
    "make_data_client",
]

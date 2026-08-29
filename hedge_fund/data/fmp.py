"""Financial Modeling Prep (FMP) data client — a second DataClient provider.

Same contract as FDClient (hedge_fund/data/client.py): empty means the data
genuinely does not exist; infrastructure failures raise. Select it with
HEDGE_FUND_DATA_SOURCE=fmp (see make_data_client in hedge_fund/data/__init__.py).

Point-in-time correctness: FMP's metrics endpoints key rows by fiscal period
end, which precedes public availability by weeks — using them directly would
leak the future into a backtest. So get_financial_metrics joins each metrics
row to its income statement's fillingDate and filters on THAT. A row whose
filing date is unknown is dropped: not provably public means not usable.

Two deliberate approximations, documented here once:
- Rows are quarterly, not trailing-twelve-month. Margins and ratios read the
  same either way; growth rates are computed year-over-year in this module
  (vs 4 quarters back) so seasonality does not masquerade as growth.
- Earnings events come from the earnings calendar (announcement date +
  actual vs estimated EPS). The announcement is reported as source_type
  "8-K" — the filing that carries it — and BEAT/MISS is derived by sign of
  the surprise, matching what PEAD consumes.

Each ticker's fundamentals history is fetched once and filtered per end_date
in memory (the PEADModel cache pattern): a weekly backtest re-asks with a new
end_date every tick, and the underlying filings change quarterly, not weekly.
"""

from __future__ import annotations

import os

import requests

from hedge_fund.data.models import (
    CompanyFacts,
    CompanyNews,
    Earnings,
    EarningsData,
    EarningsRecord,
    FinancialMetrics,
    InsiderTrade,
    Price,
)

_HISTORY_QUARTERS = 80  # 20 years — one fetch covers any sane backtest window


class FMPClientError(Exception):
    """An FMP request failed for infrastructure reasons (auth, rate limit,
    server error, network). Distinct from "no data exists" — that returns
    empty. A backtest must crash on this, not treat it as no-data."""

    def __init__(self, message: str, *, status_code: int | None = None, path: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.path = path


class FMPClient:
    """Financial Modeling Prep API client (v3 endpoints).

    Reads FMP_API_KEY from the environment. Usage mirrors FDClient::

        with FMPClient() as fmp:
            prices = fmp.get_prices("AAPL", "2024-01-01", "2024-12-31")
    """

    BASE_URL = "https://financialmodelingprep.com/api/v3"

    def __init__(self, api_key: str | None = None, timeout: float = 30.0) -> None:
        self._api_key = api_key or os.environ.get("FMP_API_KEY", "")
        self._timeout = timeout
        self._session = requests.Session()
        # (metrics rows joined+filed, earnings events) memoized per ticker.
        self._fundamentals: dict[str, list[dict]] = {}
        self._earnings: dict[str, list[EarningsRecord]] = {}

    def __enter__(self) -> FMPClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------

    def get_prices(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        interval: str = "day",
        interval_multiplier: int = 1,
    ) -> list[Price]:
        """Daily OHLC bars, ascending. Intraday intervals are not ported."""
        if interval != "day" or interval_multiplier != 1:
            raise ValueError(
                f"FMPClient only serves daily bars, not {interval_multiplier}x{interval}"
            )
        data = self._get(f"/historical-price-full/{ticker}",
                         {"from": start_date, "to": end_date})
        rows = data.get("historical", []) if isinstance(data, dict) else []
        bars = [
            Price(
                open=r["open"], close=r["close"], high=r["high"], low=r["low"],
                volume=int(r.get("volume") or 0), time=r["date"],
            )
            for r in rows
        ]
        return sorted(bars, key=lambda p: p.time)

    # ------------------------------------------------------------------
    # Financial metrics
    # ------------------------------------------------------------------

    def get_financial_metrics(
        self,
        ticker: str,
        end_date: str,
        period: str = "ttm",
        limit: int = 10,
    ) -> list[FinancialMetrics]:
        """Metrics rows PUBLIC as of *end_date*, newest first.

        *period* is accepted for protocol compatibility; rows are always
        quarterly (see the module docstring for why that is acceptable).
        """
        rows = self._fundamentals_history(ticker)
        filed = [r for r in rows if r["filing_date"] <= end_date]
        return [FinancialMetrics(**r) for r in filed[:limit]]

    def get_company_facts(self, ticker: str) -> CompanyFacts | None:
        data = self._get(f"/profile/{ticker}")
        if not data:
            return None
        p = data[0]
        return CompanyFacts(
            ticker=ticker,
            is_active=bool(p.get("isActivelyTrading", True)),
            name=p.get("companyName"),
            cik=p.get("cik"),
            sector=p.get("sector"),
            industry=p.get("industry"),
            exchange=p.get("exchangeShortName"),
        )

    def get_market_cap(self, ticker: str, end_date: str) -> float | None:
        metrics = self.get_financial_metrics(ticker, end_date, limit=1)
        return metrics[0].market_cap if metrics else None

    # ------------------------------------------------------------------
    # Earnings
    # ------------------------------------------------------------------

    def get_earnings_history(self, ticker: str, limit: int = 12) -> list[EarningsRecord]:
        if ticker not in self._earnings:
            self._earnings[ticker] = self._build_earnings(ticker)
        return self._earnings[ticker][:limit]

    # ------------------------------------------------------------------
    # Not ported — fail loud, never silently empty
    # ------------------------------------------------------------------

    def get_news(self, ticker, end_date, start_date=None, limit=1000) -> list[CompanyNews]:
        raise NotImplementedError("FMPClient does not serve news yet")

    def get_insider_trades(self, ticker, end_date, start_date=None, limit=1000) -> list[InsiderTrade]:
        raise NotImplementedError("FMPClient does not serve insider trades yet")

    def get_earnings(self, ticker: str) -> Earnings | None:
        raise NotImplementedError(
            "FMPClient does not serve single-period earnings; use get_earnings_history"
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None):
        url = f"{self.BASE_URL}{path}"
        try:
            resp = self._session.get(
                url, params={**(params or {}), "apikey": self._api_key},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise FMPClientError(f"request failed for {path}: {exc}", path=path) from exc
        if resp.status_code != 200:
            raise FMPClientError(
                f"FMP returned {resp.status_code} for {path}",
                status_code=resp.status_code, path=path,
            )
        data = resp.json()
        # FMP reports some failures (bad key, exhausted quota) as HTTP 200
        # with an error body — that is infrastructure, not no-data.
        if isinstance(data, dict) and "Error Message" in data:
            raise FMPClientError(f"FMP error for {path}: {data['Error Message']}", path=path)
        return data

    def _fundamentals_history(self, ticker: str) -> list[dict]:
        """Joined quarterly rows with filing dates, newest first, memoized."""
        if ticker in self._fundamentals:
            return self._fundamentals[ticker]

        q = {"period": "quarter", "limit": _HISTORY_QUARTERS}
        income = self._get(f"/income-statement/{ticker}", q)
        km = {r["date"]: r for r in self._get(f"/key-metrics/{ticker}", q)}
        ratios = {r["date"]: r for r in self._get(f"/ratios/{ticker}", q)}

        revenue = {r["date"]: r.get("revenue") for r in income}
        net_income = {r["date"]: r.get("netIncome") for r in income}
        eps = {r["date"]: r.get("eps") for r in income}
        period_ends = sorted(revenue, reverse=True)
        year_ago = {d: period_ends[i + 4] for i, d in enumerate(period_ends)
                    if i + 4 < len(period_ends)}

        rows: list[dict] = []
        for stmt in income:
            date = stmt["date"]
            filing = stmt.get("fillingDate")
            k, r = km.get(date), ratios.get(date)
            if not filing or k is None or r is None:
                continue
            prior = year_ago.get(date)
            rows.append({
                "ticker": ticker,
                "report_period": date,
                "period": "quarter",
                "currency": stmt.get("reportedCurrency"),
                "filing_date": filing[:10],
                "market_cap": k.get("marketCap"),
                "enterprise_value": k.get("enterpriseValue"),
                "price_to_earnings_ratio": k.get("peRatio"),
                "price_to_book_ratio": k.get("pbRatio"),
                "price_to_sales_ratio": k.get("priceToSalesRatio"),
                "enterprise_value_to_ebitda_ratio": k.get("enterpriseValueOverEBITDA"),
                "free_cash_flow_yield": k.get("freeCashFlowYield"),
                "peg_ratio": r.get("priceEarningsToGrowthRatio"),
                "gross_margin": r.get("grossProfitMargin"),
                "operating_margin": r.get("operatingProfitMargin"),
                "net_margin": r.get("netProfitMargin"),
                "return_on_equity": r.get("returnOnEquity"),
                "return_on_assets": r.get("returnOnAssets"),
                "return_on_invested_capital": k.get("roic"),
                "asset_turnover": r.get("assetTurnover"),
                "inventory_turnover": r.get("inventoryTurnover"),
                "receivables_turnover": r.get("receivablesTurnover"),
                "days_sales_outstanding": r.get("daysOfSalesOutstanding"),
                "operating_cycle": r.get("operatingCycle"),
                "current_ratio": r.get("currentRatio"),
                "quick_ratio": r.get("quickRatio"),
                "cash_ratio": r.get("cashRatio"),
                "debt_to_equity": k.get("debtToEquity"),
                "debt_to_assets": k.get("debtToAssets"),
                "interest_coverage": r.get("interestCoverage"),
                "revenue_growth": _yoy(revenue.get(date), revenue.get(prior)),
                "earnings_growth": _yoy(net_income.get(date), net_income.get(prior)),
                "earnings_per_share_growth": _yoy(eps.get(date), eps.get(prior)),
                "payout_ratio": k.get("payoutRatio"),
                "earnings_per_share": stmt.get("eps"),
                "book_value_per_share": k.get("bookValuePerShare"),
                "free_cash_flow_per_share": k.get("freeCashFlowPerShare"),
            })

        rows.sort(key=lambda r: r["filing_date"], reverse=True)
        self._fundamentals[ticker] = rows
        return rows

    def _build_earnings(self, ticker: str) -> list[EarningsRecord]:
        rows = self._get(f"/historical/earning_calendar/{ticker}",
                         {"limit": _HISTORY_QUARTERS})
        records: list[EarningsRecord] = []
        for r in rows:
            actual, estimate = r.get("eps"), r.get("epsEstimated")
            if actual is None or estimate is None:  # future or unestimated event
                continue
            eps_surprise = ("BEAT" if actual > estimate
                            else "MISS" if actual < estimate else "MEET")
            records.append(EarningsRecord(
                ticker=ticker,
                report_period=r["fiscalDateEnding"],
                source_type="8-K",
                filing_date=r["date"],
                quarterly=EarningsData(
                    earnings_per_share=actual,
                    estimated_earnings_per_share=estimate,
                    eps_surprise=eps_surprise,
                    revenue=r.get("revenue"),
                    estimated_revenue=r.get("revenueEstimated"),
                ),
            ))
        records.sort(key=lambda r: r.filing_date or "", reverse=True)
        return records


def _yoy(current, prior) -> float | None:
    if current is None or not prior:
        return None
    return current / prior - 1

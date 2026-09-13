"""Financial Modeling Prep (FMP) data client — a second DataClient provider.

Same contract as FDClient (hedge_fund/data/client.py): empty means the data
genuinely does not exist; infrastructure failures raise. Select it with
HEDGE_FUND_DATA_SOURCE=fmp (see make_data_client in hedge_fund/data/__init__.py).

Every request goes through fmpsdk, which retries a 429 ten times at 20 second
intervals before it raises. That backoff is why this module keeps no HTTP code
of its own.

Point-in-time correctness: FMP's metrics endpoints key rows by fiscal period
end, which precedes public availability by weeks — using them directly would
leak the future into a backtest. So get_financial_metrics joins each metrics
row to its income statement's filingDate and filters on THAT. A row whose
filing date is unknown is dropped: not provably public means not usable.

Three deliberate approximations, documented here once:
- Rows are quarterly, not trailing-twelve-month. Margins and ratios read the
  same either way; growth rates are computed year-over-year in this module
  (vs 4 quarters back) so seasonality does not masquerade as growth.
- The earnings endpoint gives an announcement date but no fiscal period end,
  so the period comes from the latest income statement that closed before the
  announcement. PEAD drops an event on filing-minus-period lag, so the period
  has to be the fiscal one and not the announcement date.
- Earnings events are reported as source_type "8-K" — the filing that carries
  the announcement — and BEAT/MISS comes from the sign of the surprise.

Each ticker's fundamentals history is fetched once and filtered per end_date
in memory (the PEADModel cache pattern): a weekly backtest re-asks with a new
end_date every tick, and the underlying filings change quarterly, not weekly.
"""

from __future__ import annotations

import os

import fmpsdk
import requests
from fmpsdk import calendar_module
from fmpsdk.exceptions import (
    InvalidAPIKeyException,
    InvalidExchangeCodeException,
    InvalidQueryParameterException,
    PremiumEndpointException,
    PremiumQueryParameterException,
    RateLimitExceededException,
)
from pydantic import ValidationError

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

_TYPED_ERRORS = (
    InvalidAPIKeyException,
    InvalidExchangeCodeException,
    InvalidQueryParameterException,
    PremiumEndpointException,
    PremiumQueryParameterException,
    RateLimitExceededException,
)


class FMPClientError(Exception):
    """An FMP request failed for infrastructure reasons (auth, rate limit,
    server error, network). Distinct from "no data exists" — that returns
    empty. A backtest must crash on this, not treat it as no-data."""

    def __init__(self, message: str, *, status_code: int | None = None, path: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.path = path


class FMPClient:
    """Financial Modeling Prep API client, backed by fmpsdk's stable endpoints.

    Reads FMP_API_KEY from the environment. Usage mirrors FDClient::

        with FMPClient() as fmp:
            prices = fmp.get_prices("AAPL", "2024-01-01", "2024-12-31")
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("FMP_API_KEY", "")
        # (joined metrics rows, fiscal period ends, earnings) memoized per ticker.
        self._fundamentals: dict[str, list[dict]] = {}
        self._period_ends: dict[str, list[str]] = {}
        self._earnings: dict[str, list[EarningsRecord]] = {}

    def __enter__(self) -> FMPClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        """No-op: fmpsdk owns its connections. Kept for the DataClient shape."""

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
        rows = self._call(
            fmpsdk.historical_price_eod,
            symbol=ticker,
            from_date=start_date,
            to_date=end_date,
        )
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
        rows = self._call(fmpsdk.company_profile, symbol=ticker)
        if not rows:
            return None
        p = rows[0]
        active = p.get("isActivelyTrading")
        return CompanyFacts(
            ticker=ticker,
            is_active=True if active is None else bool(active),
            name=p.get("companyName"),
            cik=p.get("cik"),
            sector=p.get("sector"),
            industry=p.get("industry"),
            # The stable profile leaves exchangeShortName null and names the
            # venue in `exchange`.
            exchange=p.get("exchangeShortName") or p.get("exchange"),
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

    def _call(self, fn, **params) -> list[dict]:
        """Run an fmpsdk endpoint and hand back plain rows.

        fmpsdk reports trouble three ways — a typed exception, a bare Response
        for a premium endpoint, or a dict carrying "Error Message" — and each
        one becomes FMPClientError so the fail-loud contract holds.
        """
        name = getattr(fn, "__name__", str(fn))
        try:
            response = fn(apikey=self._api_key, **params)
        except RateLimitExceededException as exc:
            raise FMPClientError(
                f"{name} rate limited (429) after fmpsdk exhausted its retries: {exc}",
                status_code=429, path=name,
            ) from exc
        except _TYPED_ERRORS as exc:
            raise FMPClientError(f"{name} failed: {exc}", path=name) from exc
        except ValidationError as exc:
            raise FMPClientError(
                f"{name} returned rows that do not match the fmpsdk model: {exc}",
                path=name,
            ) from exc
        except requests.RequestException as exc:
            raise FMPClientError(f"request failed for {name}: {exc}", path=name) from exc
        except Exception as exc:  # noqa: BLE001 - every path here is an HTTP fetch
            raise FMPClientError(f"{name} failed: {exc}", path=name) from exc

        status = getattr(response, "status_code", None)
        if status is not None:
            raise FMPClientError(
                f"FMP returned {status} for {name}", status_code=status, path=name,
            )
        if isinstance(response, dict) and "Error Message" in response:
            raise FMPClientError(
                f"FMP error for {name}: {response['Error Message']}", path=name,
            )
        return fmpsdk.to_dict_list(response)

    def _fundamentals_history(self, ticker: str) -> list[dict]:
        """Joined quarterly rows with filing dates, newest first, memoized."""
        if ticker in self._fundamentals:
            return self._fundamentals[ticker]

        q = {"period": "quarter", "limit": _HISTORY_QUARTERS}
        income = self._call(fmpsdk.income_statement, symbol=ticker, **q)
        km = {r["date"]: r for r in self._call(fmpsdk.key_metrics, symbol=ticker, **q)}
        ratios = {r["date"]: r for r in self._call(fmpsdk.financial_ratios, symbol=ticker, **q)}

        revenue = {r["date"]: r.get("revenue") for r in income}
        net_income = {r["date"]: r.get("netIncome") for r in income}
        eps = {r["date"]: r.get("eps") for r in income}
        roe = {d: v.get("returnOnEquity") for d, v in km.items()}
        fcf_per_share = {d: v.get("freeCashFlowPerShare") for d, v in ratios.items()}
        period_ends = sorted(revenue, reverse=True)
        year_ago = {d: period_ends[i + 4] for i, d in enumerate(period_ends)
                    if i + 4 < len(period_ends)}

        # FMP reports flows per quarter, but prices these rows against a
        # trailing-twelve-month P/E. A persona that multiplies the two reads a
        # price a quarter of the real one, so every flow is summed over the
        # four quarters ending at its period. A period without four quarters
        # behind it has no TTM figure, and says so rather than guessing.
        position = {d: i for i, d in enumerate(period_ends)}

        def _ttm(values, date):
            window = period_ends[position[date] : position[date] + 4]
            if len(window) < 4:
                return None
            parts = [values.get(d) for d in window]
            return None if any(p is None for p in parts) else sum(parts)
        # Earnings announcements join against the full tape, including periods
        # the join below drops for a missing metrics or ratios row.
        self._period_ends[ticker] = period_ends

        rows: list[dict] = []
        for stmt in income:
            date = stmt["date"]
            filing = stmt.get("filingDate")
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
                "price_to_earnings_ratio": r.get("priceToEarningsRatio"),
                "price_to_book_ratio": r.get("priceToBookRatio"),
                "price_to_sales_ratio": r.get("priceToSalesRatio"),
                "enterprise_value_to_ebitda_ratio": k.get("evToEBITDA"),
                "free_cash_flow_yield": k.get("freeCashFlowYield"),
                "peg_ratio": r.get("priceToEarningsGrowthRatio"),
                "gross_margin": r.get("grossProfitMargin"),
                "operating_margin": r.get("operatingProfitMargin"),
                "net_margin": r.get("netProfitMargin"),
                "return_on_equity": _ttm(roe, date),
                "return_on_assets": k.get("returnOnAssets"),
                "return_on_invested_capital": k.get("returnOnInvestedCapital"),
                "asset_turnover": r.get("assetTurnover"),
                "inventory_turnover": r.get("inventoryTurnover"),
                "receivables_turnover": r.get("receivablesTurnover"),
                "days_sales_outstanding": k.get("daysOfSalesOutstanding"),
                "operating_cycle": k.get("operatingCycle"),
                "current_ratio": r.get("currentRatio"),
                "quick_ratio": r.get("quickRatio"),
                "cash_ratio": r.get("cashRatio"),
                "debt_to_equity": r.get("debtToEquityRatio"),
                "debt_to_assets": r.get("debtToAssetsRatio"),
                "interest_coverage": r.get("interestCoverageRatio"),
                "revenue_growth": _yoy(revenue.get(date), revenue.get(prior)),
                "earnings_growth": _yoy(net_income.get(date), net_income.get(prior)),
                "earnings_per_share_growth": _yoy(eps.get(date), eps.get(prior)),
                "payout_ratio": r.get("dividendPayoutRatio"),
                "earnings_per_share": _ttm(eps, date),
                "book_value_per_share": r.get("bookValuePerShare"),
                "free_cash_flow_per_share": _ttm(fcf_per_share, date),
            })

        rows.sort(key=lambda r: r["filing_date"], reverse=True)
        self._fundamentals[ticker] = rows
        return rows

    def _build_earnings(self, ticker: str) -> list[EarningsRecord]:
        rows = self._call(
            calendar_module.earnings, symbol=ticker, limit=str(_HISTORY_QUARTERS),
        )
        self._fundamentals_history(ticker)  # fills _period_ends
        period_ends = self._period_ends.get(ticker, [])

        records: list[EarningsRecord] = []
        for r in rows:
            actual, estimate = r.get("epsActual"), r.get("epsEstimated")
            if actual is None or estimate is None:  # future or unestimated event
                continue
            announced = r["date"]
            period = next((p for p in period_ends if p < announced), None)
            if period is None:  # no closed fiscal period — PEAD cannot age it
                continue
            eps_surprise = ("BEAT" if actual > estimate
                            else "MISS" if actual < estimate else "MEET")
            records.append(EarningsRecord(
                ticker=ticker,
                report_period=period,
                source_type="8-K",
                filing_date=announced,
                quarterly=EarningsData(
                    earnings_per_share=actual,
                    estimated_earnings_per_share=estimate,
                    eps_surprise=eps_surprise,
                    revenue=r.get("revenueActual"),
                    estimated_revenue=r.get("revenueEstimated"),
                ),
            ))
        records.sort(key=lambda r: r.filing_date or "", reverse=True)
        return records


def _yoy(current, prior) -> float | None:
    if current is None or not prior:
        return None
    return current / prior - 1

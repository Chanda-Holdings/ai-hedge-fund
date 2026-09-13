"""FMPClient tests — canned fmpsdk payloads, no network."""

import pytest
import requests
from fmpsdk.exceptions import InvalidAPIKeyException, RateLimitExceededException

from hedge_fund.data import make_data_client
from hedge_fund.data.fmp import FMPClient, FMPClientError


def make_client(payloads: dict) -> FMPClient:
    """An FMPClient whose _call serves canned rows by endpoint name."""
    client = FMPClient(api_key="test")

    def fake_call(fn, **params):
        name = getattr(fn, "__name__", str(fn))
        if name not in payloads:
            raise AssertionError(f"unexpected endpoint {name}")
        return payloads[name]

    client._call = fake_call
    return client


def _income(date, filing, revenue, eps, net_income=10.0):
    return {"date": date, "filingDate": filing, "reportedCurrency": "USD",
            "revenue": revenue, "eps": eps, "netIncome": net_income}


def _km(date, **extra):
    return {"date": date, "marketCap": 1e12, "returnOnEquity": 0.4,
            "returnOnAssets": 0.2, "evToEBITDA": 18.0,
            "returnOnInvestedCapital": 0.3, **extra}


def _ratios(date, **extra):
    return {"date": date, "grossProfitMargin": 0.5, "operatingProfitMargin": 0.3,
            "netProfitMargin": 0.25, "currentRatio": 1.0,
            "priceToEarningsRatio": 25.0, "debtToEquityRatio": 1.5,
            "bookValuePerShare": 7.0, "freeCashFlowPerShare": 2.0,
            "interestCoverageRatio": 12.0, **extra}


def test_prices_map_and_sort_ascending():
    client = make_client({"historical_price_eod": [
        {"date": "2026-08-27", "open": 310.0, "high": 315.0, "low": 309.0,
         "close": 314.0, "volume": 1000},
        {"date": "2026-08-26", "open": 305.0, "high": 311.0, "low": 304.0,
         "close": 310.0, "volume": 900},
    ]})

    bars = client.get_prices("AAPL", "2026-08-01", "2026-08-27")

    assert [b.time for b in bars] == ["2026-08-26", "2026-08-27"]
    assert bars[1].close == 314.0


def test_prices_reject_intraday():
    client = make_client({})
    with pytest.raises(ValueError):
        client.get_prices("AAPL", "2026-08-01", "2026-08-27", interval="minute")


def test_metrics_are_point_in_time():
    dates = ["2026-06-27", "2026-03-28", "2025-12-27", "2025-09-27"]
    filings = ["2026-07-31", "2026-05-02", "2026-01-31", "2025-11-01"]
    client = make_client({
        "income_statement": [_income(d, f, 100.0, 2.0)
                             for d, f in zip(dates, filings)],
        "key_metrics": [_km(d) for d in dates],
        "financial_ratios": [_ratios(d) for d in dates],
    })

    metrics = client.get_financial_metrics("AAPL", "2026-06-01")

    # The Q2 row was filed 2026-07-31 — after end_date — and must not appear.
    assert [m.report_period for m in metrics] == dates[1:]
    assert metrics[0].filing_date == "2026-05-02"
    assert metrics[0].market_cap == 1e12
    assert metrics[0].gross_margin == 0.5


_QUARTERS = ["2026-03-28", "2025-12-28", "2025-09-28", "2025-06-28"]


def test_metrics_read_each_field_from_its_stable_endpoint():
    """Stable split these across key_metrics and ratios; v3 grouped them differently."""
    client = make_client({
        "income_statement": [_income(d, "2026-05-02", 100.0, 2.0) for d in _QUARTERS],
        "key_metrics": [_km(d) for d in _QUARTERS],
        "financial_ratios": [_ratios(d) for d in _QUARTERS],
    })

    m = client.get_financial_metrics("AAPL", "2026-12-31")[0]

    assert m.return_on_invested_capital == 0.3          # key_metrics, was roic
    assert m.enterprise_value_to_ebitda_ratio == 18.0   # key_metrics, renamed
    assert m.price_to_earnings_ratio == 25.0            # ratios, moved
    assert m.debt_to_equity == 1.5                      # ratios, renamed
    assert m.interest_coverage == 12.0                  # ratios, renamed


def test_flows_are_summed_over_four_quarters():
    """The rows are priced against a TTM P/E, so quarterly flows would make
    price read a quarter of itself."""
    client = make_client({
        "income_statement": [_income(d, "2026-05-02", 100.0, 2.0) for d in _QUARTERS],
        "key_metrics": [_km(d) for d in _QUARTERS],
        "financial_ratios": [_ratios(d) for d in _QUARTERS],
    })

    rows = client.get_financial_metrics("AAPL", "2026-12-31")

    assert rows[0].earnings_per_share == pytest.approx(8.0)        # 4 x 2.0
    assert rows[0].return_on_equity == pytest.approx(1.6)          # 4 x 0.4
    assert rows[0].free_cash_flow_per_share == pytest.approx(8.0)  # 4 x 2.0


def test_flows_are_none_without_four_quarters():
    """Not enough history is unknown, not zero — say so rather than guess."""
    client = make_client({
        "income_statement": [_income(d, "2026-05-02", 100.0, 2.0) for d in _QUARTERS[:3]],
        "key_metrics": [_km(d) for d in _QUARTERS[:3]],
        "financial_ratios": [_ratios(d) for d in _QUARTERS[:3]],
    })

    rows = client.get_financial_metrics("AAPL", "2026-12-31")

    assert rows[0].earnings_per_share is None
    assert rows[0].return_on_equity is None


def test_metrics_growth_is_year_over_year():
    dates = [f"2026-0{q}-01" for q in (6, 3)] + \
            [f"2025-{m:02d}-01" for m in (12, 9, 6)]
    client = make_client({
        "income_statement": [
            _income(dates[0], "2026-07-01", revenue=120.0, eps=2.4),
            _income(dates[1], "2026-04-01", revenue=100.0, eps=2.0),
            _income(dates[2], "2026-01-01", revenue=100.0, eps=2.0),
            _income(dates[3], "2025-10-01", revenue=100.0, eps=2.0),
            _income(dates[4], "2025-07-01", revenue=100.0, eps=2.0),
        ],
        "key_metrics": [_km(d) for d in dates],
        "financial_ratios": [_ratios(d) for d in dates],
    })

    metrics = client.get_financial_metrics("AAPL", "2026-12-31")

    assert metrics[0].revenue_growth == pytest.approx(0.2)  # 120 vs 100 a year ago
    assert metrics[1].revenue_growth is None  # no row 4 quarters back


def _earnings_client():
    dates = ["2026-06-27", "2026-03-28"]
    return make_client({
        "earnings": [
            {"date": "2026-10-29", "epsActual": None, "epsEstimated": 1.98},
            {"date": "2026-07-30", "epsActual": 2.02, "epsEstimated": 1.89},
            {"date": "2026-04-29", "epsActual": 1.80, "epsEstimated": 1.95},
        ],
        "income_statement": [_income(dates[0], "2026-07-31", 100.0, 2.0),
                             _income(dates[1], "2026-05-02", 100.0, 2.0)],
        "key_metrics": [_km(d) for d in dates],
        "financial_ratios": [_ratios(d) for d in dates],
    })


def test_earnings_history_derives_surprise_and_skips_future():
    records = _earnings_client().get_earnings_history("AAPL")

    assert len(records) == 2  # the unreported future event is dropped
    assert records[0].filing_date == "2026-07-30"
    assert records[0].quarterly.eps_surprise == "BEAT"
    assert records[1].quarterly.eps_surprise == "MISS"


def test_earnings_report_period_is_the_fiscal_period_not_the_announcement():
    """PEAD ages an event by filing_date - report_period, so the period has to
    be the quarter that closed before the announcement."""
    records = _earnings_client().get_earnings_history("AAPL")

    assert records[0].report_period == "2026-06-27"  # announced 2026-07-30
    assert records[1].report_period == "2026-03-28"  # announced 2026-04-29
    assert records[0].report_period != records[0].filing_date


def test_company_facts_map_profile():
    client = make_client({"company_profile": [
        {"companyName": "Apple Inc.", "sector": "Technology",
         "industry": "Consumer Electronics", "cik": "0000320193",
         "exchangeShortName": "NASDAQ", "isActivelyTrading": True},
    ]})

    facts = client.get_company_facts("AAPL")

    assert facts.name == "Apple Inc."
    assert facts.sector == "Technology"
    assert facts.industry == "Consumer Electronics"
    assert facts.exchange == "NASDAQ"


def test_company_facts_fall_back_to_exchange_and_stay_active():
    """Stable leaves exchangeShortName null and may report no trading flag."""
    client = make_client({"company_profile": [
        {"companyName": "McDonald's Corporation", "exchangeShortName": None,
         "exchange": "NYSE", "isActivelyTrading": None},
    ]})

    facts = client.get_company_facts("MCD")

    assert facts.exchange == "NYSE"
    assert facts.is_active is True


def test_rate_limit_surfaces_as_client_error():
    client = FMPClient(api_key="test")

    def income_statement(apikey, **params):
        raise RateLimitExceededException("Rate limit exceeded")

    with pytest.raises(FMPClientError) as exc_info:
        client._call(income_statement)
    assert exc_info.value.status_code == 429


@pytest.mark.parametrize("error", [
    InvalidAPIKeyException("Invalid API KEY"),
    requests.ConnectionError("dns failure"),
    Exception("Resource not found"),
])
def test_infrastructure_failures_raise_client_error(error):
    client = FMPClient(api_key="test")

    def company_profile(apikey, **params):
        raise error

    with pytest.raises(FMPClientError):
        client._call(company_profile)


def test_premium_response_object_raises():
    client = FMPClient(api_key="test")
    response = requests.Response()
    response.status_code = 402

    def key_metrics(apikey, **params):
        return response

    with pytest.raises(FMPClientError) as exc_info:
        client._call(key_metrics)
    assert exc_info.value.status_code == 402


def test_unported_methods_fail_loud():
    client = make_client({})
    with pytest.raises(NotImplementedError):
        client.get_news("AAPL", "2026-01-01")
    with pytest.raises(NotImplementedError):
        client.get_insider_trades("AAPL", "2026-01-01")
    with pytest.raises(NotImplementedError):
        client.get_earnings("AAPL")


def test_make_data_client_routes_by_env(monkeypatch):
    monkeypatch.setenv("HEDGE_FUND_DATA_SOURCE", "fmp")
    assert isinstance(make_data_client()._client, FMPClient)

    monkeypatch.setenv("HEDGE_FUND_DATA_SOURCE", "nope")
    with pytest.raises(ValueError):
        make_data_client()

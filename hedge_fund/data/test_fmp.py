"""FMPClient tests — canned API payloads, no network."""

import pytest

from hedge_fund.data import make_data_client
from hedge_fund.data.fmp import FMPClient


def make_client(payloads: dict) -> FMPClient:
    """An FMPClient whose _get serves canned payloads by path prefix."""
    client = FMPClient(api_key="test")

    def fake_get(path, params=None):
        for prefix, payload in payloads.items():
            if path.startswith(prefix):
                return payload
        raise AssertionError(f"unexpected path {path}")

    client._get = fake_get
    return client


def _income(date, filing, revenue, eps, net_income=10.0):
    return {"date": date, "fillingDate": filing, "reportedCurrency": "USD",
            "revenue": revenue, "eps": eps, "netIncome": net_income}


def _km(date, **extra):
    return {"date": date, "marketCap": 1e12, "peRatio": 25.0, "roe": 0.4,
            "debtToEquity": 1.5, "bookValuePerShare": 7.0,
            "freeCashFlowPerShare": 2.0, **extra}


def _ratios(date):
    return {"date": date, "grossProfitMargin": 0.5, "operatingProfitMargin": 0.3,
            "netProfitMargin": 0.25, "currentRatio": 1.0, "returnOnEquity": 0.4}


def test_prices_map_and_sort_ascending():
    client = make_client({"/historical-price-full/AAPL": {"symbol": "AAPL", "historical": [
        {"date": "2026-08-27", "open": 310.0, "high": 315.0, "low": 309.0,
         "close": 314.0, "volume": 1000},
        {"date": "2026-08-26", "open": 305.0, "high": 311.0, "low": 304.0,
         "close": 310.0, "volume": 900},
    ]}})

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
        "/income-statement/": [_income(d, f, 100.0, 2.0)
                               for d, f in zip(dates, filings)],
        "/key-metrics/": [_km(d) for d in dates],
        "/ratios/": [_ratios(d) for d in dates],
    })

    metrics = client.get_financial_metrics("AAPL", "2026-06-01")

    # The Q2 row was filed 2026-07-31 — after end_date — and must not appear.
    assert [m.report_period for m in metrics] == dates[1:]
    assert metrics[0].filing_date == "2026-05-02"
    assert metrics[0].market_cap == 1e12
    assert metrics[0].gross_margin == 0.5


def test_metrics_growth_is_year_over_year():
    dates = [f"2026-0{q}-01" for q in (6, 3)] + \
            [f"2025-{m:02d}-01" for m in (12, 9, 6)]
    client = make_client({
        "/income-statement/": [
            _income(dates[0], "2026-07-01", revenue=120.0, eps=2.4),
            _income(dates[1], "2026-04-01", revenue=100.0, eps=2.0),
            _income(dates[2], "2026-01-01", revenue=100.0, eps=2.0),
            _income(dates[3], "2025-10-01", revenue=100.0, eps=2.0),
            _income(dates[4], "2025-07-01", revenue=100.0, eps=2.0),
        ],
        "/key-metrics/": [_km(d) for d in dates],
        "/ratios/": [_ratios(d) for d in dates],
    })

    metrics = client.get_financial_metrics("AAPL", "2026-12-31")

    assert metrics[0].revenue_growth == pytest.approx(0.2)  # 120 vs 100 a year ago
    assert metrics[1].revenue_growth is None  # no row 4 quarters back


def test_earnings_history_derives_surprise_and_skips_future():
    client = make_client({"/historical/earning_calendar/AAPL": [
        {"date": "2026-10-29", "eps": None, "epsEstimated": 1.98,
         "fiscalDateEnding": "2026-09-27"},
        {"date": "2026-07-30", "eps": 2.02, "epsEstimated": 1.89,
         "fiscalDateEnding": "2026-06-27"},
        {"date": "2026-04-29", "eps": 1.80, "epsEstimated": 1.95,
         "fiscalDateEnding": "2026-03-28"},
    ]})

    records = client.get_earnings_history("AAPL")

    assert len(records) == 2  # the unreported future event is dropped
    assert records[0].filing_date == "2026-07-30"
    assert records[0].report_period == "2026-06-27"
    assert records[0].quarterly.eps_surprise == "BEAT"
    assert records[1].quarterly.eps_surprise == "MISS"


def test_company_facts_map_profile():
    client = make_client({"/profile/AAPL": [
        {"companyName": "Apple Inc.", "sector": "Technology",
         "industry": "Consumer Electronics", "cik": "0000320193",
         "exchangeShortName": "NASDAQ", "isActivelyTrading": True},
    ]})

    facts = client.get_company_facts("AAPL")

    assert facts.name == "Apple Inc."
    assert facts.sector == "Technology"
    assert facts.industry == "Consumer Electronics"


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

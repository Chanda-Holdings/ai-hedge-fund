import datetime
import os
import pandas as pd
import requests
from FinancialModelingPrep import FMP
from dotenv import load_dotenv
from src.data.cache import get_cache
from src.data.models import (
    CompanyNews,
    FinancialMetrics,
    Price,
    PriceResponse,
    LineItem,
    LineItemResponse,
    InsiderTrade,
)
import sys

load_dotenv()

# Global cache instance
_cache = get_cache()
fmp = FMP(os.environ.get("FINANCIAL_MODELING_PREP_API_KEY"))


def get_prices(ticker: str, start_date: str, end_date: str) -> list[Price]:
    """Fetch price data from cache or API."""
    # Check cache first
    if cached_data := _cache.get_prices(ticker):
        print(cached_data)
        # Filter cached data by date range and convert to Price objects
        filtered_data = [Price(**price) for price in cached_data if start_date <= price["time"] <= end_date]
        if filtered_data:
            return filtered_data
    
    start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    end_date = datetime.datetime.strptime(end_date, "%Y-%m-%d")
    response = fmp.historical_prices_raw(ticker, start_date, end_date)

    # Parse response with Pydantic model
    # Convert the response to a list of Price objects
    prices = []
    for price_data in response:
        price = Price(
            open=price_data['open'],
            close=price_data['close'],
            high=price_data['high'],
            low=price_data['low'],
            volume=price_data['volume'],
            time=price_data['date']
        )
        prices.append(price)
    
    # Create PriceResponse object
    price_response = PriceResponse(ticker=ticker, prices=prices)
    prices = price_response.prices

    if not prices:
        return []

    # Cache the results as dicts
    _cache.set_prices(ticker, [p.model_dump() for p in prices])
    return prices


def get_financial_metrics(
    ticker: str,
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[FinancialMetrics]:
    """Fetch financial metrics from cache or API."""
    # Check cache first
    if cached_data := _cache.get_financial_metrics(ticker):
        # Filter cached data by date and limit
        filtered_data = [FinancialMetrics(**metric) for metric in cached_data if metric["report_period"] <= end_date]
        filtered_data.sort(key=lambda x: x.report_period, reverse=True)
        if filtered_data:
            return filtered_data[:limit]
    
    financial_ratios = fmp.financial_ratios(ticker, "quarter")
    income_statement_growth = fmp.income_statement_growth(ticker, "quarter")
    enterprise_values = fmp.enterprise_values(ticker, "quarter")
    income_statements = fmp.income_statement(ticker, "quarter")
    # Create a dictionary to store merged data by date
    merged_data = {}
    
    # First add all financial ratios data
    for ratio in financial_ratios:
        date = ratio.get("date")
        if date:
            merged_data[date] = ratio.copy()
    
    # Then merge in income statement growth data
    for growth in income_statement_growth:
        date = growth.get("date")
        if date in merged_data:
            # Update the existing entry with growth data
            merged_data[date].update(growth)

    # Then merge in enterprise values data
    for value in enterprise_values:
        date = value.get("date")
        if date in merged_data:
            # Update the existing entry with enterprise values data
            merged_data[date].update(value)

    # Then merge in income statements data
    for statement in income_statements:
        date = statement.get("date")
        if date in merged_data:
            # Update the existing entry with income statement data
            merged_data[date].update(statement)

    # Convert back to list
    merged_data = list(merged_data.values())
    df = pd.DataFrame(merged_data)
    df = df.head(limit)
    df = df.sort_values(by='date', ascending=False)
    df = df[df['date'] <= end_date]
    filtered_merged_data = df.to_dict(orient='records')

    financial_metrics = []
    for metric in filtered_merged_data:
        # Safely calculate EV/EBITDA
        enterprise_value = metric.get("enterpriseValue")
        ebitda = metric.get("ebitda")
        ev_to_ebitda_ratio = None
        if enterprise_value is not None and ebitda is not None and ebitda != 0:
            try:
                # Ensure values are numeric before division
                ev_to_ebitda_ratio = float(enterprise_value) / float(ebitda)
            except (ValueError, TypeError):
                ev_to_ebitda_ratio = None # Handle case where values are not numeric

        financial_metrics.append(
            FinancialMetrics(
                ticker=metric.get("symbol", ticker),
                report_period=metric.get("date"),
                period=metric.get("period"),
                currency=metric.get("reportedCurrency"),
                market_cap=metric.get("marketCapitalization"),
                enterprise_value=metric.get("enterpriseValue"),
                price_to_earnings_ratio=metric.get("priceToEarningsRatio"),
                price_to_book_ratio=metric.get("priceToBookRatio"),
                price_to_sales_ratio=metric.get("priceToSalesRatio"),
                enterprise_value_to_ebitda_ratio=ev_to_ebitda_ratio,
                enterprise_value_to_revenue_ratio=None,
                free_cash_flow_yield=None,
                peg_ratio=metric.get("priceToEarningsGrowthRatio"),
                gross_margin=metric.get("grossProfitMargin"),
                operating_margin=metric.get("operatingProfitMargin"),
                net_margin=metric.get("netProfitMargin"),
                return_on_equity=None,
                return_on_assets=None,
                return_on_invested_capital=None,
                asset_turnover=metric.get("assetTurnover"),
                inventory_turnover=metric.get("inventoryTurnover"),
                receivables_turnover=metric.get("receivablesTurnover"),
                days_sales_outstanding=None,
                operating_cycle=None,
                working_capital_turnover=metric.get("workingCapitalTurnoverRatio"),
                current_ratio=metric.get("currentRatio"),
                quick_ratio=metric.get("quickRatio"),
                cash_ratio=metric.get("cashRatio"),
                operating_cash_flow_ratio=metric.get("operatingCashFlowRatio"),
                debt_to_equity=metric.get("debtToEquityRatio"),
                debt_to_assets=metric.get("debtToAssetsRatio"),
                interest_coverage=metric.get("interestCoverageRatio"),
                revenue_growth=metric.get("growthRevenue"),
                earnings_growth=metric.get("growthNetIncome"),
                book_value_growth=None,
                earnings_per_share_growth=metric.get("growthEPSDiluted"),
                free_cash_flow_growth=None,
                operating_income_growth=metric.get("growthOperatingIncome"),
                ebitda_growth=metric.get("growthEBITDA"),
                payout_ratio=metric.get("dividendPayoutRatio"),
                earnings_per_share=metric.get("epsDiluted") or metric.get("eps") or metric.get("netIncomePerShare"),
                book_value_per_share=metric.get("bookValuePerShare"),
                free_cash_flow_per_share=metric.get("freeCashFlowPerShare"),
            )
        )

    if not financial_metrics:
        return []

    # Cache the results as dicts
    _cache.set_financial_metrics(ticker, [m.model_dump() for m in financial_metrics])
    return financial_metrics


def search_line_items(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[LineItem]:
    """Fetch line items from API."""
    # If not in cache or insufficient data, fetch from API
    headers = {}
    if api_key := os.environ.get("FINANCIAL_DATASETS_API_KEY"):
        headers["X-API-KEY"] = api_key

    url = "https://api.financialdatasets.ai/financials/search/line-items"

    body = {
        "tickers": [ticker],
        "line_items": line_items,
        "end_date": end_date,
        "period": period,
        "limit": limit,
    }
    response = requests.post(url, headers=headers, json=body)
    if response.status_code != 200:
        raise Exception(f"Error fetching data: {ticker} - {response.status_code} - {response.text}")
    data = response.json()
    response_model = LineItemResponse(**data)
    search_results = response_model.search_results
    if not search_results:
        return []

    # Cache the results
    return search_results[:limit]


def get_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[InsiderTrade]:
    """Fetch insider trades from cache or API."""
    # Check cache first
    if cached_data := _cache.get_insider_trades(ticker):
        # Filter cached data by date range
        filtered_data = [InsiderTrade(**trade) for trade in cached_data 
                        if (start_date is None or (trade.get("transaction_date") or trade["filing_date"]) >= start_date)
                        and (trade.get("transaction_date") or trade["filing_date"]) <= end_date]
        filtered_data.sort(key=lambda x: x.transaction_date or x.filing_date, reverse=True)
        if filtered_data:
            return filtered_data


    insider_trades = fmp.insider_trading(ticker)
    df = pd.DataFrame(insider_trades)
    df = df.sort_values(by='transactionDate', ascending=False)
    df = df[df['transactionDate'] >= start_date]
    df = df[df['transactionDate'] <= end_date]
    
    all_trades = []
    for index, row in df.iterrows():
        all_trades.append(InsiderTrade(
            ticker=ticker,
            issuer=None,
            name=row['reportingName'],
            title=row['typeOfOwner'],
            is_board_director=row['typeOfOwner'] == 'director',
            transaction_date=row['transactionDate'],
            transaction_shares=row['securitiesTransacted'],
            transaction_price_per_share=row['price'],
            transaction_value=row['securitiesTransacted'] * row['price'],
            shares_owned_before_transaction=row['securitiesOwned'],
            shares_owned_after_transaction=row['securitiesOwned'] - row['securitiesTransacted'] if row['acquisitionOrDisposition'] == "D" else row['securitiesOwned'] + row['securitiesTransacted'],
            security_title=row['securityName'],
            filing_date=row['filingDate'],
        ))

    if not all_trades:
        return []
    
    # Cache the results
    _cache.set_insider_trades(ticker, [trade.model_dump() for trade in all_trades])
    return all_trades


def get_company_news(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[CompanyNews]:
    """Fetch company news from cache or API."""
    # Check cache first
    if cached_data := _cache.get_company_news(ticker):
        # Filter cached data by date range
        filtered_data = [CompanyNews(**news) for news in cached_data 
                        if (start_date is None or news["date"] >= start_date)
                        and news["date"] <= end_date]
        filtered_data.sort(key=lambda x: x.date, reverse=True)
        if filtered_data:
            return filtered_data
    

    start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    end_date = datetime.datetime.strptime(end_date, "%Y-%m-%d")
    news = fmp.company_news([ticker], start_date, end_date)
    
    company_news = []
    for item in news:
        company_news.append(CompanyNews(
            ticker=ticker,
            title=item.get('title', ''),
            author=item.get('publisher', ''),
            source=item.get('site', ''),
            date=item.get('publishedDate', ''),
            url=item.get('url', ''),
            sentiment=None
        ))
    # print(company_news)
    if not company_news:
        return []

    # Cache the results
    _cache.set_company_news(ticker, [news.model_dump() for news in company_news])
    return company_news


def get_market_cap(
    ticker: str,
    end_date: str,
) -> float | None:
    """Fetch market cap from the API."""
    # Check if end_date is today
    _from = datetime.datetime.strptime(end_date, "%Y-%m-%d") - datetime.timedelta(days=10)
    to = datetime.datetime.strptime(end_date, "%Y-%m-%d") + datetime.timedelta(days=10)
    response = fmp.historical_market_capitalization(ticker, _from, to)
    df = pd.DataFrame(response)
    # Convert date column to datetime and set as index
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    
    # Create a complete date range from min to max date
    date_range = pd.date_range(start=df.index.min(), end=df.index.max(), freq='D')
    
    # Reindex the dataframe to include all dates
    df = df.reindex(date_range)
    df.ffill(inplace=True)
    df.bfill(inplace=True)
    market_cap = df.loc[end_date, 'marketCap']

    return market_cap


def prices_to_df(prices: list[Price]) -> pd.DataFrame:
    """Convert prices to a DataFrame."""
    df = pd.DataFrame([p.model_dump() for p in prices])
    df["Date"] = pd.to_datetime(df["time"])
    df.set_index("Date", inplace=True)
    numeric_cols = ["open", "close", "high", "low", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.sort_index(inplace=True)
    return df


# Update the get_price_data function to use the new functions
def get_price_data(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    prices = get_prices(ticker, start_date, end_date)
    return prices_to_df(prices)



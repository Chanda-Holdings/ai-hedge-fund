import fmpsdk
import enum
from tqdm import tqdm
from datetime import datetime

class FMP:
    def __init__(self, api_key):
        self.api_key = api_key

    @staticmethod
    def handle_request(func, args):
        resp = func(**args)
        return resp

    def percent_ema_delta(self, symbol, _from, period):
        prices = self.historical_prices(symbol, _from)
        prices = {k: v["close"] for k, v in prices.items()}
        try:
            ema = self.calculate_ema(sorted(prices.items()), period)
            p_diff = {}
            for dt in ema.keys():
                p_diff[dt] = (prices[dt] - ema[dt]) / ema[dt]
            return p_diff
        except Exception as e:
            print("Exception 435", e)
            return None


    def historical_market_capitalization(self, symbol, _from, to):
        args = {"apikey": self.api_key, "symbol": symbol, "from_date": _from.strftime("%Y-%m-%d"),
                "to_date": to.strftime("%Y-%m-%d")}
        func = fmpsdk.historical_market_capitalization
        market_caps = self.handle_request(func, args)
        return market_caps

    def historical_prices_raw(self, symbol, _from, to):
        args = {"apikey": self.api_key, "symbol": symbol, "from_date": _from.strftime("%Y-%m-%d"), "to_date": to.strftime("%Y-%m-%d")}
        func = fmpsdk.historical_price_full
        prices = self.handle_request(func, args)
        return prices

    def insider_trading(self, symbol):
        args = {"apikey": self.api_key, "symbol": symbol, "limit": 1000}
        iterable_args = {"func": fmpsdk.insider_trading, "args": args}
        func = fmpsdk.iterate_over_pages
        return self.handle_request(func, iterable_args)
    
    def company_news(self, symbol, _from=datetime(2024, 1, 1), to=datetime.now()):
        args = {"apikey": self.api_key, "symbols": symbol, "from_date": _from.strftime("%Y-%m-%d"), "to_date": to.strftime("%Y-%m-%d"), "limit": 250}
        iterable_args = {"func": fmpsdk.company_news, "args": args}
        func = fmpsdk.iterate_over_pages
        return self.handle_request(func, iterable_args)
    
    def financial_ratios(self, symbol, period="annual"):
        args = {"apikey": self.api_key, "symbol": symbol, "period": period, "limit": 1000}
        func = fmpsdk.financial_ratios
        return self.handle_request(func, args)
    
    def income_statement_growth(self, symbol, period="annual"):
        args = {"apikey": self.api_key, "symbol": symbol, "period": period, "limit": 1000}
        func = fmpsdk.income_statement_growth
        return self.handle_request(func, args)
    
    def enterprise_values(self, symbol, period="annual"):
        args = {"apikey": self.api_key, "symbol": symbol, "period": period, "limit": 1000}
        func = fmpsdk.enterprise_values
        return self.handle_request(func, args)
    
    def income_statement(self, symbol, period="annual"):
        args = {"apikey": self.api_key, "symbol": symbol, "period": period, "limit": 1000}
        func = fmpsdk.income_statement
        return self.handle_request(func, args)
    
   

    class Rating(enum.Enum):
        strongSell, sell, hold, buy, strongBuy = range(1, 6)

    class TimeFrame(enum.Enum):
        min = "1min"
        fiveMin = "5min"
        fifteenMin = "15min"
        thirtyMin = "30min"
        hour = "1hour"
        # fourHour = "4hour" # Doesn't work; defaults to 1day for some reason
        day = "1day"
        week = "1week"
        month = "1month"
        year = "1year"

        @staticmethod
        def get_timeframe_from_min(mins):
            if mins == 1:
                return FMP.TimeFrame.min
            elif mins == 5:
                return FMP.TimeFrame.fiveMin
            elif mins == 15:
                return FMP.TimeFrame.fifteenMin
            elif mins == 30:
                return FMP.TimeFrame.thirtyMin
            elif mins == 60:
                return FMP.TimeFrame.hour
            elif mins == 1440:
                return FMP.TimeFrame.day
            elif mins == 10080:
                return FMP.TimeFrame.week
            elif mins == 43200:
                return FMP.TimeFrame.month
            elif mins == 525600:
                return FMP.TimeFrame.year

            return None





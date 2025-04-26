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

    def largest_ema_deltas(self, symbols, _from, period, number_tickers):
        all_peds = {}
        for symbol in tqdm(symbols, desc="Calculating EMAs"):
            ped = self.percent_ema_delta(symbol, _from, period)
            if not ped:
                continue

            for dt, p_diff in ped.items():
                if dt not in all_peds:
                    all_peds[dt] = {}

                all_peds[dt][symbol] = p_diff

        rel_peds = {}
        for dt, peds in all_peds.items():
            rel_tickers_pos = sorted(peds.items(), key=lambda item: item[1], reverse=True)[:int(number_tickers / 2)]
            rel_tickers_neg = sorted(peds.items(), key=lambda item: item[1])[:int(number_tickers / 2)]
            for ticker_set in [rel_tickers_pos, rel_tickers_neg]:
                for ticker, p_val in ticker_set:
                    if dt not in rel_peds:
                        rel_peds[dt] = {}

                    rel_peds[dt][ticker] = p_val

        return rel_peds

    def historical_market_capitalization(self, symbol, _from, to):
        args = {"apikey": self.api_key, "symbol": symbol, "from_date": _from.strftime("%Y-%m-%d"),
                "to_date": to.strftime("%Y-%m-%d")}
        func = fmpsdk.historical_market_capitalization
        market_caps = self.handle_request(func, args)
        return market_caps
    
    def historical_prices_multi(self, symbols, _from, to=datetime.now()):
        price_dict = {}
        for ticker_group in tqdm([symbols[i:i + 5] for i in range(0, len(symbols), 5)], desc="Downloading Price Data"):
            prices = self.historical_prices_raw(ticker_group, _from, to)
            if not prices or len(prices) == 0:
                return None
            for entry in prices:
                symbol = entry["symbol"]
                price_dict[symbol] = {}
                for price_data in entry.get("historical", []):
                    price_dict[symbol][datetime.strptime(price_data["date"], "%Y-%m-%d").timestamp()] = {
                        "open": price_data["open"], "close": price_data["close"]}
        
        return price_dict

    def historical_prices(self, symbol, _from, to=datetime.now()):
        prices = self.historical_prices_raw(symbol, _from, to)
        price_dict = {}
        if not prices or len(prices) == 0:
            return None
        for price_data in prices:
            price_dict[datetime.strptime(price_data["date"], "%Y-%m-%d").timestamp()] = {
                "open": price_data["open"], "close": price_data["close"]}
        return price_dict

    def historical_prices_raw(self, symbol, _from, to):
        args = {"apikey": self.api_key, "symbol": symbol, "from_date": _from.strftime("%Y-%m-%d"), "to_date": to.strftime("%Y-%m-%d")}
        func = fmpsdk.historical_price_full
        prices = self.handle_request(func, args)
        return prices

    def sentiment(self, symbol):
        res = self.comment_sentiments(symbol)
        if not res or len(res) == 0:
            return None
        last = res[0]
        return {"stocktwits": last["stocktwitsSentiment"], "twitter": last["twitterSentiment"]}

    def price_target(self, symbol, historical=False):
        res = self.price_target_consensus(symbol)
        raw = self.price_target_raw(symbol)
        if not raw or len(raw) == 0 or not res or len(res) == 0:
            return None
        if historical:
            return res
        return res[0]["targetConsensus"]

    def current_price(self, symbol):
        res = self.quote(symbol)
        return res[0]["price"]

    def recommendation(self, symbol, historical=False):
        res = self.rating(symbol, historical)
        if not res or len(res) == 0:
            return None

        res = sorted(res.items(), key=lambda item: item[0], reverse=True)
        return self.Rating(res[0][1]["ratingScore"])

    def comment_sentiments(self, symbol):
        args = {"apikey": self.api_key, "symbol": symbol}
        func = fmpsdk.social_sentiments
        return self.handle_request(func, args)

    def quote(self, symbol):
        args = {"apikey": self.api_key, "symbol": symbol}
        func = fmpsdk.quote
        return self.handle_request(func, args)

    def trending_sentiment(self, _type=None, source="twitter"):
        args = {"apikey": self.api_key, "type": _type, "source": source}
        func = fmpsdk.trending_sentiment
        return self.handle_request(func, args)

    def price_target_consensus(self, symbol):
        args = {"apikey": self.api_key, "symbol": symbol}
        func = fmpsdk.price_target_consensus
        return self.handle_request(func, args)

    def price_target_raw(self, symbol):
        args = {"apikey": self.api_key, "symbol": symbol}
        func = fmpsdk.price_target
        return self.handle_request(func, args)

    def rating(self, symbol, historical=False, _from=datetime(1970, 1, 1)):
        func = fmpsdk.historical_rating if historical else fmpsdk.rating
        args = {"apikey": self.api_key, "symbol": symbol} if not historical else {"apikey": self.api_key, "symbol": symbol, "limit": 100000000}
        raw = self.handle_request(func, args)
        data = {datetime.strptime(i["date"], "%Y-%m-%d").timestamp(): i for i in raw if
                datetime.strptime(i["date"], "%Y-%m-%d") >= _from}
        return data

    def insider_trading(self, symbol, reporting_cik=None, company_cik=None, limit=None):
        args = {"apikey": self.api_key, "symbol": symbol, "reporting_cik": reporting_cik, "company_cik": company_cik,
                "limit": limit}
        func = fmpsdk.insider_trading
        return self.handle_request(func, args)

    def daily_chart(self, symbol, interval_minutes, period_days):
        seconds_ago = period_days * 24 * 60 * 60
        five_minute_interval = 5 * 60

        delta = int(datetime.time() - seconds_ago)
        now = int(int(datetime.time()) / five_minute_interval) * five_minute_interval
        to = int(datetime.time())

        if to - now < 60:
            delta = delta - five_minute_interval
            to = now - five_minute_interval

        delta = datetime.fromtimestamp(delta).strftime('%Y-%m-%d')
        to = datetime.fromtimestamp(to).strftime('%Y-%m-%d')

        timeframe = self.TimeFrame.get_timeframe_from_min(interval_minutes)
        if not timeframe:
            raise ValueError("Invalid timeframe_min: " + str(interval_minutes))
        args = {"apikey": self.api_key, "symbol": symbol, "timeframe": timeframe.value, "from_date": delta,
                "to_date": to}
        func = fmpsdk.historical_chart
        return self.handle_request(func, args)

    def gainers(self):
        args = {"apikey": self.api_key}
        func = fmpsdk.gainers
        return self.handle_request(func, args)
    
    def losers(self):
        args = {"apikey": self.api_key}
        func = fmpsdk.losers
        return self.handle_request(func, args)
    
    def actives(self):
        args = {"apikey": self.api_key}
        func = fmpsdk.actives
        return self.handle_request(func, args)

    def screener(self, market_cap_more_than=None, market_cap_lower_than=None, price_more_than=None, price_lower_than=None, beta_more_than=None, beta_lower_than=None, volume_more_than=None, volume_lower_than=None, dividend_more_than=None, dividend_lower_than=None, is_etf=None, is_fund=None, is_actively_trading=None, sector=None, industry=None, country=None, exchange=None, limit=None, unique_companies=True):
        args = {"apikey": self.api_key, "market_cap_more_than": market_cap_more_than, "market_cap_lower_than": market_cap_lower_than, 
                "price_more_than": price_more_than, "price_lower_than": price_lower_than, "beta_more_than": beta_more_than, 
                "beta_lower_than": beta_lower_than, "volume_more_than": volume_more_than, "volume_lower_than": volume_lower_than, 
                "dividend_more_than": dividend_more_than, "dividend_lower_than": dividend_lower_than, "is_etf": is_etf, "is_fund": is_fund, 
                "is_actively_trading": is_actively_trading, "sector": sector, "industry": industry, "country": country, "exchange": exchange, 
                "limit": limit
                }
        func = fmpsdk.stock_screener
        market_caps = self.handle_request(func, args)
        if unique_companies:
            seen = set()
            market_caps = [data for data in market_caps if not (data["companyName"] in seen or seen.add(data["companyName"]))]
            
        return market_caps
    
    def stock_list(self):
        args = {"apikey": self.api_key}
        func = fmpsdk.stock_list
        return self.handle_request(func, args)
    
    def dividend_calendar(self, _from, to=datetime.now()):
        args = {"apikey": self.api_key, "from_date": _from.strftime("%Y-%m-%d"), "to_date": to.strftime("%Y-%m-%d")}
        func = fmpsdk.dividend_calendar
        return self.handle_request(func, args)
    
    def company_news(self, symbol, _from=datetime(2000, 1, 1), to=datetime.now()):
        args = {"apikey": self.api_key, "symbols": symbol, "limit": 100000000, "from_date": _from.strftime("%Y-%m-%d"), "to_date": to.strftime("%Y-%m-%d"), "limit": 250}
        iterable_args = {"func": fmpsdk.company_news, "args": args}
        func = fmpsdk.iterate_over_pages
        return self.handle_request(func, iterable_args)
    
    def general_news(self, _from=datetime(2000, 1, 1), to=datetime.now()):
        args = {"apikey": self.api_key, "limit": 100000000, "from_date": _from.strftime("%Y-%m-%d"), "to_date": to.strftime("%Y-%m-%d"), "limit": 250}
        iterable_args = {"func": fmpsdk.general_news, "args": args}
        func = fmpsdk.iterate_over_pages
        return self.handle_request(func, iterable_args)
    
    def company_press_releases(self, symbol, _from=datetime(2000, 1, 1), to=datetime.now()):
        args = {"apikey": self.api_key, "symbols": symbol, "limit": 100000000, "from_date": _from.strftime("%Y-%m-%d"), "to_date": to.strftime("%Y-%m-%d"), "limit": 250}
        iterable_args = {"func": fmpsdk.company_press_releases, "args": args}
        func = fmpsdk.iterate_over_pages
        return self.handle_request(func, iterable_args)
    
    @staticmethod
    def calculate_ema(data, period):
        if len(data) < period:
            raise ValueError("Period must be less than or equal to the length of the data.")

        alpha = 2 / (period + 1)
        ema_values = {}

        prev = None
        for i in data:
            if not prev:
                ema_values[i[0]] = i[1]
                prev = i[1]
                continue

            ema = (i[1] - prev) * alpha + prev
            ema_values[i[0]] = ema
            prev = ema

        return ema_values

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





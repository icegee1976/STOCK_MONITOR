"""資料來源層。

設計原則:
* stdlib 優先 (urllib) — 台股 FinMind 不需任何第三方套件。
* 美股用 yfinance(若已安裝);抓不到時優雅降級、給清楚訊息,而不是整支崩掉。
* 所有抓回來的資料放進輕量 JSON 快取,避免短時間重複打 API。

回傳統一格式 StockData,讓上層估價/分類模組不用管資料從哪來。
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")


@dataclass
class StockData:
    ticker: str
    market: str                       # "TW" | "US"
    name: str = ""
    currency: str = ""
    price: float | None = None        # 最新收盤/即時價(延遲)
    price_date: str = ""
    trailing_eps: float | None = None # 近四季 EPS
    per: float | None = None          # 目前本益比
    shares: float | None = None
    revenue_ttm: float | None = None
    dividend_yield: float | None = None   # 以「比例」表示, 0.0093 = 0.93%
    price_history: list = field(default_factory=list)   # [(date, close), ...] 舊→新
    per_history: list = field(default_factory=list)     # [(date, per), ...]
    per_history_approx: bool = False  # 美股的 PER 河流圖是近似(price/現EPS)
    source: str = ""
    error: str = ""

    def ok(self) -> bool:
        return self.price is not None and self.error == ""


# --------------------------------------------------------------------------- #
#  快取
# --------------------------------------------------------------------------- #
def _cache_path(market: str, ticker: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = ticker.replace("/", "_")
    return os.path.join(CACHE_DIR, f"{market}_{safe}.json")


def _load_cache(market: str, ticker: str, max_age_min: float):
    p = _cache_path(market, ticker)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            blob = json.load(f)
        fetched = datetime.fromisoformat(blob["_fetched_at"])
        if datetime.now() - fetched > timedelta(minutes=max_age_min):
            return None
        blob.pop("_fetched_at", None)
        return StockData(**blob)
    except Exception:
        return None


def _save_cache(data: StockData):
    try:
        blob = asdict(data)
        blob["_fetched_at"] = datetime.now().isoformat()
        with open(_cache_path(data.market, data.ticker), "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  HTTP 小工具
# --------------------------------------------------------------------------- #
def _http_get_json(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (aimonitor)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
#  台股: FinMind
# --------------------------------------------------------------------------- #
FINMIND = "https://api.finmindtrade.com/api/v4/data"


def _finmind(dataset: str, data_id: str, start_date: str, token: str = "") -> list:
    params = {"dataset": dataset, "data_id": data_id, "start_date": start_date}
    if token:
        params["token"] = token
    url = FINMIND + "?" + urllib.parse.urlencode(params)
    j = _http_get_json(url)
    if j.get("status") != 200 and "data" not in j:
        raise RuntimeError(f"FinMind {dataset} 回傳異常: {j.get('msg', j)}")
    return j.get("data", [])


def fetch_tw(ticker: str, name: str, years: int, token: str = "") -> StockData:
    start = (datetime.now() - timedelta(days=int(years * 365.25) + 10)).strftime("%Y-%m-%d")
    d = StockData(ticker=ticker, market="TW", name=name, currency="TWD", source="FinMind")
    try:
        prices = _finmind("TaiwanStockPrice", ticker, start, token)
        d.price_history = [(r["date"], float(r["close"])) for r in prices if r.get("close")]
        if d.price_history:
            d.price = d.price_history[-1][1]
            d.price_date = d.price_history[-1][0]
    except Exception as e:
        d.error = f"FinMind 價格抓取失敗: {e}"
        return d
    try:
        per = _finmind("TaiwanStockPER", ticker, start, token)
        d.per_history = [(r["date"], float(r["PER"])) for r in per if r.get("PER") not in (None, 0)]
        if per:
            last = per[-1]
            d.per = float(last["PER"]) if last.get("PER") else None
            dy = last.get("dividend_yield")
            d.dividend_yield = float(dy) / 100.0 if dy else None
            if d.per and d.price:
                d.trailing_eps = round(d.price / d.per, 4)  # EPS = 股價 / 本益比
    except Exception:
        pass  # PER 拿不到不致命,價格帶可改用 price_band
    return d


# --------------------------------------------------------------------------- #
#  美股: yfinance (主) — 失敗時給清楚降級訊息
# --------------------------------------------------------------------------- #
def fetch_us(ticker: str, name: str, years: int) -> StockData:
    d = StockData(ticker=ticker, market="US", name=name, currency="USD", source="yfinance")
    try:
        import yfinance as yf
    except ImportError:
        d.error = "未安裝 yfinance。請 `pip install yfinance` 以啟用美股報價。"
        return d
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=f"{max(years,1)}y", auto_adjust=True)
        if hist is not None and len(hist):
            d.price_history = [(idx.strftime("%Y-%m-%d"), float(c))
                               for idx, c in zip(hist.index, hist["Close"])]
            d.price = d.price_history[-1][1]
            d.price_date = d.price_history[-1][0]
        # 基本面 (info 可能慢/不穩,包起來)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        d.trailing_eps = _f(info.get("trailingEps"))
        d.shares = _f(info.get("sharesOutstanding"))
        d.revenue_ttm = _f(info.get("totalRevenue"))
        # yfinance 的 dividendYield 欄位格式不穩(有時 0.46 代表 0.46%)。
        # 改用 trailingAnnualDividendYield(乾淨的小數,如 0.0002),較可靠;
        # 退而求其次用 年化股利金額 / 股價。
        dy = _f(info.get("trailingAnnualDividendYield"))
        if dy is None:
            rate = _f(info.get("dividendRate"))
            if rate and d.price:
                dy = rate / d.price
        if dy is not None:
            d.dividend_yield = dy if dy < 1 else dy / 100.0  # 保險:>1 必是百分數誤填
        cur = info.get("currency")
        if cur:
            d.currency = cur
        if d.per is None and d.price and d.trailing_eps and d.trailing_eps > 0:
            d.per = round(d.price / d.trailing_eps, 2)
        # 近似本益比河流圖: 用歷史股價 / 現行 EPS (假設 EPS 不變,標示為近似)
        if d.trailing_eps and d.trailing_eps > 0:
            d.per_history = [(dt, round(c / d.trailing_eps, 2)) for dt, c in d.price_history]
            d.per_history_approx = True
        if not d.price:
            d.error = "yfinance 取得不到價格(可能被限流或代號錯誤)。"
    except Exception as e:
        d.error = f"yfinance 抓取失敗: {type(e).__name__}: {str(e)[:140]}"
    return d


def _f(x):
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  對外統一入口
# --------------------------------------------------------------------------- #
def fetch(stock_cfg: dict, providers_cfg: dict, history_years: int, use_cache: bool = True) -> StockData:
    market = stock_cfg["market"].upper()
    ticker = str(stock_cfg["ticker"])
    name = stock_cfg.get("name", ticker)
    cache_min = float(providers_cfg.get("cache_minutes", 15))

    if use_cache:
        cached = _load_cache(market, ticker, cache_min)
        if cached is not None:
            return cached

    if market == "TW":
        data = fetch_tw(ticker, name, history_years, providers_cfg.get("finmind_token", ""))
    elif market == "US":
        data = fetch_us(ticker, name, history_years)
    else:
        data = StockData(ticker=ticker, market=market, name=name, error=f"未知市場 {market}")

    if data.ok():
        _save_cache(data)
    return data


# --------------------------------------------------------------------------- #
#  匯率
# --------------------------------------------------------------------------- #
def usd_twd(fallback: float = 32.0) -> float:
    """嘗試線上取 USD/TWD,失敗回退設定值。"""
    try:
        j = _http_get_json("https://api.exchangerate-api.com/v4/latest/USD", timeout=10)
        rate = j.get("rates", {}).get("TWD")
        if rate:
            return float(rate)
    except Exception:
        pass
    return float(fallback)

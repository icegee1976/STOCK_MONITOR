"""`aimonitor.providers.fetch` 降級鏈離線 mock 測試(工單 005)。

目的:此鏈是雲端存活的命脈(README ☁ 節)——台股 FinMind、美股 Finnhub→yfinance
兩層備援、快取 hit/過期快取保命、_retry 指數退避。全部離線 mock,**零真實網路**,
用來鎖住 aimonitor/providers.py 現有的降級順序與錯誤語意(不驗證「應該長怎樣」的
理想行為,只驗證「現在真的長怎樣」,好讓未來改動這條鏈時有 regression 網)。

mock 紀律(對照工單 SPEC 的 4 條):
1. 快取隔離:每個測試把 `providers.CACHE_DIR` patch 到 tempfile 目錄
   (setUp 建立、tearDown 清除)。_cache_path() 在執行期讀模組全域 CACHE_DIR,
   所以這個 patch 對所有 fetch 系列函數都生效,不會碰到使用者真實 .cache/。
2. 保險絲:patch `providers.urllib.request.urlopen` 為一被呼叫就
   raise AssertionError("real network!")。主要 mock 點是 `_http_get_json`
   (每個測試都會 patch 它);保險絲用來證明沒有任何呼叫漏網繞過 mock 直接連網。
3. yfinance:fetch_us / fetch_us_finnhub 都在函數內 `import yfinance as yf`,
   用 `patch.dict(sys.modules, {"yfinance": fake_module})` 注入假模組。此機器
   未安裝真 yfinance(見開發時確認),但即使安裝了 patch.dict 也會在測試期間
   讓 import 綁定到假物件,不會執行到真模組。fake Ticker 提供 .history()/.info,
   刻意不 import pandas —— 用最小的 FakeHist(index=[FakeTs...], ["Close"]=[float...])
   即可滿足 fetch_us/fetch_us_finnhub 用到的 len()/.index/["Close"]/zip/.strftime。
4. 速度:patch `providers.time.sleep` 為 no-op 並用 call_count 驗證有沒有被呼叫,
   讓 _retry 的 0.8s/1.6s 指數退避不拖慢測試套件(仍是秒級)。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from aimonitor import providers
from aimonitor.providers import StockData, _retry


# --------------------------------------------------------------------------- #
#  共用測試基底:快取隔離 + 保險絲 + 免 sleep
# --------------------------------------------------------------------------- #
class ProvidersFallbackTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_cache_")
        self._cache_patch = patch.object(providers, "CACHE_DIR", self._tmpdir)
        self._cache_patch.start()

        # 保險絲:任何真的打到 urlopen 的呼叫都視為測試設計錯誤,立刻炸掉。
        self._urlopen_patch = patch.object(
            providers.urllib.request,
            "urlopen",
            side_effect=AssertionError("real network! _http_get_json 應該被 mock 攔住"),
        )
        self._urlopen_patch.start()

        # _retry 的指數退避在測試中不需要真的等待。
        self._sleep_patch = patch.object(providers.time, "sleep")
        self.mock_sleep = self._sleep_patch.start()

        # 環境變數快照:測試內可放心 pop/set os.environ(如 FINNHUB_API_KEY),
        # stop 時自動還原,不汙染同 process 的後續測試或部署者 shell 環境。
        self._env_patch = patch.dict(os.environ)
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        self._sleep_patch.stop()
        self._urlopen_patch.stop()
        self._cache_patch.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # -- helpers ------------------------------------------------------- #
    def _stock_cfg(self, ticker="AAPL", market="US", name="Apple", method=""):
        cfg = {"ticker": ticker, "market": market, "name": name}
        if method:
            cfg["valuation"] = {"method": method}
        return cfg

    def _providers_cfg(self, finmind_token="", finnhub_api_key="", cache_minutes=15):
        return {
            "finmind_token": finmind_token,
            "finnhub_api_key": finnhub_api_key,
            "cache_minutes": cache_minutes,
        }

    def _write_stale_cache(self, data: StockData, days_old: float = 2.0):
        """用production的_save_cache寫入,再把_fetched_at改成過期,製造「有快取但已過TTL」情境。
        重用 _save_cache 的序列化邏輯(而不是手刻 JSON blob),避免 fixture 與
        StockData 的真實 dataclass 欄位形狀脫節。"""
        providers._save_cache(data)
        path = providers._cache_path(data.market, data.ticker)
        import json
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
        blob["_fetched_at"] = (datetime.now() - timedelta(days=days_old)).isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)


# --------------------------------------------------------------------------- #
#  假 yfinance 模組(不 import pandas,最小化滿足 fetch_us/fetch_us_finnhub 用到的介面)
# --------------------------------------------------------------------------- #
class _FakeTimestamp:
    """只需要支援 .strftime("%Y-%m-%d")(fetch_us / fetch_us_finnhub 都這樣用)。"""

    def __init__(self, s: str):
        self._s = s

    def strftime(self, fmt):
        # 測試 fixture 一律直接給 "%Y-%m-%d" 格式字串,原樣回傳即可(不必真的解析)。
        assert fmt == "%Y-%m-%d"
        return self._s


class _FakeHist:
    """最小化滿足: len(hist)、hist.index、hist["Close"]、zip(hist.index, hist["Close"])。"""

    def __init__(self, rows):
        # rows: list[(date_str, close_float)]
        self.index = [_FakeTimestamp(d) for d, _ in rows]
        self._closes = [c for _, c in rows]

    def __len__(self):
        return len(self._closes)

    def __getitem__(self, key):
        assert key == "Close"
        return self._closes

    def __bool__(self):
        # fetch_us/fetch_us_finnhub 先判斷 `hist is not None and len(hist)`,
        # 不會走到 hist 本身的 bool(),但保留避免任何隱性 truthiness 誤用時炸得清楚。
        return len(self._closes) > 0


class _FakeTicker:
    def __init__(self, hist_rows=None, info=None, raise_on_history=False, raise_on_info=False):
        self._hist_rows = hist_rows or []
        self._info = info if info is not None else {}
        self._raise_on_history = raise_on_history
        self._raise_on_info = raise_on_info
        self.history_calls = []

    def history(self, period=None, auto_adjust=None):
        self.history_calls.append((period, auto_adjust))
        if self._raise_on_history:
            raise RuntimeError("yfinance 429 (fake)")
        return _FakeHist(self._hist_rows)

    @property
    def info(self):
        if self._raise_on_info:
            raise RuntimeError("info 429 (fake)")
        return self._info


def _fake_yf_module(hist_rows=None, info=None, raise_on_history=False, raise_on_info=False):
    """回傳一個具備 .Ticker(ticker) 建構子的假 yfinance 模組物件,供
    patch.dict(sys.modules, {"yfinance": ...}) 注入。"""
    ticker_kwargs = dict(
        hist_rows=hist_rows, info=info,
        raise_on_history=raise_on_history, raise_on_info=raise_on_info,
    )
    fake_module = type("FakeYFModule", (), {})()
    fake_module.Ticker = lambda ticker: _FakeTicker(**ticker_kwargs)
    return fake_module


def _http_get_json_router(routes):
    """建一個 side_effect callable,依 URL 子字串分流到不同 handler。
    routes: list[(substring, handler_or_exception)]。同時把收到的每個 URL
    append 進 routes 呼叫方傳入的 `calls` list(呼叫數斷言用,見 S8)。
    handler 可以是 dict(直接回傳)、Exception 實例(raise)、或 callable(url)->dict。
    """
    def _dispatch(url, timeout=25):
        for substr, handler in routes:
            if substr in url:
                if isinstance(handler, Exception):
                    raise handler
                if callable(handler):
                    return handler(url)
                return handler
        raise AssertionError(f"_http_get_json_router: 沒有任何 route 匹配 URL: {url}")
    return _dispatch


# =========================================================================== #
#  S1 — 快取 hit → 0 網路
# =========================================================================== #
class CacheHitZeroNetworkTest(ProvidersFallbackTestCase):
    """鎖:fetch(use_cache=True) 若有新鮮快取,直接回傳、完全不打任何網路
    (_http_get_json 呼叫數必須是 0,且 yfinance 完全不被 import)。
    什麼突變會翻紅:未來如果有人在 cache-hit 分支之後又「順便」打一次報價確認
    (常見的偷懶寫法),或是 _load_cache 的 TTL 判斷被改壞導致新鮮快取被當成
    過期而重新抓取,這裡就會抓到(mock_http.call_count 從 0 變成 >0)。
    """

    def test_fresh_cache_hit_skips_all_network_calls(self):
        cached = StockData(
            ticker="2330", market="TW", name="台積電", currency="TWD",
            price=1000.0, price_date="2026-07-01", source="FinMind",
        )
        providers._save_cache(cached)  # 剛寫入,必定新鮮(TTL=15min 綽綽有餘)

        # 注入「一被使用就炸」的假 yfinance:cache-hit 路徑若碰到 yf.Ticker 會直接
        # AssertionError。這比檢查 sys.modules 是否含 yfinance 更強且無環境耦合
        # (不依賴「全 process 沒人 import 過 yfinance」的前提)。
        def _forbidden_ticker(ticker):
            raise AssertionError("cache hit 不應觸碰 yfinance")

        forbidden_yf = type("ForbiddenYF", (), {})()
        forbidden_yf.Ticker = _forbidden_ticker

        with patch.object(providers, "_http_get_json") as mock_http, \
             patch.dict(sys.modules, {"yfinance": forbidden_yf}):
            result = providers.fetch(
                self._stock_cfg(ticker="2330", market="TW", name="台積電"),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )

        self.assertEqual(mock_http.call_count, 0)
        self.assertEqual(result.price, 1000.0)
        self.assertEqual(result.source, "FinMind")


# =========================================================================== #
#  S2 — US 有金鑰、Finnhub 成功(含歷史缺失仍 ok,並確認有寫快取)
# =========================================================================== #
class FinnhubSuccessTest(ProvidersFallbackTestCase):
    """鎖:fetch_us_finnhub 在 Finnhub 報價成功時,即使「補歷史」的 yfinance
    best-effort 呼叫失敗(雲端常見情境),整體結果仍然 ok()、source 標為
    "Finnhub"、price 來自 Finnhub 的 /quote,且不會因歷史缺失而把 error 標起來
    (price_history 保持空 list,不是 None、不是崩潰)。同時鎖住成功結果會被
    _save_cache 寫入 temp CACHE_DIR(fetch() 的 `if data.ok(): _save_cache(data)` 分支)。
    什麼突變會翻紅:如果有人把「補歷史失敗」也算進 d.error(讓 ok() 變 False),
    或忘記在成功時呼叫 _save_cache,這裡都會抓到。
    """

    def test_finnhub_quote_success_with_history_fetch_failure_still_ok_and_cached(self):
        calls = []

        def _quote(url):
            calls.append(url)
            return {"c": 123.0, "t": 1751328000}  # 2025-07-01 附近的任意 epoch

        def _metric(url):
            calls.append(url)
            return {"metric": {"epsTTM": 5.0, "peTTM": 24.6}}

        router = _http_get_json_router([
            ("/quote?", _quote),
            ("/stock/metric?", _metric),
        ])

        fake_yf = _fake_yf_module(raise_on_history=True)  # 補歷史失敗(best-effort 應靜默略過)

        with patch.object(providers, "_http_get_json", side_effect=router), \
             patch.dict(sys.modules, {"yfinance": fake_yf}):
            result = providers.fetch(
                self._stock_cfg(ticker="AAPL", market="US"),
                self._providers_cfg(finnhub_api_key="fake-key"),
                history_years=5,
                use_cache=True,
            )

        self.assertTrue(result.ok())
        self.assertEqual(result.source, "Finnhub")
        self.assertEqual(result.price, 123.0)
        self.assertEqual(result.price_history, [])  # 歷史缺不致命,維持空 list
        self.assertEqual(len(calls), 2)  # /quote + /stock/metric,各打一次

        # 成功結果應已寫入 temp CACHE_DIR(fetch() 的 ok() → _save_cache 分支)。
        cache_file = providers._cache_path("US", "AAPL")
        self.assertTrue(os.path.exists(cache_file))


# =========================================================================== #
#  S3 — Finnhub quote c=0 / 例外 → 整檔退回 yfinance(兩個 case)
# =========================================================================== #
class FinnhubFallbackToYfinanceTest(ProvidersFallbackTestCase):
    """鎖:fetch_us_finnhub 的「退回 yfinance」邏輯有兩條觸發路徑——
    (a) /quote 正常回應但 c=0(無此美股/無資料,_finnhub_us 提前 return {}),
    (b) /quote 本身丟例外(_finnhub_us 內沒有 try 包住第一次呼叫,例外會傳到
        fetch_us_finnhub 的 `except Exception: fh = {}`)。
    兩者都應該殊途同歸地整檔退回 fetch_us(yfinance),source 變成 "yfinance"、
    price 來自假造的 yfinance 歷史最後一筆。
    什麼突變會翻紅:如果 c=0 判斷被改成只認 `is None`(讓 0 被誤當合法報價),
    或例外處理被拿掉導致例外往上炸穿,這裡都會抓到。
    """

    def _run_with_quote_handler(self, quote_handler):
        router = _http_get_json_router([("/quote?", quote_handler)])
        fake_yf = _fake_yf_module(hist_rows=[("2026-06-30", 150.0), ("2026-07-01", 151.5)])
        with patch.object(providers, "_http_get_json", side_effect=router), \
             patch.dict(sys.modules, {"yfinance": fake_yf}):
            return providers.fetch(
                self._stock_cfg(ticker="MSFT", market="US"),
                self._providers_cfg(finnhub_api_key="fake-key"),
                history_years=1,
                use_cache=True,
            )

        # 交由呼叫方 assert

    def test_quote_c_zero_falls_back_to_yfinance(self):
        result = self._run_with_quote_handler(lambda url: {"c": 0, "t": None})
        self.assertTrue(result.ok())
        self.assertEqual(result.source, "yfinance")
        self.assertEqual(result.price, 151.5)

    def test_quote_exception_falls_back_to_yfinance(self):
        result = self._run_with_quote_handler(RuntimeError("finnhub down (fake)"))
        self.assertTrue(result.ok())
        self.assertEqual(result.source, "yfinance")
        self.assertEqual(result.price, 151.5)


# =========================================================================== #
#  S4 — US 無金鑰 → 直接 yfinance,證明沒偷打 Finnhub
# =========================================================================== #
class NoFinnhubKeyGoesStraightToYfinanceTest(ProvidersFallbackTestCase):
    """鎖:fetch() 的分流邏輯 `fh_key = providers_cfg.get(...) or os.environ.get(...)`——
    當兩者都是空字串時必須直接呼叫 fetch_us,完全不觸碰 _http_get_json(Finnhub 走
    HTTP、yfinance 走假模組,兩者互斥,呼叫數 ==0 才能證明真的沒繞去打 Finnhub)。
    什麼突變會翻紅:如果 fh_key 的空字串判斷被改壞(例如 `if fh_key is not None`
    誤把 "" 當成「有金鑰」),這裡的 _http_get_json 呼叫數會從 0 變成 >0。
    也額外確認環境變數 FINNHUB_API_KEY 沒有殘留造成偽陽性(os.environ.pop)。
    """

    def test_no_finnhub_key_means_zero_http_get_json_calls(self):
        os.environ.pop("FINNHUB_API_KEY", None)  # 避免本機殘留環境變數污染此測試
        fake_yf = _fake_yf_module(hist_rows=[("2026-07-01", 400.0)])

        with patch.object(providers, "_http_get_json") as mock_http, \
             patch.dict(sys.modules, {"yfinance": fake_yf}):
            result = providers.fetch(
                self._stock_cfg(ticker="NVDA", market="US"),
                self._providers_cfg(finnhub_api_key=""),
                history_years=1,
                use_cache=True,
            )

        self.assertEqual(mock_http.call_count, 0)
        self.assertTrue(result.ok())
        self.assertEqual(result.source, "yfinance")
        self.assertEqual(result.price, 400.0)


# =========================================================================== #
#  S5 — 全源失敗 + 過期快取保命
# =========================================================================== #
class StaleCacheRescueTest(ProvidersFallbackTestCase):
    """鎖:fetch() 在線上抓取全部失敗(data.ok()==False)時,會用
    `_load_cache(market, ticker, None)`(max_age_min=None 無視 TTL)撈「過期」的
    舊快取回來救命,並把 source 標上「(過期快取)」字樣,ok() 仍為 True。
    這是雲端額度用罄/被限流時「顯示上次成功的資料」而非整檔消失的關鍵行為。
    什麼突變會翻紅:如果有人把 `_load_cache(..., None)` 誤改成帶 TTL(讓過期快取
    真的被當成過期而回傳 None),或忘記在 source 加註記,這裡都會抓到。
    """

    def test_all_sources_fail_falls_back_to_stale_cache_with_marker(self):
        stale = StockData(
            ticker="TSLA", market="US", name="Tesla", currency="USD",
            price=250.0, price_date="2026-07-01", source="Finnhub",
        )
        self._write_stale_cache(stale, days_old=2.0)  # 2 天前,遠超 15min TTL

        os.environ.pop("FINNHUB_API_KEY", None)
        fake_yf = _fake_yf_module(raise_on_history=True)  # yfinance 也失敗

        with patch.object(providers, "_http_get_json") as mock_http, \
             patch.dict(sys.modules, {"yfinance": fake_yf}):
            result = providers.fetch(
                self._stock_cfg(ticker="TSLA", market="US"),
                self._providers_cfg(finnhub_api_key=""),
                history_years=1,
                use_cache=True,  # 讀取路徑本身仍走 use_cache=True,但 TTL 內無新鮮快取(2天前)
            )

        self.assertEqual(mock_http.call_count, 0)  # 無金鑰,壓根沒打 Finnhub;yfinance 也是假的
        self.assertTrue(result.ok())
        self.assertEqual(result.price, 250.0)
        self.assertIn("(過期快取)", result.source)


# =========================================================================== #
#  S6 — 全源失敗 + 無快取 → error 非空
# =========================================================================== #
class AllSourcesFailNoCacheTest(ProvidersFallbackTestCase):
    """鎖:當全部來源失敗、且 temp CACHE_DIR 完全沒有任何快取檔時,fetch() 最終
    return 的是「原始失敗的 data」(帶 error 訊息),ok()==False。這是使用者第一次
    抓取某支新股票、剛好遇到全源失敗時會看到的「誠實失敗」路徑,不應該被過期快取
    邏輯意外掩蓋成看似成功。
    什麼突變會翻紅:如果 stale fallback 誤判成功(例如 `_load_cache` 對「不存在的
    檔案」回傳了非 None 的假資料),這裡的 ok() 會從 False 變 True。
    """

    def test_all_sources_fail_and_no_cache_returns_error_data(self):
        os.environ.pop("FINNHUB_API_KEY", None)
        fake_yf = _fake_yf_module(raise_on_history=True)

        with patch.object(providers, "_http_get_json") as mock_http, \
             patch.dict(sys.modules, {"yfinance": fake_yf}):
            result = providers.fetch(
                self._stock_cfg(ticker="BRAND_NEW_TICKER", market="US"),
                self._providers_cfg(finnhub_api_key=""),
                history_years=1,
                use_cache=True,
            )

        self.assertEqual(mock_http.call_count, 0)
        self.assertFalse(result.ok())
        self.assertNotEqual(result.error, "")


# =========================================================================== #
#  S7 — TW FinMind 402(清楚錯誤訊息)
# =========================================================================== #
class FinMind402ErrorMessageTest(ProvidersFallbackTestCase):
    """鎖:fetch_tw 對「額度用罄」有專門的清楚錯誤訊息分支(commit 02a6787),
    不是把原始例外字串直接丟給使用者。判斷方式是 `"402" in str(e)`,錯誤訊息
    必須同時提到「402」與「FINMIND_TOKEN」,讓使用者知道怎麼解(申請 token)。
    什麼突變會翻紅:如果有人把訊息改成不含 "FINMIND_TOKEN"(例如精簡成純數字
    錯誤碼),或把 "402" 判斷改成別的字串比對邏輯導致誤判成一般錯誤訊息
    (`FinMind 價格抓取失敗: ...`,不含 "FINMIND_TOKEN"),這裡都會抓到。
    """

    def test_402_error_message_mentions_402_and_finmind_token(self):
        with patch.object(
            providers, "_http_get_json",
            side_effect=RuntimeError("HTTP Error 402: Payment Required"),
        ):
            result = providers.fetch(
                self._stock_cfg(ticker="2330", market="TW", name="台積電"),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )

        self.assertFalse(result.ok())
        self.assertIn("402", result.error)
        self.assertIn("FINMIND_TOKEN", result.error)


# =========================================================================== #
#  S8 — TW 呼叫數 = api-budget §1 表(regression 化)
# =========================================================================== #
class TwFinMindCallCountByMethodTest(ProvidersFallbackTestCase):
    """鎖:fetch_tw 依 `valuation.method` 決定要不要額外打 PER / 配息 API
    (為省 FinMind 額度,見 providers.py 註解)。004 審計已經人工數過這張表,
    這裡把它變成可執行的 regression:
      - method="" (price_band)      → 只打 TaiwanStockPrice        = 1 次
      - method="pe_band"            → Price + PER                 = 2 次
      - method="yield_band"         → Price + Dividend            = 2 次
    什麼突變會翻紅:如果有人「順手」把 PER 或配息呼叫從 `if method == "xxx"` 的
    條件式底下移出來變成無條件呼叫(額度會爆),這裡的呼叫數斷言會從預期值變多。
    """

    def _call_count_for_method(self, method: str) -> int:
        urls = []

        def _price(url):
            urls.append(url)
            return {"status": 200, "data": [
                {"date": "2026-06-30", "close": 999.0},
                {"date": "2026-07-01", "close": 1000.0},
            ]}

        def _per(url):
            urls.append(url)
            return {"status": 200, "data": [
                {"date": "2026-07-01", "PER": 18.5, "dividend_yield": 2.1},
            ]}

        def _dividend(url):
            urls.append(url)
            return {"status": 200, "data": [
                {"CashExDividendTradingDate": "2026-01-15", "CashEarningsDistribution": 2.5},
            ]}

        router = _http_get_json_router([
            ("dataset=TaiwanStockPrice", _price),
            ("dataset=TaiwanStockPER", _per),
            ("dataset=TaiwanStockDividend", _dividend),
        ])

        with patch.object(providers, "_http_get_json", side_effect=router):
            result = providers.fetch(
                self._stock_cfg(ticker="2330", market="TW", name="台積電", method=method),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )
        self.assertTrue(result.ok())  # 每個分支都應成功(才能確保計數不是因為失敗提早中止)
        return len(urls)

    def test_price_band_method_calls_finmind_once(self):
        self.assertEqual(self._call_count_for_method(""), 1)

    def test_pe_band_method_calls_finmind_twice(self):
        self.assertEqual(self._call_count_for_method("pe_band"), 2)

    def test_yield_band_method_calls_finmind_twice(self):
        self.assertEqual(self._call_count_for_method("yield_band"), 2)


# =========================================================================== #
#  S9 — _retry 語意(對抗 429 的核心)
# =========================================================================== #
class RetrySemanticsTest(ProvidersFallbackTestCase):
    """鎖:_retry 的重試/退避/最終失敗語意——這是所有網路呼叫共用的底層機制,
    對抗雲端機房 IP 被 Yahoo/FinMind 429 限流。
    什麼突變會翻紅:如果重試次數被改壞(例如 attempts 用到 off-by-one)、
    sleep 呼叫次數不對(該退避的時候沒退避,或多退避一次),或全部失敗時
    raise 了錯誤的例外(例如 raise 第一個而非最後一個),這裡都會抓到。
    """

    def test_succeeds_on_third_attempt_and_sleeps_twice(self):
        # 前兩次失敗、第三次成功 → 應該呼叫 sleep 兩次(每次失敗後退避一次,
        # 成功的最後一次不再退避)。
        calls = {"n": 0}

        def _flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError(f"transient failure #{calls['n']}")
            return "ok-on-third-try"

        self.mock_sleep.reset_mock()  # 防同測試內其他路徑的 sleep 串進計數
        result = _retry(_flaky, attempts=3, base_delay=0.8)

        self.assertEqual(result, "ok-on-third-try")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(self.mock_sleep.call_count, 2)
        # 指數退避:0.8 * 2**0 = 0.8,0.8 * 2**1 = 1.6(鎖住退避倍率,不只是次數)。
        self.mock_sleep.assert_any_call(0.8)
        self.mock_sleep.assert_any_call(1.6)

    def test_all_attempts_fail_raises_the_last_exception(self):
        # 3 次全敗 → 應該 raise 「最後一個」例外(不是第一個),用可辨識的
        # 不同例外實例逐一驗證身分(identity,而不只是訊息字串)。
        errors = [RuntimeError("first"), RuntimeError("second"), RuntimeError("third")]
        calls = {"n": 0}

        def _always_fails():
            e = errors[calls["n"]]
            calls["n"] += 1
            raise e

        self.mock_sleep.reset_mock()  # 防同測試內其他路徑的 sleep 串進計數
        with self.assertRaises(RuntimeError) as ctx:
            _retry(_always_fails, attempts=3, base_delay=0.8)

        self.assertIs(ctx.exception, errors[-1])  # 必須是「第三個」例外實例本身
        self.assertEqual(calls["n"], 3)
        self.assertEqual(self.mock_sleep.call_count, 2)  # 第3次失敗後不再退避(已無下一次嘗試)


if __name__ == "__main__":
    unittest.main()

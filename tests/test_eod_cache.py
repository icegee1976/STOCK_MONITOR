"""台股 EOD-aware 快取新鮮度(工單 013)。

背景:台股資料是日收盤(EOD),FinMind 收盤後約傍晚才更新,一天只變一次;
固定 15 分 TTL 會在活躍使用時整天重抓不會變的資料。改成「有沒有跨過收盤
更新邊界」判斷新鮮度。此檔鎖住:

1. `_tw_cache_fresh` 純函數本身(表格測試,顯式傳 fetched_at/now,全部 aware
   台北 UTC+8,涵蓋 SPEC 列的每個邊界案例)。
2. `fetch()` 接線(離線 mock,沿用 005 的 mock 紀律:CACHE_DIR 隔離到 tempdir、
   urlopen 保險絲、_http_get_json 逐條 mock):TW 走 EOD-aware 規則、
   US/INTL 維持固定 15 分 TTL 不變(回歸鎖)。

全部離線、0 API 呼叫。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from aimonitor import providers
from aimonitor.providers import StockData, TPE, _tw_cache_fresh


# =========================================================================== #
#  純函數表格測試:_tw_cache_fresh
# =========================================================================== #
class TwCacheFreshPureFunctionTest(unittest.TestCase):
    """全部顯式傳入 aware datetime(除非案例本身就是在測 naive 相容),避免
    依賴系統當前時間造成測試不穩定。基準日曆(已用 python 驗證過星期幾):
    2026-01-07 = 週三、2026-01-08 = 週四、2026-01-09 = 週五、
    2026-01-10 = 週六、2026-01-11 = 週日、2026-01-12 = 週一。
    """

    def _tpe(self, y, m, d, hh, mm=0):
        return datetime(y, m, d, hh, mm, tzinfo=TPE)

    def test_intraday_same_day_before_boundary_is_fresh(self):
        # 週三 10:00 抓、14:00 問:同一天、尚未到 18:00 邊界 → 沒跨邊界 → 新鮮。
        fetched = self._tpe(2026, 1, 7, 10, 0)
        now = self._tpe(2026, 1, 7, 14, 0)
        self.assertTrue(_tw_cache_fresh(fetched, now, floor_minutes=1))

    def test_cross_same_day_boundary_is_stale(self):
        # 週三 10:00 抓、19:00 問:(10:00, 19:00] 內含週三 18:00 邊界 → 過期。
        fetched = self._tpe(2026, 1, 7, 10, 0)
        now = self._tpe(2026, 1, 7, 19, 0)
        self.assertFalse(_tw_cache_fresh(fetched, now, floor_minutes=1))

    def test_after_boundary_to_next_day_before_boundary_is_fresh(self):
        # 週三 19:00 抓(週三邊界已過)、週四 10:00 問(週四邊界 18:00 尚未到)
        # → 兩個邊界都不在 (fetched, now] 內 → 新鮮。
        fetched = self._tpe(2026, 1, 7, 19, 0)
        now = self._tpe(2026, 1, 8, 10, 0)
        self.assertTrue(_tw_cache_fresh(fetched, now, floor_minutes=1))

    def test_friday_evening_to_sunday_is_fresh(self):
        # 週五 19:00 抓、週日問:週末沒有邊界 → 新鮮。
        fetched = self._tpe(2026, 1, 9, 19, 0)
        now = self._tpe(2026, 1, 11, 12, 0)
        self.assertTrue(_tw_cache_fresh(fetched, now, floor_minutes=1))

    def test_friday_evening_to_monday_before_boundary_is_fresh(self):
        # 週五 19:00 抓、週一 17:59 問:週一 18:00 邊界尚未到 → 新鮮。
        # (最大無邊界間隔 = 週五18:00→週一18:00 = 72h,SPEC 已註記。)
        fetched = self._tpe(2026, 1, 9, 19, 0)
        now = self._tpe(2026, 1, 12, 17, 59)
        self.assertTrue(_tw_cache_fresh(fetched, now, floor_minutes=1))

    def test_friday_evening_to_monday_after_boundary_is_stale(self):
        # 週五 19:00 抓、週一 18:01 問:跨過週一 18:00 邊界 → 過期。
        fetched = self._tpe(2026, 1, 9, 19, 0)
        now = self._tpe(2026, 1, 12, 18, 1)
        self.assertFalse(_tw_cache_fresh(fetched, now, floor_minutes=1))

    def test_floor_overrides_a_crossed_boundary(self):
        # 週三 17:57 抓、18:02 問:名目上跨過 18:00 邊界,但只過了 5 分鐘,
        # 小於安全地板 15 分 → 地板凌駕邊界判斷 → 新鮮。
        fetched = self._tpe(2026, 1, 7, 17, 57)
        now = self._tpe(2026, 1, 7, 18, 2)
        self.assertTrue(_tw_cache_fresh(fetched, now, floor_minutes=15))
        # 對照組:同樣的邊界跨越,若地板調低到 1 分鐘(< 5 分鐘的實際間隔),
        # 地板不再蓋過邊界判斷 → 應該過期,證明地板真的是「地板」而非恆真。
        self.assertFalse(_tw_cache_fresh(fetched, now, floor_minutes=1))

    def test_naive_legacy_timestamp_interpreted_as_taipei(self):
        # legacy _fetched_at 是 naive isoformat → 一律當作台北時間解讀。
        # naive 10:00 抓、aware 14:00(台北)問,同一天未到邊界 → 新鮮。
        fetched_naive = datetime(2026, 1, 7, 10, 0)  # 無 tzinfo
        now_aware = self._tpe(2026, 1, 7, 14, 0)
        self.assertTrue(_tw_cache_fresh(fetched_naive, now_aware, floor_minutes=1))
        # 同一組 naive fetched,問到已跨邊界的時間點 → 過期(證明真的有拿來比較,
        # 不是被忽略或恆真)。
        now_after_boundary = self._tpe(2026, 1, 7, 19, 0)
        self.assertFalse(_tw_cache_fresh(fetched_naive, now_after_boundary, floor_minutes=1))

    def test_aware_utc_input_is_normalized_to_taipei_before_comparing(self):
        # fetched 用 UTC 表示 02:00(= 台北 10:00),now 用 UTC 表示 11:00
        # (= 台北 19:00)。若沒有正確轉換成台北時區直接比較 UTC 掛鐘時間,
        # 邊界掃描會用錯的「本地時間」算,這裡驗證有正確 astimezone 轉換:
        # 台北 10:00→19:00 跨過 18:00 邊界 → 過期。
        fetched_utc = datetime(2026, 1, 7, 2, 0, tzinfo=timezone.utc)
        now_utc = datetime(2026, 1, 7, 11, 0, tzinfo=timezone.utc)
        self.assertFalse(_tw_cache_fresh(fetched_utc, now_utc, floor_minutes=1))

    def test_naive_and_aware_mixed_inputs_do_not_raise_typeerror(self):
        # fetched naive、now aware(或反過來)必須被正規化後才相減,不能直接
        # naive - aware 炸 TypeError。這裡只驗證「呼叫不炸」+ 回傳合理布林值,
        # 具體邊界語意已由上面兩個測試覆蓋。
        fetched_naive = datetime(2026, 1, 7, 10, 0)
        now_aware = self._tpe(2026, 1, 7, 14, 0)
        try:
            result = _tw_cache_fresh(fetched_naive, now_aware, floor_minutes=1)
        except TypeError:
            self.fail("_tw_cache_fresh 不應該在 naive/aware 混用時丟 TypeError")
        self.assertIsInstance(result, bool)

        # 反過來:fetched aware、now 用 None(內部會用 datetime.now(tz=TPE),
        # 一定是 aware)——確認也不炸,且極短間隔內視為新鮮(地板保底)。
        fetched_aware = datetime.now(tz=TPE)
        try:
            result2 = _tw_cache_fresh(fetched_aware, None)
        except TypeError:
            self.fail("_tw_cache_fresh(now=None) 不應該丟 TypeError")
        self.assertTrue(result2)  # 剛產生的時間戳,地板內必為新鮮


# =========================================================================== #
#  tw_eod_hour 防呆:_tw_eod_hour(工單 013 reviewer 修正包 P2-1)
# =========================================================================== #
class TwEodHourSanitizationPureFunctionTest(unittest.TestCase):
    """快取讀取路徑必須「永不往外炸」——`providers_cfg.tw_eod_hour` 若是非法值
    (型別轉換不了、或轉換得了但超出 0–23 時範圍),一律靜默回退預設 18,
    不能讓一個設定失誤把整個 fetch() 炸掉。"""

    def test_missing_key_defaults_to_18(self):
        self.assertEqual(providers._tw_eod_hour({}), 18)

    def test_none_value_falls_back_to_18(self):
        self.assertEqual(providers._tw_eod_hour({"tw_eod_hour": None}), 18)

    def test_non_numeric_string_falls_back_to_18(self):
        self.assertEqual(providers._tw_eod_hour({"tw_eod_hour": "18:00"}), 18)

    def test_out_of_range_25_falls_back_to_18(self):
        self.assertEqual(providers._tw_eod_hour({"tw_eod_hour": 25}), 18)

    def test_out_of_range_negative_falls_back_to_18(self):
        self.assertEqual(providers._tw_eod_hour({"tw_eod_hour": -1}), 18)

    def test_valid_boundary_values_are_respected(self):
        # 0 與 23 都是合法的 0–23 時範圍邊界,不該被防呆誤擋。
        self.assertEqual(providers._tw_eod_hour({"tw_eod_hour": 0}), 0)
        self.assertEqual(providers._tw_eod_hour({"tw_eod_hour": 23}), 23)

    def test_numeric_string_is_coerced_not_treated_as_illegal(self):
        # "20" 可以被 int() 成功轉換,屬合法輸入(不是防呆要擋的對象),應被尊重。
        self.assertEqual(providers._tw_eod_hour({"tw_eod_hour": "20"}), 20)


# =========================================================================== #
#  整合測試:fetch() 的 TW/US 快取讀取分支(離線 mock,沿用 005 的 mock 紀律)
# =========================================================================== #
class TwEodCacheFetchIntegrationTest(unittest.TestCase):
    """mock 紀律同 tests/test_providers_fallback.py(工單 005):
    1. CACHE_DIR 隔離到 tempdir(setUp 建立、tearDown 清除)。
    2. urlopen 保險絲:一被呼叫就 AssertionError,證明沒有真的連網。
    3. _http_get_json 逐條 mock,呼叫數斷言證明「用快取、沒打 API」。
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_eod_cache_")
        self._cache_patch = patch.object(providers, "CACHE_DIR", self._tmpdir)
        self._cache_patch.start()
        self._urlopen_patch = patch.object(
            providers.urllib.request,
            "urlopen",
            side_effect=AssertionError("real network! _http_get_json 應該被 mock 攔住"),
        )
        self._urlopen_patch.start()
        self._sleep_patch = patch.object(providers.time, "sleep")
        self._sleep_patch.start()
        # 環境變數快照(比照 tests/test_providers_fallback.py 的 ProvidersFallbackTestCase,
        # 工單 013 reviewer 修正包 P3-4):測試內 os.environ.pop("FINNHUB_API_KEY", ...)
        # 這類操作 tearDown 時自動還原,不汙染同 process 的後續測試或部署者 shell 環境。
        self._env_patch = patch.dict(os.environ)
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        self._sleep_patch.stop()
        self._urlopen_patch.stop()
        self._cache_patch.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _stock_cfg(self, ticker, market, name=""):
        return {"ticker": ticker, "market": market, "name": name or ticker}

    def _providers_cfg(self, **overrides):
        cfg = {"finmind_token": "", "finnhub_api_key": "", "cache_minutes": 15}
        cfg.update(overrides)
        return cfg

    def _write_cache_with_age(self, data: StockData, age: timedelta, now: datetime | None = None):
        """用 production 的 _save_cache 寫入(拿到正確格式的 blob),
        再把 `_fetched_at` 改成「(now 或真實現在) - age」的時間戳,模擬不同新舊
        程度的快取。`now` 預設 None → 用真實系統時間(UTC),沿用既有測試的
        行為;傳入 aware datetime(任何時區皆可,寫入時原樣 isoformat)則可搭配
        `patch.object(providers, "_now_tpe", return_value=now)` 做完全鎖定時鐘的
        確定性測試(工單 013 reviewer 修正包 P2-3)。"""
        providers._save_cache(data)
        path = providers._cache_path(data.market, data.ticker)
        import json
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
        base = now if now is not None else datetime.now(timezone.utc)
        stamp = base - age
        blob["_fetched_at"] = stamp.isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)

    def test_tw_cache_10min_old_is_cache_hit_zero_network(self):
        # 10 分鐘前的快取:一定 < 安全地板(cache_minutes=15),不管當下是否
        # 恰好跨過 18:00 邊界,地板都保證新鮮 → fetch() 應該直接回傳快取、
        # 完全不打 FinMind。
        cached = StockData(
            ticker="2330", market="TW", name="台積電", currency="TWD",
            price=1000.0, price_date="2026-01-07", source="FinMind",
        )
        self._write_cache_with_age(cached, timedelta(minutes=10))

        with patch.object(providers, "_http_get_json") as mock_http:
            result = providers.fetch(
                self._stock_cfg("2330", "TW", "台積電"),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )

        self.assertEqual(mock_http.call_count, 0)
        self.assertEqual(result.price, 1000.0)
        self.assertEqual(result.source, "FinMind")

    def test_tw_cache_4days_old_always_has_crossed_a_boundary_refetches(self):
        # 4 天(96h)前的快取:SPEC 明言 72h(週五18:00→週一18:00)是最大無邊界
        # 間隔,所以任何「now」下 4 天前一定至少跨過一個平日 18:00 邊界 →
        # 過期 → fetch() 必須重新打 FinMind(不能只回傳舊快取)。
        cached = StockData(
            ticker="2330", market="TW", name="台積電", currency="TWD",
            price=1000.0, price_date="2026-01-01", source="FinMind",
        )
        self._write_cache_with_age(cached, timedelta(days=4))

        def _price(url):
            return {"status": 200, "data": [
                {"date": "2026-01-08", "close": 1050.0},
            ]}

        with patch.object(providers, "_http_get_json", side_effect=_price) as mock_http:
            result = providers.fetch(
                self._stock_cfg("2330", "TW", "台積電"),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )

        self.assertEqual(mock_http.call_count, 1)  # price_band(method="") 只打 Price 一次
        self.assertEqual(result.price, 1050.0)  # 拿到的是重新抓的新資料,不是舊快取的 1000

    def test_tw_cache_3h_old_same_day_no_boundary_crossed_zero_network(self):
        """工單 013 reviewer 修正包 P2-3/P3-5:既有的「10 分鐘前」與「4 天前」
        兩個案例對「EOD-aware 誤退化回固定 15 分 TTL」這種突變沒有鑑別力
        ——10 分鐘前無論用哪種規則都在地板內必新鮮,4 天前無論用哪種規則都
        遠超 15 分/72h 必過期,兩種實作在這兩點上行為一致。

        這裡用 `_now_tpe` 把「現在」釘死在週三 13:00(台北),快取是同一天
        10:00 抓的(3 小時前、(10:00,13:00] 內沒有 18:00 邊界)——EOD-aware
        規則下應該新鮮、0 網路呼叫;但如果退化成固定 15 分 TTL,3 小時
        (180 分鐘)遠超 15 分,會誤判過期而重新打 FinMind、call_count 變 1,
        這裡的斷言就會翻紅,真正鑑別出這個突變(見 REPORT 的 mutation 重演)。
        """
        fake_now = datetime(2026, 1, 7, 13, 0, tzinfo=providers.TPE)  # 週三 13:00
        cached = StockData(
            ticker="2330", market="TW", name="台積電", currency="TWD",
            price=1000.0, price_date="2026-01-07", source="FinMind",
        )
        self._write_cache_with_age(cached, timedelta(hours=3), now=fake_now)

        with patch.object(providers, "_now_tpe", return_value=fake_now), \
             patch.object(providers, "_http_get_json") as mock_http:
            result = providers.fetch(
                self._stock_cfg("2330", "TW", "台積電"),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )

        self.assertEqual(mock_http.call_count, 0)
        self.assertEqual(result.price, 1000.0)

    def test_us_cache_still_uses_fixed_15min_ttl_regression(self):
        # US/INTL 完全不動:10 分鐘前的快取(< 15 分 TTL)仍是 cache hit。
        cached = StockData(
            ticker="AAPL", market="US", name="Apple", currency="USD",
            price=200.0, price_date="2026-01-07", source="yfinance",
        )
        self._write_cache_with_age(cached, timedelta(minutes=10))

        with patch.object(providers, "_http_get_json") as mock_http:
            result = providers.fetch(
                self._stock_cfg("AAPL", "US", "Apple"),
                self._providers_cfg(),
                history_years=1,
                use_cache=True,
            )

        self.assertEqual(mock_http.call_count, 0)
        self.assertEqual(result.price, 200.0)

    def test_us_cache_20min_old_exceeds_fixed_ttl_refetches(self):
        # US/INTL 完全不動:20 分鐘前的快取(> 15 分固定 TTL,沒有 EOD 邊界
        # 這種東西可言)必須視為過期、重新抓取——回歸鎖住「US 沒有被誤套用
        # TW 的 EOD-aware 規則」。用無 Finnhub 金鑰路徑直接測 yfinance 分支。
        #
        # 工單 013 reviewer 修正包 P2-3:改用 `_now_tpe` 釘死假時鐘,刻意避開
        # 18:00–18:20 這個「弱時段」——若有人不小心把 US 路徑也接到
        # `_tw_cache_fresh`(混用地板/邊界邏輯的錯誤合併),在這個時段跑測試
        # 剛好會因為靠近 18:00 邊界而「巧合」判成過期,跟正確實作的結果一樣,
        # 測試就測不出這個混用錯誤。釘死在週三 13:00(離任何 EOD 邊界都遠),
        # 讓斷言只反映「有沒有正確套用 15 分固定 TTL」,不受巧合影響。
        import sys
        fake_now = datetime(2026, 1, 7, 13, 0, tzinfo=providers.TPE)  # 週三 13:00,遠離 18:00 邊界
        cached = StockData(
            ticker="AAPL", market="US", name="Apple", currency="USD",
            price=200.0, price_date="2026-01-07", source="yfinance",
        )
        self._write_cache_with_age(cached, timedelta(minutes=20), now=fake_now)

        class _FakeTs:
            def __init__(self, s):
                self._s = s

            def strftime(self, fmt):
                return self._s

        class _FakeHist:
            def __init__(self, rows):
                self.index = [_FakeTs(d) for d, _ in rows]
                self._closes = [c for _, c in rows]

            def __len__(self):
                return len(self._closes)

            def __getitem__(self, key):
                return self._closes

        class _FakeTicker:
            def __init__(self, ticker):
                pass

            def history(self, period=None, auto_adjust=None):
                return _FakeHist([("2026-01-08", 205.0)])

            @property
            def info(self):
                return {}

        fake_yf = type("FakeYF", (), {})()
        fake_yf.Ticker = _FakeTicker

        os.environ.pop("FINNHUB_API_KEY", None)  # setUp 的 patch.dict(os.environ) 會自動還原
        with patch.object(providers, "_now_tpe", return_value=fake_now), \
             patch.object(providers, "_http_get_json") as mock_http, \
             patch.dict(sys.modules, {"yfinance": fake_yf}):
            result = providers.fetch(
                self._stock_cfg("AAPL", "US", "Apple"),
                self._providers_cfg(finnhub_api_key=""),
                history_years=1,
                use_cache=True,
            )

        self.assertEqual(mock_http.call_count, 0)  # 沒有金鑰,走 yfinance 不會打 _http_get_json
        self.assertEqual(result.price, 205.0)  # 拿到的是重新抓的新資料,不是舊快取的 200

    def test_illegal_tw_eod_hour_values_do_not_crash_and_behave_like_default_18(self):
        """工單 013 reviewer 修正包 P2-1:確認防呆真的接線到 `fetch()`,不只是
        `_tw_eod_hour` 這個純函數本身沒事(見上面的
        `TwEodHourSanitizationPureFunctionTest`)。用一個「用 eod_hour=18 判斷
        會是新鮮」的快取時間點(週三 19:00 抓、`_now_tpe` 釘死在隔天週四 10:00
        問,兩個 18:00 邊界都不落在 (fetched, now] 內)驗證三種非法值
        (None、"18:00"、25)都不炸例外、且都走到跟預設 18 一致的結果:
        新鮮、0 網路呼叫——證明防呆的回退值真的是 18,不是 fetch() 整個
        意外繞過 EOD 判斷或崩潰。"""
        fake_now = datetime(2026, 1, 8, 10, 0, tzinfo=providers.TPE)      # 週四 10:00
        fetched_at = datetime(2026, 1, 7, 19, 0, tzinfo=providers.TPE)   # 週三 19:00(當天邊界已過)

        for bad_value in (None, "18:00", 25):
            with self.subTest(tw_eod_hour=bad_value):
                cached = StockData(
                    ticker="2330", market="TW", name="台積電", currency="TWD",
                    price=1000.0, price_date="2026-01-07", source="FinMind",
                )
                self._write_cache_with_age(cached, fake_now - fetched_at, now=fake_now)

                with patch.object(providers, "_now_tpe", return_value=fake_now), \
                     patch.object(providers, "_http_get_json") as mock_http:
                    result = providers.fetch(
                        self._stock_cfg("2330", "TW", "台積電"),
                        self._providers_cfg(tw_eod_hour=bad_value),
                        history_years=5,
                        use_cache=True,
                    )

                self.assertEqual(mock_http.call_count, 0,
                                  f"tw_eod_hour={bad_value!r} 不應該打 FinMind(該回退成 18)")
                self.assertEqual(result.price, 1000.0)


if __name__ == "__main__":
    unittest.main()

"""缺日偵測 + 資料品質警告(工單 015)。完全離線,0 API。

涵蓋 SPEC 的三塊:
1. `_series_quality_warnings` 純函數表格(缺口/尾端過舊/邊界/短序列/未排序輸入)。
2. blob 相容 roundtrip:新欄位存讀、舊 blob(無 `quality_warnings` 鍵)不炸(dataclass
   default_factory)、模擬「多一個現在 dataclass 沒有的鍵」(新舊版互讀情境,或未來
   欄位)不炸(`_load_cache_raw` 過濾未知鍵)。
3. 顯示:`report.py::render_stock_card` 對含警告的 stub item 印出黃色列(比照
   `tests/test_report_roi_display.py` 的 `HAS_RICH=False` + `redirect_stdout` 手法做
   內容驗證;另外用假 Console 攔截 `.print()` 收到的原始字串,驗證真的帶
   `[yellow]...[/yellow]` 標記,滿足 SPEC「以黃色列出」的要求)。

額外補一組 `fetch_tw`/`fetch_us` 端對端(mock 網路)接線測試,確認不只是純函數與
blob 機制各自獨立正確,實際组裝路徑真的有把 `quality_warnings` 填進去
(mock 紀律同 tests/test_providers_fallback.py:CACHE_DIR 隔離到 tempdir、
urlopen 保險絲、_http_get_json 逐條 mock)。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from aimonitor import providers, report
from aimonitor.providers import StockData, TPE, _series_quality_warnings


# =========================================================================== #
#  純函數表格:_series_quality_warnings
# =========================================================================== #
class SeriesQualityWarningsPureFunctionTest(unittest.TestCase):
    def _tpe_now(self, y, m, d):
        return datetime(y, m, d, 12, 0, tzinfo=TPE)

    def test_fewer_than_two_points_never_checked(self):
        self.assertEqual(_series_quality_warnings([]), [])
        self.assertEqual(_series_quality_warnings([("2026-01-01", 100.0)]), [])

    def test_normal_weekend_gap_no_warning(self):
        hist = [("2026-02-06", 100.0), ("2026-02-09", 101.0)]  # 3 天,一般週末
        self.assertEqual(_series_quality_warnings(hist, now=self._tpe_now(2026, 2, 9)), [])

    def test_nine_day_holiday_no_false_positive(self):
        # 模擬台股連假(例如春節):9 天 < 12 天門檻,不該誤報。
        hist = [("2026-02-01", 100.0), ("2026-02-10", 101.0)]
        self.assertEqual(_series_quality_warnings(hist, now=self._tpe_now(2026, 2, 10)), [])

    def test_gap_exactly_at_threshold_boundary_no_warning(self):
        # 剛好 12 天(SPEC:> 12 才警告,== 12 不算)。
        hist = [("2026-02-01", 100.0), ("2026-02-13", 101.0)]
        self.assertEqual(_series_quality_warnings(hist, now=self._tpe_now(2026, 2, 13)), [])

    def test_gap_just_above_threshold_warns(self):
        hist = [("2026-02-01", 100.0), ("2026-02-14", 101.0)]  # 13 天
        out = _series_quality_warnings(hist, now=self._tpe_now(2026, 2, 14))
        self.assertEqual(len(out), 1)

    def test_fifteen_day_hole_warns_with_dates_and_neutral_wording(self):
        hist = [("2026-02-01", 100.0), ("2026-02-16", 101.0)]  # 15 天
        out = _series_quality_warnings(hist, now=self._tpe_now(2026, 2, 16))
        self.assertEqual(len(out), 1)
        self.assertIn("2026-02-01", out[0])
        self.assertIn("2026-02-16", out[0])
        self.assertIn("15", out[0])
        self.assertIn("百分位", out[0])
        self.assertIn("波動率", out[0])
        self.assertIn("偏差", out[0])
        # 語氣中性、非投資建議:不該出現任何買賣字眼。
        for bad in ("買進", "賣出", "建議買", "建議賣"):
            self.assertNotIn(bad, out[0])

    def test_unsorted_input_still_detects_gap_correctly(self):
        # price_history 不假設已排序;故意打亂順序輸入。
        hist = [("2026-02-16", 101.0), ("2026-02-01", 100.0)]
        out = _series_quality_warnings(hist, now=self._tpe_now(2026, 2, 16))
        self.assertEqual(len(out), 1)
        self.assertIn("2026-02-01", out[0])
        self.assertIn("2026-02-16", out[0])

    def test_stale_tail_exactly_at_boundary_no_warning(self):
        hist = [("2026-01-01", 100.0), ("2026-01-02", 101.0)]
        # now = 最後一筆 + 10 天(SPEC:> 10 才警告,== 10 不算)。
        out = _series_quality_warnings(hist, now=self._tpe_now(2026, 1, 12))
        self.assertEqual(out, [])

    def test_stale_tail_just_above_boundary_warns(self):
        hist = [("2026-01-01", 100.0), ("2026-01-02", 101.0)]
        out = _series_quality_warnings(hist, now=self._tpe_now(2026, 1, 13))  # 11 天
        self.assertEqual(len(out), 1)
        self.assertIn("2026-01-02", out[0])
        self.assertIn("百分位", out[0])
        self.assertIn("波動率", out[0])

    def test_stale_tail_with_fake_clock_far_future_warns(self):
        hist = [("2026-01-01", 100.0), ("2026-01-02", 101.0)]
        out = _series_quality_warnings(hist, now=self._tpe_now(2026, 1, 21))  # 19 天
        self.assertEqual(len(out), 1)

    def test_default_now_uses_now_tpe_when_not_injected(self):
        # 沒傳 now 時應該用 _now_tpe()(可注入的時鐘),不是繞過去直接用別的時間源。
        fake_now = self._tpe_now(2026, 1, 21)
        hist = [("2026-01-01", 100.0), ("2026-01-02", 101.0)]
        with patch.object(providers, "_now_tpe", return_value=fake_now):
            out = providers._series_quality_warnings(hist)
        self.assertEqual(len(out), 1)

    def test_no_gap_and_fresh_tail_returns_empty(self):
        hist = [("2026-01-01", 100.0), ("2026-01-02", 101.0), ("2026-01-05", 102.0)]
        out = _series_quality_warnings(hist, now=self._tpe_now(2026, 1, 6))
        self.assertEqual(out, [])

    def test_multiple_gaps_each_produce_own_warning(self):
        hist = [
            ("2026-01-01", 100.0),
            ("2026-01-20", 101.0),   # 缺口 19 天
            ("2026-02-10", 102.0),   # 缺口 21 天
        ]
        out = _series_quality_warnings(hist, now=self._tpe_now(2026, 2, 10))
        self.assertEqual(len(out), 2)


# =========================================================================== #
#  blob 相容 roundtrip:_load_cache_raw / _save_cache
# =========================================================================== #
class BlobCompatibilityRoundtripTest(unittest.TestCase):
    """mock 紀律同 tests/test_eod_cache.py:CACHE_DIR 隔離到 tempdir。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_quality_blob_")
        self._cache_patch = patch.object(providers, "CACHE_DIR", self._tmpdir)
        self._cache_patch.start()

    def tearDown(self):
        self._cache_patch.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_new_blob_roundtrip_preserves_quality_warnings(self):
        data = StockData(
            ticker="2330", market="TW", name="台積電", currency="TWD",
            price=600.0, price_date="2026-01-01", source="FinMind",
            quality_warnings=["資料缺口:2026-01-01 ~ 2026-01-20(相差 19 天)"],
        )
        providers._save_cache(data)

        loaded = providers._load_cache_raw("TW", "2330")
        self.assertIsNotNone(loaded)
        loaded_data, _fetched_at = loaded
        self.assertEqual(loaded_data.quality_warnings,
                          ["資料缺口:2026-01-01 ~ 2026-01-20(相差 19 天)"])
        self.assertEqual(loaded_data.price, 600.0)

    def test_old_blob_missing_quality_warnings_key_loads_with_default_empty_list(self):
        # 模擬 015 之前寫入的舊快取檔:blob 裡完全沒有 quality_warnings 這個鍵。
        data = StockData(
            ticker="2330", market="TW", name="台積電", currency="TWD",
            price=600.0, price_date="2026-01-01", source="FinMind",
        )
        blob = asdict(data)
        del blob["quality_warnings"]  # 舊版 blob 缺這個鍵
        blob["_fetched_at"] = datetime.now(timezone.utc).isoformat()
        path = providers._cache_path("TW", "2330")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)

        loaded = providers._load_cache_raw("TW", "2330")
        self.assertIsNotNone(loaded)  # 不炸、不被吞成 None
        loaded_data, _fetched_at = loaded
        self.assertEqual(loaded_data.quality_warnings, [])  # default_factory 補上
        self.assertEqual(loaded_data.price, 600.0)  # 其餘欄位正常讀回

    def test_blob_with_unknown_future_key_is_filtered_and_does_not_crash(self):
        # 模擬「blob 裡有現在這版 StockData 沒有的鍵」——可能是新版多寫的欄位被
        # 舊版程式讀到,也可能是未來欄位。驗證 _load_cache_raw 有過濾未知鍵,
        # 不會被 StockData(**blob) 的 TypeError 炸掉、也不會被外層 try/except
        # 靜默吞成 None(那樣會造成每次都當成快取不存在、白白多打一次 API)。
        data = StockData(
            ticker="AAPL", market="US", name="Apple", currency="USD",
            price=200.0, price_date="2026-01-01", source="yfinance",
            quality_warnings=["尾端過舊"],
        )
        blob = asdict(data)
        blob["some_future_field"] = "unexpected-value-from-a-newer-schema"
        blob["_fetched_at"] = datetime.now(timezone.utc).isoformat()
        path = providers._cache_path("US", "AAPL")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)

        loaded = providers._load_cache_raw("US", "AAPL")
        self.assertIsNotNone(loaded)
        loaded_data, _fetched_at = loaded
        self.assertEqual(loaded_data.price, 200.0)
        self.assertEqual(loaded_data.quality_warnings, ["尾端過舊"])
        self.assertFalse(hasattr(loaded_data, "some_future_field"))

    def test_raw_stockdata_construction_rejects_unknown_kwarg_without_filter(self):
        # 對照組(鎖住「先驗證」的診斷結論本身):直接對 StockData 建構子丟一個
        # 未知鍵,證明「多鍵 TypeError」這件事是 dataclass 的真實行為,不是臆測
        # ——_load_cache_raw 的過濾邏輯是必要的,不是防禦性過度工程。
        with self.assertRaises(TypeError):
            StockData(ticker="2330", market="TW", unknown_field_xyz="boom")


# =========================================================================== #
#  端對端接線:fetch_tw / fetch_us 真的有把 quality_warnings 填進去
# =========================================================================== #
class FetchWiringQualityWarningsTest(unittest.TestCase):
    """mock 紀律同 tests/test_providers_fallback.py。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_quality_fetch_")
        self._cache_patch = patch.object(providers, "CACHE_DIR", self._tmpdir)
        self._cache_patch.start()
        self._urlopen_patch = patch.object(
            providers.urllib.request, "urlopen",
            side_effect=AssertionError("real network! _http_get_json 應該被 mock 攔住"),
        )
        self._urlopen_patch.start()
        self._sleep_patch = patch.object(providers.time, "sleep")
        self._sleep_patch.start()
        self._env_patch = patch.dict(os.environ)
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        self._sleep_patch.stop()
        self._urlopen_patch.stop()
        self._cache_patch.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_fetch_tw_wires_quality_warnings_on_price_gap(self):
        fake_now = datetime(2026, 1, 20, 12, 0, tzinfo=providers.TPE)  # 貼齊最後一筆,只測缺口

        def _price(url):
            return {"status": 200, "data": [
                {"date": "2026-01-01", "close": 600.0},
                {"date": "2026-01-20", "close": 610.0},   # 19 天缺口
            ]}

        with patch.object(providers, "_now_tpe", return_value=fake_now), \
             patch.object(providers, "_http_get_json", side_effect=_price):
            result = providers.fetch(
                {"ticker": "2330", "market": "TW", "name": "台積電"},
                {"finmind_token": "", "finnhub_api_key": "", "cache_minutes": 15},
                history_years=5,
                use_cache=True,
            )

        self.assertTrue(result.ok())
        self.assertEqual(len(result.quality_warnings), 1)
        self.assertIn("2026-01-01", result.quality_warnings[0])
        self.assertIn("2026-01-20", result.quality_warnings[0])

    def test_fetch_us_wires_quality_warnings_on_price_gap(self):
        fake_now = datetime(2026, 1, 20, 12, 0, tzinfo=providers.TPE)

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
                return _FakeHist([("2026-01-01", 100.0), ("2026-01-20", 105.0)])  # 19 天缺口

            @property
            def info(self):
                return {}

        fake_yf = type("FakeYF", (), {})()
        fake_yf.Ticker = _FakeTicker

        os.environ.pop("FINNHUB_API_KEY", None)
        with patch.object(providers, "_now_tpe", return_value=fake_now), \
             patch.object(providers, "_http_get_json") as mock_http, \
             patch.dict(sys.modules, {"yfinance": fake_yf}):
            result = providers.fetch(
                {"ticker": "AAPL", "market": "US", "name": "Apple"},
                {"finmind_token": "", "finnhub_api_key": "", "cache_minutes": 15},
                history_years=1,
                use_cache=True,
            )

        self.assertEqual(mock_http.call_count, 0)  # 無 Finnhub 金鑰,純 yfinance 路徑
        self.assertTrue(result.ok())
        self.assertEqual(len(result.quality_warnings), 1)
        self.assertIn("2026-01-01", result.quality_warnings[0])
        self.assertIn("2026-01-20", result.quality_warnings[0])

    def test_fetch_tw_stale_cache_rescue_carries_forward_old_warnings(self):
        """quality_warnings 隨 blob 存讀,stale-rescue 路徑帶的是「舊資料當時算出的
        舊警告」——這是合理的(那份 price_history 本來就是舊的,警告理應同樣是舊
        的,不會無中生有變出一份「即時重算」的新警告)。這裡驗證 fetch() 全源失敗
        時撈到的過期快取,quality_warnings 原樣跟著過期快取一起回來。"""
        stale = StockData(
            ticker="2330", market="TW", name="台積電", currency="TWD",
            price=600.0, price_date="2026-01-01", source="FinMind",
            quality_warnings=["資料缺口:2025-12-01 ~ 2025-12-20(相差 19 天)"],
        )
        providers._save_cache(stale)
        path = providers._cache_path("TW", "2330")
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
        # 手動往回改成 2 天前(遠超 15 分地板,且足以跨過至少一個平日邊界視為過期)。
        blob["_fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)

        with patch.object(providers, "_http_get_json",
                           side_effect=RuntimeError("FinMind 掛了(fake)")):
            result = providers.fetch(
                {"ticker": "2330", "market": "TW", "name": "台積電"},
                {"finmind_token": "", "finnhub_api_key": "", "cache_minutes": 15},
                history_years=5,
                use_cache=True,
            )

        self.assertTrue(result.ok())
        self.assertIn("(過期快取)", result.source)
        self.assertEqual(result.quality_warnings,
                          ["資料缺口:2025-12-01 ~ 2025-12-20(相差 19 天)"])


# =========================================================================== #
#  顯示:report.py::render_stock_card
# =========================================================================== #
def _make_item(quality_warnings):
    cfg = {"ticker": "2330", "market": "TW", "name": "台積電"}
    data = StockData(
        ticker="2330", market="TW", name="台積電", currency="TWD",
        price=600.0, price_date="2026-01-01", source="FinMind",
        quality_warnings=quality_warnings,
    )
    zones = {
        "zones": {"super_bargain": 400.0, "cheap": 500.0, "fair": 600.0,
                  "expensive": 700.0, "euphoria": 800.0},
        "anchor": None, "anchor_kind": None, "assumptions": "測試假設",
        "implied_multiple": None, "warnings": [],
    }
    analysis = {
        "region": "合理價區", "is_buy": False, "drop_to_cheap_pct": 10.0,
        "annual_vol_pct": None, "prob_hit_cheap": {}, "price_percentile": None,
    }
    return {"cfg": cfg, "data": data, "zones": zones, "analysis": analysis, "error": ""}


class RenderStockCardQualityWarningsContentTest(unittest.TestCase):
    """比照 tests/test_report_roi_display.py 手法:HAS_RICH=False 逼 _p 走
    print(...) 分支,用 redirect_stdout 擷取純文字內容斷言。"""

    def _render(self, item):
        buf = io.StringIO()
        with patch.object(report, "HAS_RICH", False):
            with contextlib.redirect_stdout(buf):
                report.render_stock_card(item, config={})
        return buf.getvalue()

    def test_quality_warning_text_is_shown(self):
        item = _make_item(["資料缺口:2026-01-01 ~ 2026-01-20(相差 19 天),期間百分位/波動率可能因此偏差。"])
        out = self._render(item)
        self.assertIn("資料品質", out)
        self.assertIn("資料缺口:2026-01-01 ~ 2026-01-20", out)

    def test_no_quality_warnings_prints_nothing_extra(self):
        item = _make_item([])
        out = self._render(item)
        self.assertNotIn("資料品質", out)

    def test_multiple_quality_warnings_each_on_own_line(self):
        item = _make_item(["缺口A", "缺口B"])
        out = self._render(item)
        self.assertEqual(out.count("資料品質"), 2)


class RenderStockCardQualityWarningsYellowMarkupTest(unittest.TestCase):
    """驗證 SPEC 要求的「黃色列出」:用假 Console 攔截傳給 `.print()` 的原始字串
    (rich markup 尚未被解析成實際顏色前),確認確實帶 `[yellow]...[/yellow]` 標記。"""

    def test_quality_warning_line_uses_yellow_markup(self):
        item = _make_item(["缺口警告內容"])
        fake_console = MagicMock()
        with patch.object(report, "HAS_RICH", True), \
             patch.object(report, "_C", fake_console):
            report.render_stock_card(item, config={})

        printed = [c.args[0] for c in fake_console.print.call_args_list if c.args]
        matches = [s for s in printed if "缺口警告內容" in str(s)]
        self.assertEqual(len(matches), 1)
        self.assertIn("[yellow]", matches[0])
        self.assertIn("[/yellow]", matches[0])


if __name__ == "__main__":
    unittest.main()

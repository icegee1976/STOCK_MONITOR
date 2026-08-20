"""台股第二資料源備援:TWSE(上市)/ TPEx(上櫃)官方 OpenAPI(工單 017)。

背景:降級鏈原本不對稱——美股有 Finnhub→yfinance→過期快取三層,台股只有
FinMind 單源(掛了只剩過期快取)。這裡在 `fetch_tw` 的 FinMind price 例外分支
加上 TWSE/TPEx 官方備援(免金鑰、一次呼叫回全市場當日行情,process 內
memoize),FinMind 成功路徑完全不觸碰。完全離線、0 API 呼叫。

實測 schema(2026-08-19,一次性各打 1 次真實呼叫,SPEC 明文允許,詳見工單
017 REPORT;以下 mock 完全依此形狀建構,不臆測):
- TWSE `openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`:回應是
  list[dict](**沒有**外層 {"status":...,"data":[...]} 包裝,跟 FinMind 不同,
  直接是陣列),鍵 Date(民國年 "1150818" 無分隔,= 2026-08-18)/Code/Name/
  ClosingPrice(乾淨數字字串,無千分位逗號,實測 1378 檔無一例外)/
  OpeningPrice/HighestPrice/LowestPrice/TradeVolume/TradeValue/Change/
  Transaction。
- TPEx `www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes`:同樣是
  list[dict](10000+ 列,含個股/ETF/債券),鍵 Date(格式同上)/
  SecuritiesCompanyCode/CompanyName/Close(同樣無逗號;**當日無成交的列
  Close 會是 " ---" 這種帶前導空白的佔位字串**,實測真實存在超過 5000 列,
  必須防呆跳過,不能誤當 0 元)/Open/High/Low/Average/TradingShares/...。
  數值字串一律經 `_tw_official_num` 統一防呆(去逗號/空白,"---" 等佔位符
  → None)。逗號千分位案例雖非這兩個端點實測所見,但 SPEC 明文要求驗證這個
  防呆(官方資料源在其他資料集/未來格式調整仍可能出現),下面另外構造合成
  案例覆蓋。

mock 紀律沿用工單 005(tests/test_providers_fallback.py)/013/014/015:
1. CACHE_DIR 隔離到 tempdir。
2. urlopen 保險絲(真的打到網路就 AssertionError)。
3. `_http_get_json` 逐條 mock(URL 子字串路由),呼叫數斷言證明沒有偷打/多打。
4. `time.sleep` no-op。
5. 環境變數快照(patch.dict(os.environ))。
刻意不 import test_providers_fallback/test_history_store,獨立寫一份等價基底
(比照工單 014 的既有決定,避免測試檔互相耦合)。

額外(本工單新增的模組層可變狀態):
6. `providers._TWSE_SNAPSHOT_MEMO` / `_TPEX_SNAPSHOT_MEMO` 是 process 記憶體
   memo(模組層 global dict),共用測試基底 `setUp`/`tearDown` 呼叫
   `providers._reset_snapshot_memos()`(工單 017 reviewer 修正包 G7 新增的
   測試輔助)重置,避免測試之間互相汙染。**注意**:這是 process 全域狀態,
   任何其他測試檔只要會驅動 `fetch_tw` 的 FinMind 失敗分支(即使目標端點也被
   mock 成失敗),就有可能寫入這兩個 module-level dict;若那些測試檔沒有自行
   呼叫 `_reset_snapshot_memos()`,理論上可能受這裡的殘留狀態影響,或反過來
   汙染它們——目前其餘既有測試檔對此免疫(不斷言 memo 相關行為),但未來
   新增這類測試檔時請務必留意並自行重置。

REVIEWER 修正包(工單 017,G1–G8;三條 P1 真實重現後仲裁):
- G1a:`_assemble_tw_fallback` 歷史來源改「store 優先,空/不可用退回舊 blob」
  (`_load_cache_raw` 讀取,blob 已是還原後序列,不可再跑 `_back_adjust_tw`)。
- G1b:備援結果(`source` 含 "(備援)")不寫入 blob,避免降級快照蓋掉完整
  FinMind blob、被 EOD 新鮮度釘住。
- G2:官方現價與本地歷史尾端比值 <0.6 或 >1.7(拆股門檻同款數字)→ 整組捨棄
  三個歷史序列 + quality_warnings 告知,防止尺度脫鉤產生假訊號。
- G3:`_market_snapshot` 加負向 memo(15 分鐘冷卻)+ 空/非 dict 視為失敗 +
  `_http_get_json` 帶 `timeout=10`,全故障時整輪只有第一檔付探測成本。
- G6:`fetch_tw` 內兩個 fallback 查詢呼叫外層再包一層 try/except(防禦性)。
- G7:新增 `providers._reset_snapshot_memos()` 測試輔助,本檔 setUp 呼叫。
- G8:`_tw_official_num` 拒絕 inf/nan;`_roc_date_to_iso` 只接受 ASCII 數字
  (拒絕全形)。
以下多數既有測試因應調整(尤其 blob 相關斷言);新增測試涵蓋 G2/G3/G4。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from aimonitor import providers, history_store
from aimonitor.providers import StockData, TPE


# --------------------------------------------------------------------------- #
#  共用 helper
# --------------------------------------------------------------------------- #
def _http_get_json_router(routes):
    """建一個 side_effect callable,依 URL 子字串分流到不同 handler(比照工單
    005 tests/test_providers_fallback.py 的 `_http_get_json_router`,獨立複製
    一份避免測試檔互相耦合)。沒有任何 route 匹配的 URL 一律 AssertionError,
    證明沒有意料外的呼叫漏網。"""
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


def _twse_row(code, close, roc_date="1150818", name="測試股"):
    """依實測 TWSE STOCK_DAY_ALL 欄位形狀構造一列。"""
    return {
        "Date": roc_date, "Code": code, "Name": name,
        "TradeVolume": "1000", "TradeValue": "1000000",
        "OpeningPrice": close, "HighestPrice": close, "LowestPrice": close,
        "ClosingPrice": close, "Change": "0.0000", "Transaction": "10",
    }


def _tpex_row(code, close, roc_date="1150818", name="測試股"):
    """依實測 TPEx tpex_mainboard_daily_close_quotes 欄位形狀構造一列。"""
    return {
        "Date": roc_date, "SecuritiesCompanyCode": code, "CompanyName": name,
        "Close": close, "Change": "0.00", "Open": close, "High": close, "Low": close,
        "Average": close, "TradingShares": "1000", "TransactionAmount": "1000000",
        "TransactionNumber": "10", "LatestBidPrice": close, "LatesAskPrice": close,
        "Capitals": "1000000", "NextReferencePrice": close,
        "NextLimitUp": close, "NextLimitDown": close,
    }


# =========================================================================== #
#  純函數:_tw_official_num(官方數值字串防呆)
# =========================================================================== #
class TwOfficialNumParsingPureFunctionTest(unittest.TestCase):
    def test_plain_decimal_string(self):
        self.assertEqual(providers._tw_official_num("1088.00"), 1088.0)

    def test_comma_thousands_separator_stripped(self):
        # 實測兩端點皆無此案例,但 SPEC 明文要求驗證這個防呆(見檔頭說明)。
        self.assertEqual(providers._tw_official_num("1,088.00"), 1088.0)

    def test_leading_trailing_whitespace_stripped(self):
        self.assertEqual(providers._tw_official_num(" 45.50 "), 45.5)

    def test_placeholder_dashes_return_none(self):
        # 實測 TPEx 當日無成交列的真實格式(前導空白 + "---")。
        self.assertIsNone(providers._tw_official_num(" ---"))
        self.assertIsNone(providers._tw_official_num("--"))
        self.assertIsNone(providers._tw_official_num(""))

    def test_none_input_returns_none(self):
        self.assertIsNone(providers._tw_official_num(None))

    def test_zero_or_negative_treated_as_no_data(self):
        self.assertIsNone(providers._tw_official_num("0"))
        self.assertIsNone(providers._tw_official_num("-5"))

    def test_non_numeric_garbage_returns_none(self):
        self.assertIsNone(providers._tw_official_num("N/A"))

    def test_g8_infinity_and_nan_strings_return_none(self):
        # 工單 017 reviewer 修正包 G8:`float()` 能成功解析 "inf"/"nan" 這種
        # 字串(不會走進 ValueError 分支),必須額外用 math.isfinite 擋下來。
        self.assertIsNone(providers._tw_official_num("inf"))
        self.assertIsNone(providers._tw_official_num("-inf"))
        self.assertIsNone(providers._tw_official_num("nan"))
        self.assertIsNone(providers._tw_official_num("Infinity"))


# =========================================================================== #
#  純函數:_roc_date_to_iso(民國年日期轉換)
# =========================================================================== #
class RocDateToIsoPureFunctionTest(unittest.TestCase):
    def test_seven_digit_no_separator(self):
        # 實測格式(TWSE/TPEx 皆同):"1150818" = 民國115年08月18日。
        self.assertEqual(providers._roc_date_to_iso("1150818"), "2026-08-18")

    def test_slash_separated_variant_also_supported(self):
        self.assertEqual(providers._roc_date_to_iso("115/08/18"), "2026-08-18")

    def test_six_digit_two_digit_roc_year(self):
        self.assertEqual(providers._roc_date_to_iso("990101"), "2010-01-01")

    def test_empty_or_none_returns_none(self):
        self.assertIsNone(providers._roc_date_to_iso(""))
        self.assertIsNone(providers._roc_date_to_iso(None))

    def test_garbage_format_returns_none(self):
        self.assertIsNone(providers._roc_date_to_iso("not-a-date"))

    def test_invalid_calendar_date_returns_none(self):
        self.assertIsNone(providers._roc_date_to_iso("1150230"))  # 2月30日不存在

    def test_g8_fullwidth_digits_are_rejected_not_silently_parsed(self):
        # 工單 017 reviewer 修正包 G8:`str.isdigit()` 對全形數字也回傳 True、
        # `int()` 也吃得下全形數字,兩者疊加會讓全形輸入被「意外正確」解析;
        # 改成只接受 ASCII 數字後,全形輸入應該被拒絕(回傳 None)。
        self.assertIsNone(providers._roc_date_to_iso("１１５０８１８"))  # 全形版 "1150818"
        self.assertIsNone(providers._roc_date_to_iso("１１５/０８/１８"))  # 全形版 "115/08/18"

    def test_g8_single_digit_month_day_known_limitation_returns_none(self):
        # 已知取捨(docstring 記錄,不修正):"115/8/18" 濾掉分隔符後只剩 6 碼,
        # 會被誤判成 2 位民國年格式,算出的 month/day 超出合法範圍,安全失敗
        # 回傳 None(不會靜默算出看似合理但錯誤的日期)。
        self.assertIsNone(providers._roc_date_to_iso("115/8/18"))


# =========================================================================== #
#  純函數:_parse_twse_snapshot / _parse_tpex_snapshot
# =========================================================================== #
class ParseSnapshotPureFunctionTest(unittest.TestCase):
    def test_twse_non_list_input_returns_empty_dict(self):
        self.assertEqual(providers._parse_twse_snapshot({"not": "a list"}), {})

    def test_twse_skips_row_missing_required_keys(self):
        self.assertEqual(providers._parse_twse_snapshot([{"Code": "2330"}]), {})

    def test_twse_skips_placeholder_close_row_keeps_valid_ones(self):
        raw = [
            {"Code": "1111", "ClosingPrice": " ---", "Date": "1150818"},
            {"Code": "2330", "ClosingPrice": "1088.00", "Date": "1150818"},
        ]
        out = providers._parse_twse_snapshot(raw)
        self.assertNotIn("1111", out)
        self.assertEqual(out["2330"], (1088.0, "2026-08-18"))

    def test_tpex_placeholder_close_row_skipped_real_world_case(self):
        # 實測真實案例(當日無成交):Close=" ---"。
        raw = [
            {"SecuritiesCompanyCode": "020001", "Close": " ---", "Date": "1150819"},
            {"SecuritiesCompanyCode": "5388", "Close": "45.50", "Date": "1150819"},
        ]
        out = providers._parse_tpex_snapshot(raw)
        self.assertNotIn("020001", out)
        self.assertEqual(out["5388"], (45.5, "2026-08-19"))


# =========================================================================== #
#  _market_snapshot:process 記憶體 memo 的 get-or-fetch(可注入時鐘)
# =========================================================================== #
class MarketSnapshotMemoPureFunctionTest(unittest.TestCase):
    """直接測 `_market_snapshot`(不經 fetch_tw),精準鎖住 memo 本身的行為,
    `now` 直接傳參注入,不需要 patch `_now_tpe`。memo 用區域變數(不是模組層
    global),測試之間天然隔離,不需要額外的 patch.object 清理。"""

    def test_fetches_once_and_reuses_within_eod_boundary(self):
        memo = {"data": None, "fetched_at": None, "failed_at": None}
        calls = {"n": 0}

        def _fake_http(url, timeout=25):
            calls["n"] += 1
            return [{"Code": "2330", "ClosingPrice": "1000.00", "Date": "1150818"}]

        t1 = datetime(2026, 8, 18, 10, 0, tzinfo=TPE)
        t2 = datetime(2026, 8, 18, 14, 0, tzinfo=TPE)  # 同日,未到 18:00 邊界

        with patch.object(providers, "_http_get_json", side_effect=_fake_http):
            data1 = providers._market_snapshot(memo, "http://fake", providers._parse_twse_snapshot, t1)
            data2 = providers._market_snapshot(memo, "http://fake", providers._parse_twse_snapshot, t2)

        self.assertEqual(calls["n"], 1)
        self.assertEqual(data1, data2)
        self.assertEqual(data1["2330"], (1000.0, "2026-08-18"))

    def test_refetches_after_crossing_eod_boundary(self):
        memo = {"data": None, "fetched_at": None, "failed_at": None}
        calls = {"n": 0}

        def _fake_http(url, timeout=25):
            calls["n"] += 1
            return [{"Code": "2330", "ClosingPrice": "1000.00", "Date": "1150818"}]

        t1 = datetime(2026, 8, 18, 17, 0, tzinfo=TPE)
        t2 = datetime(2026, 8, 18, 19, 0, tzinfo=TPE)  # 跨過當日 18:00 邊界

        with patch.object(providers, "_http_get_json", side_effect=_fake_http):
            providers._market_snapshot(memo, "http://fake", providers._parse_twse_snapshot, t1)
            providers._market_snapshot(memo, "http://fake", providers._parse_twse_snapshot, t2)

        self.assertEqual(calls["n"], 2)

    def test_fetch_failure_preserves_old_memo(self):
        memo = {"data": {"2330": (1000.0, "2026-08-18")},
                "fetched_at": datetime(2026, 8, 18, 10, 0, tzinfo=TPE), "failed_at": None}

        def _fake_http_fail(url, timeout=25):
            raise RuntimeError("network down (fake)")

        t2 = datetime(2026, 8, 18, 19, 0, tzinfo=TPE)  # 跨邊界,理應嘗試重打,但這次失敗
        with patch.object(providers, "_http_get_json", side_effect=_fake_http_fail):
            data = providers._market_snapshot(memo, "http://fake", providers._parse_twse_snapshot, t2)

        self.assertEqual(data, {"2330": (1000.0, "2026-08-18")})  # 舊資料原樣保留,不清空
        self.assertIsNotNone(memo["failed_at"])  # G3b:失敗留下負向記號,供冷卻窗口判斷

    def test_g3a_empty_dict_result_treated_as_failure_not_cached_as_success(self):
        """工單 017 reviewer 修正包 G3a:抓取成功但解析結果是空 dict(例如端點回
        `{"code": 500}` 這種非預期形狀,`_parse_twse_snapshot` 對非 list 輸入會
        回傳 `{}`)時,不可被記成「成功」memo——否則當天接下來的呼叫都會回傳
        這個空結果,不會再嘗試。"""
        memo = {"data": None, "fetched_at": None, "failed_at": None}
        calls = {"n": 0}

        def _fake_http_garbage(url, timeout=25):
            calls["n"] += 1
            return {"code": 500}   # 非 list,parse_fn 會回傳 {}

        t1 = datetime(2026, 8, 18, 10, 0, tzinfo=TPE)
        with patch.object(providers, "_http_get_json", side_effect=_fake_http_garbage):
            data = providers._market_snapshot(memo, "http://fake", providers._parse_twse_snapshot, t1)

        self.assertIsNone(data)          # 沒有可用結果
        self.assertIsNone(memo["data"])  # 沒有被寫成「成功」memo
        self.assertIsNotNone(memo["failed_at"])  # 有記下負向 memo
        self.assertEqual(calls["n"], 1)

    def test_g3b_negative_memo_suppresses_retry_within_cooldown_then_retries_after(self):
        """工單 017 reviewer 修正包 G3b:失敗後 15 分鐘內不重打;超過 15 分鐘
        (且仍在同一個 EOD 邊界內,避免跟 G3 的成功新鮮度規則糾纏)才重打。"""
        memo = {"data": None, "fetched_at": None, "failed_at": None}
        calls = {"n": 0}

        def _fake_http_fail(url, timeout=25):
            calls["n"] += 1
            raise RuntimeError("down (fake)")

        t1 = datetime(2026, 8, 18, 10, 0, tzinfo=TPE)
        t2 = datetime(2026, 8, 18, 10, 10, tzinfo=TPE)   # 10 分鐘後,仍在冷卻窗口內
        t3 = datetime(2026, 8, 18, 10, 20, tzinfo=TPE)   # 20 分鐘後,冷卻窗口已過

        with patch.object(providers, "_http_get_json", side_effect=_fake_http_fail):
            providers._market_snapshot(memo, "http://fake", providers._parse_twse_snapshot, t1)
            providers._market_snapshot(memo, "http://fake", providers._parse_twse_snapshot, t2)
            providers._market_snapshot(memo, "http://fake", providers._parse_twse_snapshot, t3)

        self.assertEqual(calls["n"], 2)  # t1(第一次探測)+ t3(冷卻過後重打),t2 被短路

    def test_g3c_http_get_json_called_with_timeout_10(self):
        memo = {"data": None, "fetched_at": None, "failed_at": None}
        seen = {}

        def _fake_http(url, timeout=25):
            seen["timeout"] = timeout
            return [{"Code": "2330", "ClosingPrice": "1000.00", "Date": "1150818"}]

        with patch.object(providers, "_http_get_json", side_effect=_fake_http):
            providers._market_snapshot(memo, "http://fake", providers._parse_twse_snapshot,
                                        datetime(2026, 8, 18, 10, 0, tzinfo=TPE))

        self.assertEqual(seen["timeout"], 10)


# =========================================================================== #
#  共用整合測試基底(mock 紀律同 005,獨立成一份;另隔離工單 017 新增的
#  模組層 memo,見檔頭第 6 點)
# =========================================================================== #
class TwFallbackTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_twse_fallback_")
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
        # 工單 017 reviewer 修正包 G7:用專用測試輔助重置 process 記憶體 memo
        # (取代先前的 patch.object 換新 dict 手法——G7 明文要求新增這個輔助
        # 函數並在這裡呼叫)。setUp/tearDown 都呼叫,雙重保險確保不管本檔測試
        # 執行順序如何,都不會讀到其他測試殘留的 memo 狀態。
        providers._reset_snapshot_memos()

    def tearDown(self):
        providers._reset_snapshot_memos()
        self._env_patch.stop()
        self._sleep_patch.stop()
        self._urlopen_patch.stop()
        self._cache_patch.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _stock_cfg(self, ticker="2330", market="TW", name="台積電", method=""):
        cfg = {"ticker": ticker, "market": market, "name": name}
        if method:
            cfg["valuation"] = {"method": method}
        return cfg

    def _providers_cfg(self, **overrides):
        cfg = {"finmind_token": "", "finnhub_api_key": "", "cache_minutes": 15}
        cfg.update(overrides)
        return cfg


# =========================================================================== #
#  SPEC 測試 1 — FinMind 失敗 + TWSE 有代號(含逗號字串/民國日期解析)→ 成功
# =========================================================================== #
class TwseHitSuccessTest(TwFallbackTestCase):
    """鎖:FinMind 例外 → TWSE 命中 → source 標「TWSE(備援)」、price/price_date
    正確解析(逗號字串 + 民國日期)、TPEx 完全不被呼叫(TWSE 已命中就不需要
    再查 TPEx)。工單 017 reviewer 修正包 G1b:備援結果**不**寫入 blob 快取
    (避免降級快照蓋掉完整 FinMind blob、被 EOD 新鮮度釘住),下面明確驗證
    blob 檔案不存在(取代原本「應該寫入 blob」的斷言,這是刻意的行為變更,
    見 REPORT)。"""

    def test_twse_hit_with_comma_price_and_roc_date_succeeds_and_writes_blob(self):
        calls = []

        def _finmind_fail(url):
            calls.append(("finmind", url))
            raise RuntimeError("connection refused (fake)")

        def _twse(url):
            calls.append(("twse", url))
            return [_twse_row("2330", "1,088.00", roc_date="1150818", name="台積電")]

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse),
        ])

        with patch.object(providers, "_http_get_json", side_effect=router):
            result = providers.fetch(
                self._stock_cfg(ticker="2330", market="TW", name="台積電"),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )

        self.assertTrue(result.ok())
        self.assertEqual(result.source, "TWSE(備援)")
        self.assertEqual(result.price, 1088.0)
        self.assertEqual(result.price_date, "2026-08-18")
        self.assertEqual(len([c for c in calls if c[0] == "twse"]), 1)
        self.assertEqual(len([c for c in calls if c[0] == "tpex"]), 0)

        # G1b:備援成功不寫 blob(取代原本「應該寫入 blob」的斷言)。
        cache_file = providers._cache_path("TW", "2330")
        self.assertFalse(os.path.exists(cache_file))

        # 備援說明附掛在 quality_warnings(SPEC 允許用 source 標示或附註訊息
        # 二擇一,這裡兩者都做);語氣中性,無買賣字眼(比照工單 015 規範)。
        self.assertTrue(any("TWSE(備援)" in w for w in result.quality_warnings))
        for w in result.quality_warnings:
            for bad in ("買進", "賣出", "建議買", "建議賣"):
                self.assertNotIn(bad, w)


# =========================================================================== #
#  SPEC 測試 2 — 上櫃代號:TWSE 無 → TPEx 有 → 成功
# =========================================================================== #
class TpexFallbackWhenTwseMissTest(TwFallbackTestCase):
    def test_otc_ticker_missing_from_twse_found_in_tpex(self):
        calls = []

        def _finmind_fail(url):
            calls.append(("finmind", url))
            raise RuntimeError("FinMind down (fake)")

        def _twse(url):
            calls.append(("twse", url))
            return [_twse_row("2330", "1000.00")]  # 不含上櫃代號 5388

        def _tpex(url):
            calls.append(("tpex", url))
            return [_tpex_row("5388", "45.50", name="中磊")]

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse),
            ("tpex.org.tw", _tpex),
        ])

        with patch.object(providers, "_http_get_json", side_effect=router):
            result = providers.fetch(
                self._stock_cfg(ticker="5388", market="TW", name="中磊"),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )

        self.assertTrue(result.ok())
        self.assertEqual(result.source, "TPEx(備援)")
        self.assertEqual(result.price, 45.5)
        self.assertEqual(len([c for c in calls if c[0] == "twse"]), 1)
        self.assertEqual(len([c for c in calls if c[0] == "tpex"]), 1)


# =========================================================================== #
#  SPEC 測試 3 — memoize:同輪第二檔呼叫數不增;跨 EOD 邊界重打
# =========================================================================== #
class MemoizeTest(TwFallbackTestCase):
    """直接呼叫 `providers.fetch_tw`(繞過 `fetch()` 的 blob 快取層),精準只測
    TWSE/TPEx 全市場回應 memo 本身的行為,不與工單 013 的 blob EOD 新鮮度規則
    糾纏在一起(兩者是不同層級的快取,各自獨立)。"""

    def test_second_ticker_same_round_reuses_memo_zero_new_twse_calls(self):
        calls = []

        def _finmind_fail(url):
            calls.append(("finmind", url))
            raise RuntimeError("FinMind down (fake)")

        def _twse(url):
            calls.append(("twse", url))
            return [_twse_row("2330", "1000.00"), _twse_row("2317", "200.00", name="鴻海")]

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse),
        ])
        fake_now = datetime(2026, 8, 18, 20, 0, tzinfo=TPE)  # 週二 20:00

        with patch.object(providers, "_now_tpe", return_value=fake_now), \
             patch.object(providers, "_http_get_json", side_effect=router):
            r1 = providers.fetch_tw("2330", "台積電", years=5, token="", method="")
            r2 = providers.fetch_tw("2317", "鴻海", years=5, token="", method="")

        self.assertTrue(r1.ok())
        self.assertTrue(r2.ok())
        self.assertEqual(r1.source, "TWSE(備援)")
        self.assertEqual(r2.source, "TWSE(備援)")
        self.assertEqual(r2.price, 200.0)
        self.assertEqual(len([c for c in calls if c[0] == "twse"]), 1)  # 第二檔沒有新增呼叫

    def test_crossing_eod_boundary_triggers_refetch(self):
        calls = []

        def _finmind_fail(url):
            calls.append(("finmind", url))
            raise RuntimeError("FinMind down (fake)")

        def _twse(url):
            calls.append(("twse", url))
            return [_twse_row("2330", "1000.00")]

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse),
        ])

        before = datetime(2026, 8, 18, 17, 0, tzinfo=TPE)  # 週二,當天 18:00 邊界前
        after = datetime(2026, 8, 18, 19, 0, tzinfo=TPE)   # 同日 18:00 邊界後

        with patch.object(providers, "_http_get_json", side_effect=router):
            with patch.object(providers, "_now_tpe", return_value=before):
                r1 = providers.fetch_tw("2330", "台積電", years=5, token="", method="")
            with patch.object(providers, "_now_tpe", return_value=after):
                r2 = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertTrue(r1.ok())
        self.assertTrue(r2.ok())
        self.assertEqual(len([c for c in calls if c[0] == "twse"]), 2)  # 跨邊界後重打


# =========================================================================== #
#  SPEC 測試 4 — 兩端點皆無/皆失敗 → 行為與現狀逐位相同(回歸鎖)
# =========================================================================== #
class BothEndpointsFailOrMissTest(TwFallbackTestCase):
    def test_both_endpoints_error_out_preserves_existing_402_message(self):
        def _all_fail(url, timeout=25):
            raise RuntimeError("HTTP Error 402: Payment Required")

        with patch.object(providers, "_http_get_json", side_effect=_all_fail):
            result = providers.fetch(
                self._stock_cfg(ticker="2330", market="TW", name="台積電"),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )

        # 與工單 005 S7(tests/test_providers_fallback.py)完全相同的斷言,回歸鎖。
        self.assertFalse(result.ok())
        self.assertIn("402", result.error)
        self.assertIn("FINMIND_TOKEN", result.error)
        self.assertEqual(result.source, "FinMind")  # 未被備援標籤覆蓋

    def test_both_endpoints_respond_but_miss_ticker_falls_through_to_error(self):
        calls = []

        def _finmind_fail(url):
            calls.append(("finmind", url))
            raise RuntimeError("FinMind down (fake)")

        def _twse(url):
            calls.append(("twse", url))
            return [_twse_row("9999", "1.00")]   # 沒有 2330

        def _tpex(url):
            calls.append(("tpex", url))
            return [_tpex_row("8888", "2.00")]   # 也沒有 2330

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse),
            ("tpex.org.tw", _tpex),
        ])

        with patch.object(providers, "_http_get_json", side_effect=router):
            result = providers.fetch(
                self._stock_cfg(ticker="2330", market="TW", name="台積電"),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )

        self.assertFalse(result.ok())
        self.assertIn("FinMind 價格抓取失敗", result.error)
        self.assertEqual(result.source, "FinMind")
        self.assertEqual(len([c for c in calls if c[0] == "twse"]), 1)
        self.assertEqual(len([c for c in calls if c[0] == "tpex"]), 1)

    def test_stale_cache_rescue_still_works_when_all_three_sources_fail(self):
        """TW 版的工單 005 S5 回歸鎖:全源(FinMind+TWSE+TPEx)失敗時,過期快取
        保命機制不受這次改動影響。"""
        stale = StockData(
            ticker="2330", market="TW", name="台積電", currency="TWD",
            price=900.0, price_date="2026-08-01", source="FinMind",
        )
        providers._save_cache(stale)
        path = providers._cache_path("TW", "2330")
        import json
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
        blob["_fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)

        def _all_fail(url, timeout=25):
            raise RuntimeError("network down (fake)")

        with patch.object(providers, "_http_get_json", side_effect=_all_fail):
            result = providers.fetch(
                self._stock_cfg(ticker="2330", market="TW", name="台積電"),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )

        self.assertTrue(result.ok())
        self.assertEqual(result.price, 900.0)
        self.assertIn("(過期快取)", result.source)


# =========================================================================== #
#  SPEC 測試 5 — FinMind 正常時 → TWSE/TPEx 呼叫數 == 0(不偷打,回歸鎖)
# =========================================================================== #
class FinMindHealthyNoFallbackCallsTest(TwFallbackTestCase):
    """router 對 TWSE/TPEx 提供『若被打就會成功』的假回應,藉此確保下面的
    呼叫數斷言是真的在驗證『沒被呼叫』,而不是仰賴 router 對未知 URL 拋例外
    的副作用掩蓋掉問題。這也是 mutation 自查「fallback 順序反轉(先打 TWSE)」
    要能翻紅的測試(見 REPORT)。"""

    def test_finmind_success_means_zero_twse_and_tpex_calls(self):
        calls = []

        def _finmind_price(url):
            calls.append(("finmind", url))
            return {"status": 200, "data": [
                {"date": "2026-08-17", "close": 1080.0},
                {"date": "2026-08-18", "close": 1088.0},
            ]}

        def _twse(url):
            calls.append(("twse", url))
            return [_twse_row("2330", "1088.00")]

        def _tpex(url):
            calls.append(("tpex", url))
            return [_tpex_row("2330", "1088.00")]

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_price),
            ("openapi.twse.com.tw", _twse),
            ("tpex.org.tw", _tpex),
        ])

        with patch.object(providers, "_http_get_json", side_effect=router):
            result = providers.fetch(
                self._stock_cfg(ticker="2330", market="TW", name="台積電"),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )

        self.assertTrue(result.ok())
        self.assertEqual(result.source, "FinMind")
        self.assertEqual(len([c for c in calls if c[0] == "finmind"]), 1)
        self.assertEqual(len([c for c in calls if c[0] == "twse"]), 0)
        self.assertEqual(len([c for c in calls if c[0] == "tpex"]), 0)


# =========================================================================== #
#  SPEC 測試 6 — 歷史庫組裝(有資料/無資料)+ compute_zones 接線(不動 valuation)
# =========================================================================== #
class HistoryStoreAssemblyAndClassifyWiringTest(TwFallbackTestCase):
    def test_history_store_has_data_gets_assembled_into_fallback_result(self):
        # 工單 020:store 內的日期改為相對「現在」動態生成(不寫死絕對日期)。
        # `_assemble_tw_fallback` 讀 store 時會用 `fetch_tw` 的 years 窗過濾
        # (`start_date=requested_start`),寫死的舊絕對日期遲早會被真實日期
        # 往前滑動的窗起點排除在外(見工單 020 REPORT 判定表);這裡只驗證
        # store 組裝接線本身,日期值與 roc_date/`d.price_date` 無關聯,可安全
        # 改成相對日期。
        d0 = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        d1 = (datetime.now() - timedelta(days=19)).strftime("%Y-%m-%d")
        div_date = (datetime.now() - timedelta(days=50)).strftime("%Y-%m-%d")
        history_store.upsert_price(self._tmpdir, "TW", "2330",
                                    [(d0, 1050.0), (d1, 1060.0)])
        history_store.upsert_dividend(self._tmpdir, "TW", "2330", [(div_date, 3.0)])

        def _finmind_fail(url):
            raise RuntimeError("FinMind down (fake)")

        def _twse(url):
            return [_twse_row("2330", "1088.00", roc_date="1150818")]

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse),
        ])

        with patch.object(providers, "_http_get_json", side_effect=router):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertTrue(result.ok())
        self.assertEqual(result.source, "TWSE(備援)")
        self.assertEqual(result.price, 1088.0)   # 官方 EOD,不被 store 內的舊資料覆蓋
        dates = [d for d, _ in result.price_history]
        self.assertIn(d0, dates)
        self.assertIn(d1, dates)
        self.assertTrue(any(c == 3.0 for _, c in result.div_history))

    def test_no_history_store_data_empty_price_history_but_fixed_band_classify_still_works(self):
        def _finmind_fail(url):
            raise RuntimeError("FinMind down (fake)")

        def _twse(url):
            return [_twse_row("2330", "1088.00")]

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse),
        ])

        with patch.object(providers, "_http_get_json", side_effect=router):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertTrue(result.ok())
        self.assertEqual(result.price, 1088.0)
        self.assertEqual(result.price_history, [])   # store 全空,沒有硬塞進今天這筆

        # 只驗接線:固定價格帶分類不需要 price_history,不應被空序列卡住
        # (禁區:不動 valuation/classify 本體,這裡只是呼叫既有函數驗證接線)。
        from aimonitor import valuation, classify
        stock_cfg = {
            "ticker": "2330", "market": "TW", "name": "台積電",
            "valuation": {"method": "price_band",
                          "bands": {"super_bargain": 800, "cheap": 900,
                                    "fair": 1000, "expensive": 1100, "euphoria": 1200}},
        }
        zones = valuation.compute_zones(stock_cfg, result, {})
        analysis = classify.analyze(result.price, zones["zones"], result.price_history, [1, 3, 5])
        self.assertEqual(analysis["region"], "昂貴價區")  # 1088 落在 (1000, 1100]
        self.assertIsNone(analysis["annual_vol_pct"])     # 序列不足,自然是 None,不炸


# =========================================================================== #
#  G2(reviewer 修正包 P1-2)— 官方現價與本地歷史尾端「尺度脫鉤」護欄
# =========================================================================== #
class ScaleGuardTest(TwFallbackTestCase):
    """鎖:P1-2 真實重現(price/歷史尺度脫鉤→假 is_buy+桌面通知)的根本修正——
    官方現價與組裝出的歷史序列尾端比值異常時,整組捨棄三個歷史序列並告知,
    寧可讓 auto 類估價誠實報 ValuationError,也不要用錯尺度的歷史算出假訊號。
    門檻 0.6/1.7 與 `_back_adjust_tw` 判斷真正拆股的門檻同款(見該函式)。

    工單 020:兩個 store 種子日期改為相對「現在」動態生成,理由同
    `HistoryStoreAssemblyAndClassifyWiringTest`(見上方說明)。"""

    def test_ratio_025_below_threshold_discards_all_three_series_with_warning(self):
        seed_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        history_store.upsert_price(self._tmpdir, "TW", "2330", [(seed_date, 4000.0)])

        def _finmind_fail(url):
            raise RuntimeError("down (fake)")

        def _twse(url):
            return [_twse_row("2330", "1000.00")]  # 1000/4000 = 0.25 < 0.6

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse),
        ])

        with patch.object(providers, "_http_get_json", side_effect=router):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertTrue(result.ok())
        self.assertEqual(result.price, 1000.0)
        self.assertEqual(result.price_history, [])
        self.assertEqual(result.div_history, [])
        self.assertEqual(result.per_history, [])
        self.assertTrue(any("跳動異常" in w for w in result.quality_warnings))
        self.assertTrue(any("拆股" in w for w in result.quality_warnings))

    def test_ratio_1_0_within_threshold_keeps_history(self):
        seed_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        history_store.upsert_price(self._tmpdir, "TW", "2330", [(seed_date, 1000.0)])

        def _finmind_fail(url):
            raise RuntimeError("down (fake)")

        def _twse(url):
            return [_twse_row("2330", "1000.00")]  # 比值 1.0,不觸發護欄

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse),
        ])

        with patch.object(providers, "_http_get_json", side_effect=router):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertTrue(result.ok())
        self.assertEqual(result.price_history, [(seed_date, 1000.0)])
        self.assertFalse(any("跳動異常" in w for w in result.quality_warnings))


# =========================================================================== #
#  G3 整合(reviewer 修正包 P1-3)— 全故障時整輪多檔的呼叫數
# =========================================================================== #
class FullOutageMultiTickerCallCountTest(TwFallbackTestCase):
    """鎖:P1-3 真實重現(全網路故障時每檔各自重打兩端點,單輪最壞可疊加到
    近一小時)的根本修正——同一輪內,不管查詢幾檔,TWSE/TPEx 各自最多只真正
    打 1 次;15 分鐘冷卻窗口過後才會再嘗試 1 次。"""

    def test_all_endpoints_down_across_three_tickers_each_endpoint_called_once(self):
        calls = []

        def _finmind_fail(url):
            calls.append(("finmind", url))
            raise RuntimeError("FinMind down (fake)")

        def _twse_fail(url):
            calls.append(("twse", url))
            raise RuntimeError("TWSE down (fake)")

        def _tpex_fail(url):
            calls.append(("tpex", url))
            raise RuntimeError("TPEx down (fake)")

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse_fail),
            ("tpex.org.tw", _tpex_fail),
        ])

        fake_now = datetime(2026, 8, 18, 20, 0, tzinfo=TPE)
        with patch.object(providers, "_now_tpe", return_value=fake_now), \
             patch.object(providers, "_http_get_json", side_effect=router):
            r1 = providers.fetch_tw("2330", "台積電", years=5, token="", method="")
            r2 = providers.fetch_tw("2317", "鴻海", years=5, token="", method="")
            r3 = providers.fetch_tw("2454", "聯發科", years=5, token="", method="")

        self.assertFalse(r1.ok())
        self.assertFalse(r2.ok())
        self.assertFalse(r3.ok())
        self.assertEqual(len([c for c in calls if c[0] == "twse"]), 1)
        self.assertEqual(len([c for c in calls if c[0] == "tpex"]), 1)
        # FinMind 本身仍然每檔各打一次探測(G1b 之後的真實模型:FinMind 額度
        # 壓力不變,只有 TWSE/TPEx 備援呼叫被壓到全輪各 1 次,見 docs/api-budget.md)。
        self.assertEqual(len([c for c in calls if c[0] == "finmind"]), 3)

    def test_after_15min_cooldown_endpoint_retried_once_more(self):
        calls = []

        def _finmind_fail(url):
            calls.append(("finmind", url))
            raise RuntimeError("FinMind down (fake)")

        def _twse_fail(url):
            calls.append(("twse", url))
            raise RuntimeError("TWSE down (fake)")

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse_fail),
        ])

        t1 = datetime(2026, 8, 18, 20, 0, tzinfo=TPE)
        t2 = datetime(2026, 8, 18, 20, 20, tzinfo=TPE)  # 20 分鐘後,冷卻窗口(15 分)已過

        with patch.object(providers, "_http_get_json", side_effect=router):
            with patch.object(providers, "_now_tpe", return_value=t1):
                providers.fetch_tw("2330", "台積電", years=5, token="", method="")
                providers.fetch_tw("2317", "鴻海", years=5, token="", method="")
            with patch.object(providers, "_now_tpe", return_value=t2):
                providers.fetch_tw("2454", "聯發科", years=5, token="", method="")

        self.assertEqual(len([c for c in calls if c[0] == "twse"]), 2)  # t1 一次 + t2 一次


# =========================================================================== #
#  G4 — 派生欄位測試補洞
# =========================================================================== #
class DerivedFieldsAndG1FallbackTest(TwFallbackTestCase):
    """補洞:dividend_yield /100 轉換、F3 十天邊界兩側、trailing_eps、
    annual_dividend TTM cutoff、store 路徑的拆股還原、G1a 的 blob 退援、
    G1b 的 blob 不寫入(內容不變)。"""

    def _router_finmind_fail_twse_hit(self, price="1000.00", roc_date="1150818"):
        def _finmind_fail(url):
            raise RuntimeError("down (fake)")

        def _twse(url):
            return [_twse_row("2330", price, roc_date=roc_date)]

        return _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse),
        ])

    def test_dividend_yield_from_per_table_divided_by_100(self):
        history_store.upsert_price(self._tmpdir, "TW", "2330", [("2026-08-15", 1000.0)])
        # FinMind PER 表原始存的是百分數(例如 2.5 代表 2.5%,組裝時要 /100)。
        history_store.upsert_per(self._tmpdir, "TW", "2330", [("2026-08-18", 20.0, 2.5)])

        with patch.object(providers, "_http_get_json",
                           side_effect=self._router_finmind_fail_twse_hit()):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertTrue(result.ok())
        self.assertAlmostEqual(result.dividend_yield, 0.025)

    def test_f3_boundary_exactly_10_days_still_derives_per(self):
        history_store.upsert_price(self._tmpdir, "TW", "2330", [("2026-08-15", 1000.0)])
        history_store.upsert_per(self._tmpdir, "TW", "2330", [("2026-08-08", 20.0, None)])  # 恰 10 天前

        with patch.object(providers, "_http_get_json",
                           side_effect=self._router_finmind_fail_twse_hit(price="1000.00", roc_date="1150818")):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertTrue(result.ok())
        self.assertEqual(result.price_date, "2026-08-18")
        self.assertEqual(result.per, 20.0)  # <=10 天,派生

    def test_f3_boundary_11_days_does_not_derive_per(self):
        history_store.upsert_price(self._tmpdir, "TW", "2330", [("2026-08-15", 1000.0)])
        history_store.upsert_per(self._tmpdir, "TW", "2330", [("2026-08-07", 20.0, None)])  # 11 天前

        with patch.object(providers, "_http_get_json",
                           side_effect=self._router_finmind_fail_twse_hit(price="1000.00", roc_date="1150818")):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertTrue(result.ok())
        self.assertEqual(result.price_date, "2026-08-18")
        self.assertIsNone(result.per)  # >10 天,不派生

    def test_trailing_eps_equals_price_over_per(self):
        history_store.upsert_price(self._tmpdir, "TW", "2330", [("2026-08-15", 1000.0)])
        history_store.upsert_per(self._tmpdir, "TW", "2330", [("2026-08-18", 20.0, None)])

        with patch.object(providers, "_http_get_json",
                           side_effect=self._router_finmind_fail_twse_hit(price="1000.00", roc_date="1150818")):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertEqual(result.per, 20.0)
        self.assertEqual(result.trailing_eps, round(1000.0 / 20.0, 4))

    def test_annual_dividend_ttm_cutoff_only_sums_within_365_days(self):
        # 工單 020:三個種子日期改為相對「現在」動態生成——原本寫死的
        # "2026-01-01"/"2020-01-01" 除了受 5 年窗過濾影響外,更早在 365 天 TTM
        # cutoff(同樣是 `datetime.now()` 相對算式)這關就會先翻紅(引爆日遠比
        # 5 年窗更早,見工單 020 REPORT 判定表),兩個風險一併用相對日期消除。
        price_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        history_store.upsert_price(self._tmpdir, "TW", "2330", [(price_date, 1000.0)])
        recent_ex_date = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        old_ex_date = (datetime.now() - timedelta(days=800)).strftime("%Y-%m-%d")
        history_store.upsert_dividend(self._tmpdir, "TW", "2330", [
            (recent_ex_date, 5.0),    # 在 365 天窗內
            (old_ex_date, 999.0),     # 遠超窗外,不該被計入
        ])

        with patch.object(providers, "_http_get_json",
                           side_effect=self._router_finmind_fail_twse_hit()):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertTrue(result.ok())
        self.assertEqual(result.annual_dividend, 5.0)

    def test_store_path_runs_back_adjust_tw_for_synthetic_split(self):
        # 模擬股價在某天發生 1:4 分割(r=1000/4000=0.25<0.6,觸發 _back_adjust_tw
        # 的整數倍拆股判斷)。工單 020:種子日期改為相對「現在」動態生成(理由
        # 同上方 ScaleGuardTest)。
        d0 = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        d1 = (datetime.now() - timedelta(days=19)).strftime("%Y-%m-%d")
        history_store.upsert_price(self._tmpdir, "TW", "2330", [
            (d0, 4000.0),
            (d1, 1000.0),
        ])

        with patch.object(providers, "_http_get_json",
                           side_effect=self._router_finmind_fail_twse_hit(price="1010.00", roc_date="1150818")):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertTrue(result.ok())
        hist = dict(result.price_history)
        # 分割前那筆應該被還原乘上 0.25 的還原因子:4000*0.25=1000。
        self.assertAlmostEqual(hist[d0], 1000.0)
        self.assertAlmostEqual(hist[d1], 1000.0)
        # 還原後現價(1010)與序列尾端(1000)比值≈1.01,不會被 G2 護欄誤判丟棄。
        self.assertNotEqual(result.price_history, [])

    def test_g1a_store_empty_blob_has_data_uses_blob_history_without_double_adjustment(self):
        """store 完全空、上一份 blob 有資料時,歷史序列應該直接沿用 blob(已經
        是還原後序列)——不能再被 `_back_adjust_tw` 處理一次。用一組「如果被
        二次還原就會不同」的序列驗證:[4000, 1000] 若被誤當成原始值再跑一次
        還原,會變成 [1000, 1000];保持原封不動 [4000, 1000] 才代表沒有二次
        還原。"""
        old_blob = StockData(
            ticker="2330", market="TW", name="台積電", currency="TWD",
            price=900.0, price_date="2026-08-01", source="FinMind",
            price_history=[("2026-07-01", 4000.0), ("2026-07-02", 1000.0)],
            div_history=[], per_history=[],
        )
        providers._save_cache(old_blob)   # store 完全不 upsert 任何東西,刻意留空

        with patch.object(providers, "_http_get_json",
                           side_effect=self._router_finmind_fail_twse_hit(price="1010.00", roc_date="1150818")):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertTrue(result.ok())
        self.assertEqual(result.source, "TWSE(備援)")
        self.assertEqual(result.price, 1010.0)  # 官方現價,不被 blob 的舊 price 覆蓋
        # blob 走過一輪 JSON 序列化/反序列化,tuple 會變成 list(json 沒有 tuple
        # 型別)——這是既有、無關本工單的 blob round-trip 特性,這裡統一轉回
        # tuple 再比對,只驗證「數值原封不動、未被二次還原」這件事本身。
        normalized = [tuple(p) for p in result.price_history]
        self.assertEqual(normalized,
                          [("2026-07-01", 4000.0), ("2026-07-02", 1000.0)])  # 原封不動,未被二次還原

    def test_g1b_existing_blob_content_byte_identical_after_fallback_success(self):
        """G1b:備援成功後不寫 blob——這裡驗證的不只是「沒有新建檔案」
        (TwseHitSuccessTest 已驗證那個案例),而是「已存在的 blob 檔案內容
        完全沒被改寫」這個更嚴格的情境(舊 blob 存在 + 備援成功,兩者疊加)。"""
        good_blob = StockData(
            ticker="2330", market="TW", name="台積電", currency="TWD",
            price=2350.0, price_date="2026-08-17", source="FinMind",
            price_history=[("2026-08-15", 2300.0), ("2026-08-17", 2350.0)],
        )
        providers._save_cache(good_blob)
        cache_file = providers._cache_path("TW", "2330")
        import json
        with open(cache_file, "r", encoding="utf-8") as f:
            blob = json.load(f)
        # 手動改成 5 天前,確保 fetch() 的 TW 分支判定這份 blob 過期、真的會
        # 往下呼叫 fetch_tw(否則會在 cache-hit 階段就直接短路回傳,測不到
        # 備援路徑)。
        blob["_fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)
        with open(cache_file, "r", encoding="utf-8") as f:
            before = f.read()

        def _finmind_fail(url):
            raise RuntimeError("down (fake)")

        def _twse(url):
            return [_twse_row("2330", "2360.00")]  # 與 blob 尾端 2350 尺度接近,不會被 G2 丟棄

        router = _http_get_json_router([
            ("api.finmindtrade.com", _finmind_fail),
            ("openapi.twse.com.tw", _twse),
        ])

        with patch.object(providers, "_http_get_json", side_effect=router):
            result = providers.fetch(
                self._stock_cfg(ticker="2330", market="TW", name="台積電"),
                self._providers_cfg(),
                history_years=5,
                use_cache=True,
            )

        self.assertTrue(result.ok())
        self.assertEqual(result.source, "TWSE(備援)")
        self.assertEqual(result.price, 2360.0)  # 備援結果本身確實拿到新價

        with open(cache_file, "r", encoding="utf-8") as f:
            after = f.read()
        self.assertEqual(before, after)  # blob 檔案內容完全沒被動過


if __name__ == "__main__":
    unittest.main()

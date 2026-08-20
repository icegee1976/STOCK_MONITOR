"""本地歷史庫(工單 014)離線測試——store 層 CRUD/損毀退化 + `fetch_tw` 增量接線。

目的:鎖住 `aimonitor/history_store.py`(純儲存層)與 `aimonitor/providers.py::fetch_tw`
的增量接線(工單 014 SPEC D1/D2/D3),尤其是**拆股跨兩次增量抓取仍須正確還原**這個
本工單命脈(見 `SplitAcrossIncrementsTest`)。全部離線、0 API 呼叫。

mock 紀律:沿用工單 005(`tests/test_providers_fallback.py`)訂下的規範——CACHE_DIR
隔離到 tempdir、`urlopen` 保險絲(真的打到網路就 AssertionError)、`_http_get_json`
逐條 mock、`time.sleep` no-op、環境變數快照。**刻意不 import test_providers_fallback**,
在這裡獨立寫一份等價的基底類別,避免兩個測試檔互相耦合(工單 014 executor 設計決定,
見 REPORT)。

每個測試類別開頭都寫「鎖什麼、什麼突變會翻紅」,呼應本專案既有測試檔慣例。

**慣例(工單 020,防再犯)**:本檔大量測試驅動 `fetch_tw` 的 `requested_start`
(`now() - (years*365.25+10)天`,見 `_window_start` helper)這個會隨真實日期
每天滑動的窗起點。任何 fixture 日期字串只要會被拿去跟這個窗(或其他任何
`datetime.now()`/`_now_tpe()` 派生值)比較大小、或會被 store 的 `start_date`
過濾條件篩選,就**不可以寫死絕對日期**——寫下當天測試會過,但終將在未來某個
可算出的日期翻紅(工單 020 就是這類「日期炸彈」被引爆的事後修復)。正確作法
二選一:(1) 用相對日期,如 `(datetime.now() - timedelta(days=N)).strftime(...)`;
(2) 該函數/路徑本身支援注入時鐘時,優先用 `patch.object(providers, "_now_tpe",
return_value=fake_now)` 把時間完全釘死(決定性最好,見 `tests/test_eod_cache.py`
`tests/test_twse_fallback.py` 的既有慣例)。新增測試前請自我檢查這一點。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs

from aimonitor import providers, history_store


# --------------------------------------------------------------------------- #
#  共用 helper
# --------------------------------------------------------------------------- #
def _window_start(years: float) -> str:
    """複製 `fetch_tw` 內計算窗起點的公式,供測試端算出「這次應該要求的 start_date」
    做比對(fetch_tw 本身沒有、也不需要為了這個工單額外做可注入時鐘——這個公式
    在工單 014 之前就是直接用 `datetime.now()`,不在本工單改動範圍)。"""
    return (datetime.now() - timedelta(days=int(years * 365.25) + 10)).strftime("%Y-%m-%d")


def _start_date_of(url: str) -> str | None:
    return parse_qs(urlparse(url).query).get("start_date", [None])[0]


# --------------------------------------------------------------------------- #
#  假 yfinance 模組(US 快照測試用,最小化滿足 fetch_us 用到的介面)
# --------------------------------------------------------------------------- #
class _FakeTimestamp:
    def __init__(self, s: str):
        self._s = s

    def strftime(self, fmt):
        assert fmt == "%Y-%m-%d"
        return self._s


class _FakeHist:
    def __init__(self, rows):
        self.index = [_FakeTimestamp(d) for d, _ in rows]
        self._closes = [c for _, c in rows]

    def __len__(self):
        return len(self._closes)

    def __getitem__(self, key):
        assert key == "Close"
        return self._closes


class _FakeTicker:
    def __init__(self, hist_rows):
        self._hist_rows = hist_rows

    def history(self, period=None, auto_adjust=None):
        return _FakeHist(self._hist_rows)

    @property
    def info(self):
        return {}


def _fake_yf_module(hist_rows):
    m = type("FakeYFModule", (), {})()
    m.Ticker = lambda ticker: _FakeTicker(hist_rows)
    return m


# =========================================================================== #
#  D4 point 1(前半):純儲存層 CRUD / 冪等
# =========================================================================== #
class StoreCrudTest(unittest.TestCase):
    """鎖:`history_store` 的公開讀寫函數在正常(未損毀)檔案上的基本語意——
    upsert 依 PK 冪等覆蓋、依日期升冪回傳、`start_date` 切片、`max_date`、meta
    讀寫、不同 market/ticker/dataset 互不污染。不碰 `providers`,直接給 tmp 目錄。
    什麼突變會翻紅:PK 定義被改壞(例如漏掉 date 導致同 ticker 只能存一筆)、
    ORDER BY 被拿掉、start_date 篩選條件寫反等,這裡的斷言都會抓到。
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_history_store_crud_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_upsert_then_get_price_roundtrip_sorted_ascending(self):
        # 刻意用「降冪」順序寫入,驗證讀出來一律是升冪(ORDER BY date ASC 真的生效)。
        history_store.upsert_price(self._tmpdir, "TW", "2330",
                                    [("2026-01-02", 101.0), ("2026-01-01", 100.0)])
        self.assertEqual(
            history_store.get_price(self._tmpdir, "TW", "2330"),
            [("2026-01-01", 100.0), ("2026-01-02", 101.0)],
        )

    def test_upsert_is_idempotent_overwrites_by_primary_key(self):
        history_store.upsert_price(self._tmpdir, "TW", "2330", [("2026-01-01", 100.0)])
        history_store.upsert_price(self._tmpdir, "TW", "2330", [("2026-01-01", 999.0)])
        rows = history_store.get_price(self._tmpdir, "TW", "2330")
        self.assertEqual(rows, [("2026-01-01", 999.0)])  # 覆蓋成一筆,不是疊加成兩筆

    def test_start_date_slicing_excludes_earlier_rows(self):
        history_store.upsert_price(self._tmpdir, "TW", "2330", [
            ("2020-01-01", 1.0), ("2021-01-01", 2.0), ("2022-01-01", 3.0)])
        self.assertEqual(
            history_store.get_price(self._tmpdir, "TW", "2330", start_date="2021-01-01"),
            [("2021-01-01", 2.0), ("2022-01-01", 3.0)],
        )

    def test_max_date_reflects_latest_stored_row(self):
        history_store.upsert_price(self._tmpdir, "TW", "2330",
                                    [("2020-01-01", 1.0), ("2022-06-15", 3.0)])
        self.assertEqual(history_store.max_date(self._tmpdir, "TW", "2330", "price"), "2022-06-15")

    def test_max_date_none_when_no_rows_or_unknown_dataset(self):
        self.assertIsNone(history_store.max_date(self._tmpdir, "TW", "2330", "price"))
        self.assertIsNone(history_store.max_date(self._tmpdir, "TW", "2330", "not_a_real_dataset"))

    def test_meta_roundtrip(self):
        self.assertIsNone(history_store.get_meta(self._tmpdir, "TW", "2330", "price"))
        self.assertTrue(history_store.set_meta(self._tmpdir, "TW", "2330", "price",
                                                "2020-01-01", "2026-01-01T00:00:00+00:00"))
        self.assertEqual(
            history_store.get_meta(self._tmpdir, "TW", "2330", "price"),
            {"requested_start": "2020-01-01", "last_success": "2026-01-01T00:00:00+00:00"},
        )

    def test_meta_upsert_overwrites_not_duplicates(self):
        history_store.set_meta(self._tmpdir, "TW", "2330", "price", "2020-01-01", "t1")
        history_store.set_meta(self._tmpdir, "TW", "2330", "price", "2019-01-01", "t2")
        self.assertEqual(
            history_store.get_meta(self._tmpdir, "TW", "2330", "price"),
            {"requested_start": "2019-01-01", "last_success": "t2"},
        )

    def test_per_and_dividend_tables_independent_of_price(self):
        history_store.upsert_per(self._tmpdir, "TW", "2330", [("2026-01-01", 18.0, 2.0)])
        history_store.upsert_dividend(self._tmpdir, "TW", "2330", [("2026-01-15", 5.0)])
        self.assertEqual(history_store.get_per(self._tmpdir, "TW", "2330"),
                          [("2026-01-01", 18.0, 2.0)])
        self.assertEqual(history_store.get_dividend(self._tmpdir, "TW", "2330"),
                          [("2026-01-15", 5.0)])
        self.assertEqual(history_store.get_price(self._tmpdir, "TW", "2330"), [])  # 不互相污染

    def test_different_market_or_ticker_isolated(self):
        history_store.upsert_price(self._tmpdir, "TW", "2330", [("2026-01-01", 100.0)])
        history_store.upsert_price(self._tmpdir, "US", "2330", [("2026-01-01", 999.0)])
        self.assertEqual(history_store.get_price(self._tmpdir, "TW", "2330"), [("2026-01-01", 100.0)])
        self.assertEqual(history_store.get_price(self._tmpdir, "US", "2330"), [("2026-01-01", 999.0)])

    def test_replace_us_snapshot_deletes_then_inserts(self):
        history_store.upsert_price(self._tmpdir, "US", "AAPL",
                                    [("2026-01-01", 1.0), ("2026-01-02", 2.0)])
        self.assertTrue(
            history_store.replace_us_snapshot(self._tmpdir, "US", "AAPL", [("2026-03-01", 9.0)])
        )
        self.assertEqual(history_store.get_price(self._tmpdir, "US", "AAPL"), [("2026-03-01", 9.0)])


# =========================================================================== #
#  D4 point 1(後半):sqlite 檔案損毀 → 永不炸
# =========================================================================== #
class StoreCorruptionTest(unittest.TestCase):
    """鎖:「永不炸原則」——sqlite 檔案是垃圾 bytes 時,每個公開函數都必須回傳
    「不可用」的 sentinel(讀 `None`、寫 `False`),不能讓例外往外傳。
    什麼突變會翻紅:任何一個公開函數如果拿掉自己的 try/except(或 except 範圍
    縮小到漏接 `sqlite3.DatabaseError`),對應那一條測試會直接看到例外炸出來
    (unittest 會回報 ERROR 而不是通過)。
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_history_store_corrupt_")
        os.makedirs(self._tmpdir, exist_ok=True)
        with open(history_store._db_path(self._tmpdir), "wb") as f:
            f.write(b"not a sqlite database, just garbage bytes" * 30)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_get_price_returns_none_not_raises(self):
        self.assertIsNone(history_store.get_price(self._tmpdir, "TW", "2330"))

    def test_get_per_returns_none_not_raises(self):
        self.assertIsNone(history_store.get_per(self._tmpdir, "TW", "2330"))

    def test_get_dividend_returns_none_not_raises(self):
        self.assertIsNone(history_store.get_dividend(self._tmpdir, "TW", "2330"))

    def test_upsert_price_returns_false_not_raises(self):
        self.assertFalse(history_store.upsert_price(self._tmpdir, "TW", "2330", [("2026-01-01", 1.0)]))

    def test_upsert_per_returns_false_not_raises(self):
        self.assertFalse(history_store.upsert_per(self._tmpdir, "TW", "2330", [("2026-01-01", 1.0, 1.0)]))

    def test_upsert_dividend_returns_false_not_raises(self):
        self.assertFalse(history_store.upsert_dividend(self._tmpdir, "TW", "2330", [("2026-01-01", 1.0)]))

    def test_get_meta_returns_none_not_raises(self):
        self.assertIsNone(history_store.get_meta(self._tmpdir, "TW", "2330", "price"))

    def test_set_meta_returns_false_not_raises(self):
        self.assertFalse(history_store.set_meta(self._tmpdir, "TW", "2330", "price", "x", "y"))

    def test_max_date_returns_none_not_raises(self):
        self.assertIsNone(history_store.max_date(self._tmpdir, "TW", "2330", "price"))

    def test_replace_us_snapshot_returns_false_not_raises(self):
        self.assertFalse(
            history_store.replace_us_snapshot(self._tmpdir, "US", "AAPL", [("2026-01-01", 1.0)])
        )


# =========================================================================== #
#  共用整合測試基底(mock 紀律同 005,獨立成一份,見檔頭說明)
# =========================================================================== #
class _HistoryStoreIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_history_store_")
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


# =========================================================================== #
#  D4 point 1(整合面):store 損毀時 fetch_tw 仍退回全量抓取成功
# =========================================================================== #
class StoreUnavailableFallsBackToSuccessfulFetchTest(_HistoryStoreIntegrationTestCase):
    """鎖:即使 `history.sqlite3` 損毀(讀寫皆不可用),`fetch_tw` 仍必須靠這次
    剛抓到、尚未入庫的原始資料成功組出結果——store 完全是加分項,壞掉不能拖垮
    既有的「至少這次抓到什麼就回傳什麼」語意。
    什麼突變會翻紅:如果 `_sync_and_assemble` 在 `get_fn` 回傳 `None` 時沒有退回
    `raw_rows`(例如誤寫成直接回傳 `assembled`,即使是 None 也不做 fallback),
    `d.price_history` 會變空、`result.ok()` 變 False。
    """

    def test_corrupted_store_file_does_not_prevent_successful_fetch(self):
        os.makedirs(self._tmpdir, exist_ok=True)
        with open(history_store._db_path(self._tmpdir), "wb") as f:
            f.write(b"garbage, not a sqlite file" * 50)

        def _price(url):
            return {"status": 200, "data": [
                {"date": "2026-08-01", "close": 500.0},
                {"date": "2026-08-02", "close": 505.0},
            ]}

        with patch.object(providers, "_http_get_json", side_effect=_price):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertTrue(result.ok())
        self.assertEqual(result.price, 505.0)
        self.assertEqual(result.price_history, [("2026-08-01", 500.0), ("2026-08-02", 505.0)])
        # 確認真的是走了退回路徑,不是巧合般碰巧修好了。
        self.assertIsNone(history_store.get_price(self._tmpdir, "TW", "2330"))


class WriteFailsButReadWorksStillMergesFreshDataTest(_HistoryStoreIntegrationTestCase):
    """F1(reviewer 修正包 P1-1 根修)專屬測試,直接對應 reviewer 用真實檔案鎖
    重現的場景:sqlite **可讀不可寫**(例如另一支 process 持有寫鎖、或檔案系統
    唯讀)——不是「完全損毀讀寫皆炸」(那是上面
    `StoreUnavailableFallsBackToSuccessfulFetchTest` 鎖的情境)。這裡用
    `patch.object(history_store, "upsert_price"/"upsert_per", return_value=False)`
    模擬寫入恆失敗,但讀取(`get_price`/`get_per`/`get_meta`/`max_date`)完全
    正常。store 內預先灌入(用真正、未被 patch 的函數)半年前的舊資料,這次
    增量抓到全新的一批。

    正確實作(F1 記憶體合併)下:即使這次 upsert 全部失敗,fetch 仍必須成功,
    `price_history`/`per_history` 同時含「store 讀到的舊列」與「這次剛抓到、
    寫入失敗但仍在記憶體中的新列」,`price`/`per` 反映最新一筆——不會像 F1
    修正前的舊設計那樣,讀回「寫入前」的舊資料而不自知,導致新資料整批消失、
    嚴重時 `d.ok()` 判 False 卻沒有清楚的 `d.error`,讓 `fetch()` 不寫回 blob
    快取、額度保護失效。
    什麼突變會翻紅:如果把 `_sync_and_assemble` 的「無條件記憶體合併」拿掉、
    退回「直接信任 `get_fn` 讀回結果」的舊設計,這裡 upsert 被 patch 成恆
    `False` 後,store 讀回的內容完全不會反映這次抓到的新資料——`price_history`
    會漏掉 `("2026-01-15", 999.0)`、`result.price` 停在舊值 350.0,斷言翻紅
    (見工單 REPORT 的 mutation 重演:「merge 拿掉」)。
    """

    def test_upsert_always_fails_fetch_still_succeeds_with_merged_fresh_and_old_rows(self):
        old_price_row = ("2025-06-01", 350.0)
        old_per_row = ("2025-06-01", 15.0, 1.0)
        # 用真正(未被 patch)的函數預先灌入舊資料。
        history_store.upsert_price(self._tmpdir, "TW", "2330", [old_price_row])
        history_store.upsert_per(self._tmpdir, "TW", "2330", [old_per_row])
        window_start = _window_start(5)
        history_store.set_meta(self._tmpdir, "TW", "2330", "price", window_start,
                                "2025-06-01T00:00:00+00:00")
        history_store.set_meta(self._tmpdir, "TW", "2330", "per", window_start,
                                "2025-06-01T00:00:00+00:00")

        def _router(url):
            if "TaiwanStockPER" in url:
                return {"status": 200, "data": [
                    {"date": "2025-06-01", "PER": 15.0, "dividend_yield": 1.0},   # 重疊列
                    {"date": "2026-01-15", "PER": 22.0, "dividend_yield": 1.2},   # 這次新資料
                ]}
            # 工單注意:新舊價格比例故意維持在 [0.6,1.7] 安全帶內(350→400,
            # ratio=1.143),避免不小心觸發 `_back_adjust_tw` 的分割/反分割
            # 偵測(那是另一個獨立機制,不是本測試要驗證的對象——用差太多的
            # 價格會讓還原邏輯把舊資料「合理地」改寫掉,汙染這裡的合併斷言)。
            return {"status": 200, "data": [
                {"date": "2025-06-01", "close": 350.0},   # 重疊列(增量起點含當天)
                {"date": "2026-01-15", "close": 400.0},   # 這次真正的新資料
            ]}

        with patch.object(history_store, "upsert_price", return_value=False), \
             patch.object(history_store, "upsert_per", return_value=False), \
             patch.object(providers, "_http_get_json", side_effect=_router):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="pe_band")

        self.assertTrue(result.ok())
        self.assertEqual(result.price, 400.0)                       # 反映最新一筆(新資料)
        self.assertIn(old_price_row, result.price_history)          # 舊資料仍在(store 讀回)
        self.assertIn(("2026-01-15", 400.0), result.price_history)  # 新資料也在(記憶體合併補位)
        # PER 同樣受惠於合併;F3 護欄下,最新一筆與 price_date 同一天(差 0 天)
        # 應正常派生。
        self.assertEqual(result.per, 22.0)

        # 佐證:store 內真的沒有被寫入新資料(upsert 確實失敗了,不是巧合成功)。
        stored_price = history_store.get_price(self._tmpdir, "TW", "2330")
        self.assertNotIn("2026-01-15", [d for d, _ in stored_price])
        stored_per = history_store.get_per(self._tmpdir, "TW", "2330")
        self.assertNotIn("2026-01-15", [d for d, _, _ in stored_per])


# =========================================================================== #
#  F2(reviewer 修正包 P1-2a):增量回應為空 → 視為失敗
# =========================================================================== #
class IncrementalEmptyPriceResponseIsTreatedAsFailureTest(_HistoryStoreIntegrationTestCase):
    """F2 專屬測試:增量起點含「當天」(store 既有序列的 MAX(date)),正常情況
    下 FinMind 一定至少會回傳這筆重疊列——回空代表異常(暫時性資料源問題等),
    不是「這段期間真的沒有交易」。`fetch_tw` 層級驗證:store 已有半年前的舊
    資料(→ 這次走增量分支),這次抓到空列表 → `d.ok()` 必須是 False、
    `d.error` 非空且提及「增量」。全量分支(無 meta,首次)回空則維持現狀,
    不受這個檢查影響(新上市無資料等合法情況)。
    什麼突變會翻紅:如果 F2 的檢查被拿掉,`d.price_history` 會退化成只剩
    store 讀到的舊資料、`d.ok()` 仍是 True(悄悄吞掉一次異常),
    `test_incremental_empty_response_marks_fetch_as_failed` 會翻紅。
    """

    def test_incremental_empty_response_marks_fetch_as_failed(self):
        old_row = ("2025-06-01", 350.0)
        history_store.upsert_price(self._tmpdir, "TW", "2330", [old_row])
        window_start = _window_start(5)
        history_store.set_meta(self._tmpdir, "TW", "2330", "price", window_start,
                                "2025-06-01T00:00:00+00:00")

        def _price_empty(url):
            return {"status": 200, "data": []}

        with patch.object(providers, "_http_get_json", side_effect=_price_empty):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertFalse(result.ok())
        self.assertNotEqual(result.error, "")
        self.assertIn("增量", result.error)

    def test_full_fetch_empty_response_unaffected_current_behavior(self):
        # 對照組:全量抓取(無 meta,首次)回空 → 維持現狀(不受 F2 影響)。
        def _price_empty(url):
            return {"status": 200, "data": []}

        with patch.object(providers, "_http_get_json", side_effect=_price_empty):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertFalse(result.ok())
        self.assertNotIn("增量", result.error)  # 不是 F2 的錯誤訊息(全量分支不受影響)


class IncrementalEmptyPriceResponseTriggersStaleRescueAtFetchLevelTest(_HistoryStoreIntegrationTestCase):
    """F2 的 `fetch()` 層級驗證:當 `fetch_tw` 因增量回空而判定失敗時,上層
    `fetch()` 既有的 stale-rescue(過期快取保命,工單 005/010 契約,此工單
    零改動)應該接手——只要有「過期」的 blob 快取可用,就標上「(過期快取)」
    回傳,不是讓使用者看到整檔消失的錯誤(恢復 014 之前的語意)。
    """

    def test_stale_blob_cache_rescued_with_marker_on_incremental_empty_response(self):
        # 1) 先寫一份「過期」的 blob 快取(providers 檔案快取層,4 天前在
        #    工單 013 的 EOD-aware 判斷下鐵定過期——必跨過至少一個平日 18:00
        #    邊界,沿用 tests/test_eod_cache.py 已鎖住的既有行為)。
        stale = providers.StockData(
            ticker="2330", market="TW", name="台積電", currency="TWD",
            price=2000.0, price_date="2025-06-01", source="FinMind",
        )
        providers._save_cache(stale)
        cache_path = providers._cache_path("TW", "2330")
        with open(cache_path, "r", encoding="utf-8") as f:
            blob = json.load(f)
        blob["_fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)

        # 2) history store 內也預先放一筆舊資料,讓這次走「增量」分支
        #    (而不是全量——全量回空不受 F2 影響,見上面的對照組)。
        old_row = ("2025-06-01", 2000.0)
        history_store.upsert_price(self._tmpdir, "TW", "2330", [old_row])
        window_start = _window_start(5)
        history_store.set_meta(self._tmpdir, "TW", "2330", "price", window_start,
                                "2025-06-01T00:00:00+00:00")

        def _price_empty(url):
            return {"status": 200, "data": []}

        with patch.object(providers, "_http_get_json", side_effect=_price_empty):
            result = providers.fetch(
                {"ticker": "2330", "market": "TW", "name": "台積電"},
                {"finmind_token": "", "finnhub_api_key": "", "cache_minutes": 15},
                history_years=5,
                use_cache=True,
            )

        self.assertTrue(result.ok())
        self.assertEqual(result.price, 2000.0)
        self.assertIn("(過期快取)", result.source)


# =========================================================================== #
#  D4 point 2:增量行為鑑別(URL router 捕捉 start_date)
# =========================================================================== #
class IncrementalStartDateDiscriminationTest(_HistoryStoreIntegrationTestCase):
    """鎖:D2 point 1–2 的全量/增量判斷有沒有真的接上——首次呼叫(無 meta)start
    必須是「這次要求窗起點」;第二次呼叫(meta 已存在、窗深度沒變)start 必須是
    store 內該序列目前的 MAX(date),不是又重新從窗起點抓一次。
    什麼突變會翻紅:如果全量/增量判斷退化成「永遠全量」(例如
    `history_store.get_meta` 被誤改成恆回傳 None,或判斷式邏輯寫錯),第二次
    呼叫的 start_date 會變回窗起點而不是 store MAX(date),第二個斷言翻紅
    (見 REPORT 的 mutation 重演)。
    """

    def test_second_fetch_start_date_is_store_max_date_not_window_start(self):
        window_start = _window_start(5)
        urls1 = []

        def _price_full(url):
            urls1.append(url)
            return {"status": 200, "data": [
                {"date": "2021-08-09", "close": 400.0},
                {"date": "2021-08-10", "close": 404.0},
            ]}

        with patch.object(providers, "_http_get_json", side_effect=_price_full):
            providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertEqual(len(urls1), 1)
        self.assertEqual(_start_date_of(urls1[0]), window_start)

        urls2 = []

        def _price_incremental(url):
            urls2.append(url)
            return {"status": 200, "data": [
                {"date": "2021-08-10", "close": 404.0},   # 重疊 1 筆(防尾筆修訂)
                {"date": "2021-08-11", "close": 406.0},
            ]}

        with patch.object(providers, "_http_get_json", side_effect=_price_incremental):
            providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertEqual(len(urls2), 1)
        self.assertEqual(_start_date_of(urls2[0]), "2021-08-10")  # store MAX(date),不是 window_start


# =========================================================================== #
#  D4 point 3(本單命脈):拆股跨兩次增量
# =========================================================================== #
class SplitAcrossIncrementsTest(_HistoryStoreIntegrationTestCase):
    """本工單命脈:拆股橫跨兩次增量抓取,組裝+還原後的序列必須與「一次性全量
    抓取再還原」的結果逐值相等。這是 `_back_adjust_tw` 必須在『組裝完整序列
    之後』才執行(而不是對每次增量各自局部執行)的直接證據。

    什麼突變會翻紅:如果有人把還原時機搬到「每次剛抓到的 raw_rows 各自局部
    執行後才 upsert」(即「還原時機移到存入前」),第一次增量(只看得到分割
    前兩筆,局部看不出任何斷層)存進 store 的會是「未還原」的原始值;第二次
    增量抓到分割後的資料時,即使局部有偵測到跳空,也無法回頭修正第一次已經
    存進 store、且未被納入這次 `_back_adjust_tw` 輸入的那兩筆——兩種情境算
    出來的序列會不同,`test_two_increments_matches_one_shot_full_fetch` 的逐值
    比較會抓到(REPORT 有實際 mutation 重演輸出)。

    工單 020:`FULL_RAW` 改在 `setUp` 相對「現在」動態生成(不再是類別屬性寫死
    絕對日期)——`fetch_tw` 的窗起點 `requested_start` 是 `now()` 每天往前滑動
    的相對值,寫死的舊絕對日期遲早會被滑出窗外,讓「store 讀回」與「這次剛抓到
    的 raw_rows」在窗邊界兩側產生不對稱(這正是工單 020 的根因,見該工單
    REPORT)。相對日期(now-20..now-17)永遠深居 years>=1 的任何窗內,不受執行
    當下的真實日期影響。
    """

    def setUp(self):
        super().setUp()
        base = datetime.now() - timedelta(days=20)
        d0, d1, d2, d3 = ((base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4))
        self.FULL_RAW = [
            (d0, 400.0),
            (d1, 404.0),
            (d2, 100.5),   # 分割日:r = 100.5/404.0 ≈ 0.2488 < 0.6 → 觸發還原
            (d3, 101.0),
        ]

    def _run_one_shot(self):
        tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_history_store_oneshot_")
        try:
            with patch.object(providers, "CACHE_DIR", tmpdir):
                def _price(url):
                    return {"status": 200, "data": [
                        {"date": d, "close": c} for d, c in self.FULL_RAW
                    ]}
                with patch.object(providers, "_http_get_json", side_effect=_price):
                    return providers.fetch_tw("2330", "台積電", years=5, token="", method="")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _run_two_increments(self):
        # 沿用 setUp 已經 patch 好的 self._tmpdir/providers.CACHE_DIR。
        def _price_1(url):
            return {"status": 200, "data": [
                {"date": d, "close": c} for d, c in self.FULL_RAW[:2]
            ]}
        with patch.object(providers, "_http_get_json", side_effect=_price_1):
            providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        def _price_2(url):
            return {"status": 200, "data": [
                {"date": d, "close": c} for d, c in self.FULL_RAW[1:]  # 含重疊 1 筆
            ]}
        with patch.object(providers, "_http_get_json", side_effect=_price_2):
            return providers.fetch_tw("2330", "台積電", years=5, token="", method="")

    def test_two_increments_matches_one_shot_full_fetch(self):
        one_shot = self._run_one_shot()
        two_increment = self._run_two_increments()

        self.assertTrue(one_shot.price_history)
        self.assertEqual(one_shot.price_history, two_increment.price_history)
        self.assertEqual(one_shot.price, two_increment.price)

        # 用獨立鎖住的 `_back_adjust_tw`(test_back_adjust.py)當 oracle,避免
        # 「兩邊剛好都算出同一個錯誤答案」這種假陽性比較沒有鑑別力。
        expected_adj_px, _ = providers._back_adjust_tw(self.FULL_RAW, [])
        self.assertEqual(one_shot.price_history, expected_adj_px)
        self.assertEqual(two_increment.price_history, expected_adj_px)
        # 確認真的偏離原始值(還原確實生效,不是 factor=1.0 的退化案例)。
        self.assertNotEqual(two_increment.price_history[0][1], self.FULL_RAW[0][1])


class ExDividendAcrossPriceIncrementsWithAlwaysFullDividendTest(_HistoryStoreIntegrationTestCase):
    """D4 point 3 的除息案例:大額除息造成的跳空,價格跨兩次增量抓取,不應被
    誤判為分割。**工單 014 reviewer 修正包 F4 更新**:配息本身已撤出增量路徑
    (恆全量抓取,見 `fetch_tw` yield_band 段落),所以這裡驗證兩件事——
    (1) 配息在兩次 `fetch_tw` 呼叫中都是全量(`start_date` 皆為窗起點,不是
    store MAX(ex_date)),(2) 即使價格是跨增量拼接、配息是恆全量重抓的兩種不同
    節奏,`_back_adjust_tw` 依然正確用 ex_date 排除這筆跳空,不誤判分割。

    鑑別力誠實註記(比照 test_back_adjust.py 的誠實揭露慣例):跳空與 ex_date
    同時落在價格的第二次增量批次內,所以就算把還原時機錯誤地搬到「每批次各自
    局部處理」,也可能局部就正確排除——對「還原時機」有強鑑別力的是上面的
    `SplitAcrossIncrementsTest`(跳空發生在批次邊界、需要回頭修正前一批已存的
    資料)。這裡驗證的是另一個獨立面向:price 走增量、dividend 走恆全量,兩種
    節奏搭配時 ex_date 排除邏輯依然正確。

    工單 020:`FULL_RAW`/`DIV` 改在 `setUp` 相對「現在」動態生成,理由同
    `SplitAcrossIncrementsTest`(見該類別 docstring 的根因說明)。
    """

    def setUp(self):
        super().setUp()
        base = datetime.now() - timedelta(days=20)
        d0, d1, d2, d3 = ((base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4))
        self.FULL_RAW = [
            (d0, 400.0),
            (d1, 404.0),
            (d2, 200.0),   # 跳空:r=200/404≈0.495<0.6,若非除息會誤判為分割
            (d3, 202.0),
        ]
        self.DIV = [(d2, 5.0)]  # 跳空當日除息 → 不應觸發分割還原

    def _price_router(self, rows):
        def _h(url):
            return {"status": 200, "data": [{"date": d, "close": c} for d, c in rows]}
        return _h

    def _div_router(self, rows, capture=None):
        def _h(url):
            if capture is not None:
                capture.append(url)
            return {"status": 200, "data": [
                {"CashExDividendTradingDate": ex, "CashEarningsDistribution": c} for ex, c in rows
            ]}
        return _h

    def _dispatch(self, price_rows, div_rows, div_capture=None):
        price_h = self._price_router(price_rows)
        div_h = self._div_router(div_rows, capture=div_capture)

        def _h(url):
            if "TaiwanStockPrice" in url:
                return price_h(url)
            if "TaiwanStockDividend" in url:
                return div_h(url)
            raise AssertionError(f"unexpected url: {url}")
        return _h

    def _run_one_shot(self):
        tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_history_store_oneshot_div_")
        try:
            with patch.object(providers, "CACHE_DIR", tmpdir):
                with patch.object(providers, "_http_get_json",
                                   side_effect=self._dispatch(self.FULL_RAW, self.DIV)):
                    return providers.fetch_tw("2330", "台積電", years=5, token="", method="yield_band")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _run_two_increments(self, div_urls_1=None, div_urls_2=None):
        with patch.object(providers, "_http_get_json",
                           side_effect=self._dispatch(self.FULL_RAW[:2], self.DIV, div_urls_1)):
            providers.fetch_tw("2330", "台積電", years=5, token="", method="yield_band")

        with patch.object(providers, "_http_get_json",
                           side_effect=self._dispatch(self.FULL_RAW[1:], self.DIV, div_urls_2)):
            return providers.fetch_tw("2330", "台積電", years=5, token="", method="yield_band")

    def test_two_increments_matches_one_shot_no_false_split(self):
        one_shot = self._run_one_shot()
        div_urls_1, div_urls_2 = [], []
        two_increment = self._run_two_increments(div_urls_1, div_urls_2)

        self.assertEqual(one_shot.price_history, two_increment.price_history)
        self.assertEqual(one_shot.div_history, two_increment.div_history)
        # 兩邊都應該「沒有被誤判為分割」——價格原樣未還原。
        self.assertEqual(one_shot.price_history, self.FULL_RAW)
        self.assertEqual(two_increment.price_history, self.FULL_RAW)

        # F4:配息兩次呼叫都應該是「全量」(start_date = 窗起點),不是遞增的
        # store MAX(ex_date)——即使價格那邊已經是第二次增量了。
        window_start = _window_start(5)
        self.assertEqual(len(div_urls_1), 1)
        self.assertEqual(len(div_urls_2), 1)
        self.assertEqual(_start_date_of(div_urls_1[0]), window_start)
        self.assertEqual(_start_date_of(div_urls_2[0]), window_start)


# =========================================================================== #
#  F4(reviewer 修正包 P2-1/P2-2):配息恆全量,不做增量
# =========================================================================== #
class DividendAlwaysFullNeverIncrementalTest(_HistoryStoreIntegrationTestCase):
    """鎖:配息(`yield_band`)已撤出 `_sync_and_assemble` 的增量路徑——即使
    `fetch_tw` 被連續呼叫兩次(第二次若走增量,理應以 store MAX(ex_date) 為
    start),配息的 `start_date` 兩次都必須是「這次要求的窗起點」,`d.div_history`
    直接是這次全量抓到的 raw 資料排序組裝(不經 store 讀取),完全比照 014
    之前的原始碼路徑。
    什麼突變會翻紅:如果有人「順手」把配息重新接回 `_sync_and_assemble`,第二次
    呼叫的 `start_date` 會變成 store 的 MAX(ex_date) 而不是窗起點,下面的斷言
    會抓到。
    """

    def _price(self, url):
        return {"status": 200, "data": [{"date": "2026-01-01", "close": 100.0}]}

    def test_second_call_dividend_start_date_still_window_start_not_incremental(self):
        window_start = _window_start(5)

        div_urls_1 = []

        def _router_1(url):
            if "TaiwanStockDividend" in url:
                div_urls_1.append(url)
                return {"status": 200, "data": [
                    {"CashExDividendTradingDate": "2025-01-15", "CashEarningsDistribution": 1.5}]}
            return self._price(url)

        with patch.object(providers, "_http_get_json", side_effect=_router_1):
            result1 = providers.fetch_tw("2330", "台積電", years=5, token="", method="yield_band")
        self.assertEqual(len(div_urls_1), 1)
        self.assertEqual(_start_date_of(div_urls_1[0]), window_start)
        self.assertEqual(result1.div_history, [("2025-01-15", 1.5)])

        div_urls_2 = []

        def _router_2(url):
            if "TaiwanStockDividend" in url:
                div_urls_2.append(url)
                return {"status": 200, "data": [
                    {"CashExDividendTradingDate": "2025-01-15", "CashEarningsDistribution": 1.5},
                    {"CashExDividendTradingDate": "2025-07-15", "CashEarningsDistribution": 1.8},
                ]}
            return self._price(url)

        with patch.object(providers, "_http_get_json", side_effect=_router_2):
            result2 = providers.fetch_tw("2330", "台積電", years=5, token="", method="yield_band")
        self.assertEqual(len(div_urls_2), 1)
        self.assertEqual(_start_date_of(div_urls_2[0]), window_start)  # 仍是窗起點,不是增量
        self.assertEqual(result2.div_history, [("2025-01-15", 1.5), ("2025-07-15", 1.8)])

        # store 仍然收到 best-effort 的 upsert(耐久備份),即使組裝不靠它。
        stored = history_store.get_dividend(self._tmpdir, "TW", "2330")
        self.assertIsNotNone(stored)
        self.assertIn(("2025-07-15", 1.8), stored)


class DuplicateExDateDividendSumPreservedTest(_HistoryStoreIntegrationTestCase):
    """鎖:同一個 `CashExDividendTradingDate` 若有多筆紀錄(FinMind 原始資料可能
    發生,例如同日有現金股利+法定盈餘公積兩筆分開的配息項目被拆成不同列),
    `d.div_history` 必須**保留**全部多列(不可被摺疊成一列),讓
    `d.annual_dividend`(`sum(c for ex,c in d.div_history if ex>cutoff)`)正確
    加總。這是 F4 選擇「不用 store 的 (market,ticker,ex_date) PK 組裝」的直接
    理由之一——若組裝改走 store 讀回(PK 覆蓋語意),重複 ex_date 只會剩最後
    一筆,SUM 會少算。
    什麼突變會翻紅:如果有人把配息組裝改回經過 store 的 upsert+get(PK 覆蓋),
    這裡的「兩筆保留」與加總金額斷言都會翻紅(少一筆、金額變小)。
    """

    def _price(self, url):
        return {"status": 200, "data": [{"date": "2026-01-01", "close": 100.0}]}

    def test_same_ex_date_two_rows_both_kept_and_summed(self):
        # 除息日用「現在往前 30 天」動態算出(而非寫死日期字串),確保不論這個
        # 測試在哪一天被執行,都必定落在 annual_dividend 的 365 天回看窗內
        # (寫死的舊日期字串,一旦跑測試的當下日期往前推移超過一年,cutoff 篩選
        # 會把它濾掉,讓斷言變成偽陰性——這裡刻意避開這個陷阱)。
        recent_ex_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        def _router(url):
            if "TaiwanStockDividend" in url:
                return {"status": 200, "data": [
                    {"CashExDividendTradingDate": recent_ex_date, "CashEarningsDistribution": 1.0},
                    {"CashExDividendTradingDate": recent_ex_date, "CashStatutorySurplus": 0.5},
                ]}
            return self._price(url)

        with patch.object(providers, "_http_get_json", side_effect=_router):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="yield_band")

        self.assertEqual(len(result.div_history), 2)  # 兩筆都保留,沒被摺疊
        self.assertEqual(sorted(c for _, c in result.div_history), [0.5, 1.0])
        cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        expected_sum = round(sum(c for ex, c in result.div_history if ex > cutoff), 4)
        self.assertEqual(result.annual_dividend, expected_sum)
        self.assertAlmostEqual(result.annual_dividend, 1.5)


class Yield0056StyleOfflineParityWithPre014Test(_HistoryStoreIntegrationTestCase):
    """reviewer 修正包 Gate 補充項:「另挑一檔 yield_band(如 0056)不實跑——用
    離線測試證明 dividend 路徑行為與 014 前逐位相同」。用一組模擬 0056 這類
    高頻配息 ETF 的合成資料(多筆配息、跨越 365 天 cutoff 邊界、含重複
    ex_date),驗證 `fetch_tw(method="yield_band")`(F4 之後)的配息路徑產出的
    `d.div_history`/`d.annual_dividend`,與獨立手算的 oracle——完全比照
    014 之前(=F4 之後,兩者程式碼路徑相同)的原始邏輯:直接對這次全量抓到的
    raw 資料 `sorted()`,`annual_dividend` 用 365 天回看窗 SUM——逐位相同。
    並且連續呼叫兩次,兩次的 `start_date` 都必須是窗起點,證明恆全量、不會
    像沒修的增量設計那樣在第二次悄悄改用 store MAX(ex_date)。
    """

    TICKER = "0056"

    def _distributions(self):
        """合成配息序列:13 筆,涵蓋 cutoff 邊界內外,含一組重複 ex_date(同日
        兩筆不同項目)。日期用「現在往前 N 天」動態算,不寫死絕對日期,避免
        測試在未來某天執行時跌出 365 天窗外而改變預期結果。"""
        now = datetime.now()
        offsets_and_cash = [
            (30, 0.08), (60, 0.08), (90, 0.09), (120, 0.085), (150, 0.09),
            (180, 0.08), (210, 0.075), (240, 0.08), (270, 0.085), (300, 0.08),
            (300, 0.01),   # 重複 ex_date(同日兩筆項目,驗證 SUM 語意不摺疊)
            (330, 0.09), (400, 0.10),   # 400 天前:超過 365 天窗,不應計入 annual_dividend
        ]
        return [((now - timedelta(days=d)).strftime("%Y-%m-%d"), c) for d, c in offsets_and_cash]

    def _finmind_dividend_payload(self, rows):
        return {"status": 200, "data": [
            {"CashExDividendTradingDate": ex, "CashEarningsDistribution": cash} for ex, cash in rows
        ]}

    def _expected_div_history_and_annual(self, rows):
        """獨立 oracle,逐字比照 fetch_tw yield_band 段落(F4 之後 = 014 之前)
        的原始邏輯。"""
        hist = sorted([(ex, cash) for ex, cash in rows if ex and cash])
        cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        annual = round(sum(c for ex, c in hist if ex > cutoff), 4)
        return hist, annual

    def test_0056_style_dividend_matches_hand_computed_oracle_and_always_full(self):
        rows = self._distributions()
        expected_hist, expected_annual = self._expected_div_history_and_annual(rows)
        window_start = _window_start(5)

        def _price(url):
            return {"status": 200, "data": [{"date": "2026-01-01", "close": 28.5}]}

        div_urls_1 = []

        def _router_1(url):
            if "TaiwanStockDividend" in url:
                div_urls_1.append(url)
                return self._finmind_dividend_payload(rows)
            return _price(url)

        with patch.object(providers, "_http_get_json", side_effect=_router_1):
            result1 = providers.fetch_tw(self.TICKER, "元大高股息", years=5, token="", method="yield_band")

        self.assertEqual(len(div_urls_1), 1)   # S8 契約:yield_band 恆 1 次配息呼叫
        self.assertEqual(_start_date_of(div_urls_1[0]), window_start)
        self.assertEqual(result1.div_history, expected_hist)
        self.assertEqual(result1.annual_dividend, expected_annual)

        # 第二次呼叫(模擬下一輪 blob 快取過期後的例行重抓):start_date 仍應是
        # 窗起點(恆全量),`d.div_history`/`d.annual_dividend` 逐位相同。
        div_urls_2 = []

        def _router_2(url):
            if "TaiwanStockDividend" in url:
                div_urls_2.append(url)
                return self._finmind_dividend_payload(rows)
            return _price(url)

        with patch.object(providers, "_http_get_json", side_effect=_router_2):
            result2 = providers.fetch_tw(self.TICKER, "元大高股息", years=5, token="", method="yield_band")

        self.assertEqual(len(div_urls_2), 1)
        self.assertEqual(_start_date_of(div_urls_2[0]), window_start)  # 仍是窗起點,不是增量
        self.assertEqual(result2.div_history, expected_hist)
        self.assertEqual(result2.annual_dividend, expected_annual)


# =========================================================================== #
#  D4 point 4:P2-4 根治(013 記錄的已知限制)
# =========================================================================== #
class HistoryYearsDeepensTriggersBackfillTest(_HistoryStoreIntegrationTestCase):
    """鎖:history_years 5→10,窗起點變早,應觸發全量回填(不是增量),且
    `series_meta.requested_start` 更新成新的、更早的值。
    什麼突變會翻紅:如果全量/增量判斷式把「窗變深」誤判成走增量分支(例如
    比較條件寫反或漏比較),第二次呼叫的 URL start_date 斷言與 meta 斷言至少
    一個會翻紅(仍會是窗更淺時期的舊 start_date,或 meta 不會更新)。
    """

    def test_deepening_window_refetches_full_from_new_earlier_start(self):
        window_start_5y = _window_start(5)
        window_start_10y = _window_start(10)
        self.assertLess(window_start_10y, window_start_5y)  # 自我檢查:字串序=時間序

        urls1 = []

        def _price_1(url):
            urls1.append(url)
            return {"status": 200, "data": [{"date": "2021-08-10", "close": 400.0}]}

        with patch.object(providers, "_http_get_json", side_effect=_price_1):
            providers.fetch_tw("2330", "台積電", years=5, token="", method="")
        self.assertEqual(_start_date_of(urls1[0]), window_start_5y)

        meta_before = history_store.get_meta(self._tmpdir, "TW", "2330", "price")
        self.assertEqual(meta_before["requested_start"], window_start_5y)

        urls2 = []

        def _price_2(url):
            urls2.append(url)
            return {"status": 200, "data": [
                {"date": "2016-08-10", "close": 200.0},
                {"date": "2021-08-10", "close": 400.0},
            ]}

        with patch.object(providers, "_http_get_json", side_effect=_price_2):
            result = providers.fetch_tw("2330", "台積電", years=10, token="", method="")

        self.assertEqual(len(urls2), 1)
        self.assertEqual(_start_date_of(urls2[0]), window_start_10y)  # 全量,不是增量

        meta_after = history_store.get_meta(self._tmpdir, "TW", "2330", "price")
        self.assertEqual(meta_after["requested_start"], window_start_10y)  # 記錄更新成更早的值

        # 工單 014 reviewer 修正包 F5(c):不能只驗證「URL 有沒有打對」,還要驗證
        # 「回傳給呼叫端的序列」真的含有加深後才抓到的更早日期資料——否則就算
        # URL/meta 斷言都對,萬一組裝階段漏接了這筆全量回填的資料,這裡也要能
        # 抓到(什麼突變會翻紅:組裝改用只取 raw_rows 的某個子集,或合併鍵算錯
        # 漏掉這筆更早日期)。
        self.assertIn("2016-08-10", [d for d, _ in result.price_history])


class MethodSwitchAddsPerDatasetTest(_HistoryStoreIntegrationTestCase):
    """鎖:method price_band→pe_band 應該新增 PER dataset 的全量抓取(因為 per
    的 series_meta 從沒被寫過);再次以 pe_band 呼叫(第三次)時,PER 應該走
    增量(start = store MAX(date)),不是又整段重抓——這正是 013 記錄的 P2-4
    (cache 不含 method 維度)在台股路徑的根治證據。

    工單 014 reviewer 修正包 F3:第二、三次呼叫的 PER 最新日期與 `_price` 固定
    回傳的價格日期只差 0~1 天,遠在 10 天護欄門檻內,補上 `d.per`/
    `d.trailing_eps`/`d.dividend_yield` 的派生斷言(先前只驗證呼叫次數/URL,
    親手造出的這組小幅時間差卻從未斷言過三個派生欄位是否正確——本次補齊)。
    """

    def _price(self, url):
        return {"status": 200, "data": [{"date": "2021-08-10", "close": 400.0}]}

    def test_switch_to_pe_band_fetches_per_full_then_incremental_on_reuse(self):
        window_start = _window_start(5)

        # 第一次:price_band(method=""),只碰 price,不碰 per。
        with patch.object(providers, "_http_get_json", side_effect=self._price):
            providers.fetch_tw("2330", "台積電", years=5, token="", method="")
        self.assertIsNone(history_store.get_meta(self._tmpdir, "TW", "2330", "per"))

        # 第二次:切到 pe_band。per 是首次接觸 → 全量(start = 窗起點)。
        per_urls = []

        def _router_2(url):
            if "TaiwanStockPER" in url:
                per_urls.append(url)
                return {"status": 200, "data": [
                    {"date": "2021-08-10", "PER": 18.0, "dividend_yield": 2.0},
                ]}
            return self._price(url)

        with patch.object(providers, "_http_get_json", side_effect=_router_2):
            result2 = providers.fetch_tw("2330", "台積電", years=5, token="", method="pe_band")
        self.assertEqual(len(per_urls), 1)
        self.assertEqual(_start_date_of(per_urls[0]), window_start)  # 全量:PER 首次抓
        # F3 護欄:PER 最新日期("2021-08-10")與 price_date("2021-08-10")同一天
        # (差 0 天)<=10 天門檻 → 應正常派生。
        self.assertEqual(result2.per, 18.0)
        self.assertAlmostEqual(result2.dividend_yield, 0.02)
        self.assertEqual(result2.trailing_eps, round(400.0 / 18.0, 4))

        # 第三次:仍是 pe_band。per 這次應該走增量(不是又整段重抓)。
        per_urls2 = []

        def _router_3(url):
            if "TaiwanStockPER" in url:
                per_urls2.append(url)
                return {"status": 200, "data": [
                    {"date": "2021-08-11", "PER": 18.2, "dividend_yield": 2.0},
                ]}
            return self._price(url)

        with patch.object(providers, "_http_get_json", side_effect=_router_3):
            result3 = providers.fetch_tw("2330", "台積電", years=5, token="", method="pe_band")
        self.assertEqual(len(per_urls2), 1)
        self.assertEqual(_start_date_of(per_urls2[0]), "2021-08-10")  # 增量:store MAX(date)
        # F3 護欄:PER 最新日期("2021-08-11")與 price_date("2021-08-10")差 1 天
        # <=10 天門檻 → 仍應正常派生(不是護欄過度保守擋掉合理的小幅時間差)。
        self.assertEqual(result3.per, 18.2)
        self.assertAlmostEqual(result3.dividend_yield, 0.02)
        self.assertEqual(result3.trailing_eps, round(400.0 / 18.2, 4))


class PerDerivationFreshnessGuardTest(_HistoryStoreIntegrationTestCase):
    """F3(P1-2b 護欄)專屬測試:PER 派生欄位(`d.per`/`d.trailing_eps`/
    `d.dividend_yield`)只在 PER 最新一筆與 `d.price_date` 相差 <=10 天時才派生,
    否則三者維持 `None`(`d.per_history` 河流圖序列不受此限制)。

    什麼突變會翻紅:如果有人把 F3 護欄拿掉(退回「只要 per_rows 非空就派生」),
    `test_stale_per_beyond_10_days_derivation_stays_none` 會從 None 翻成非
    None 的舊行為,斷言失敗。
    """

    def _price(self, url):
        return {"status": 200, "data": [{"date": "2026-01-30", "close": 500.0}]}

    def test_stale_per_beyond_10_days_derivation_stays_none(self):
        # reviewer 的 S1 情境:PER 最新一筆停在半年前("2025-08-01"),price 是
        # 今天("2026-01-30")——遠超過 10 天門檻。
        def _router(url):
            if "TaiwanStockPER" in url:
                return {"status": 200, "data": [
                    {"date": "2025-08-01", "PER": 15.0, "dividend_yield": 1.0},
                ]}
            return self._price(url)

        with patch.object(providers, "_http_get_json", side_effect=_router):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="pe_band")

        self.assertTrue(result.ok())            # 護欄只影響派生欄位,不影響整體成功
        self.assertIsNone(result.per)
        self.assertIsNone(result.trailing_eps)
        self.assertIsNone(result.dividend_yield)
        # per_history(河流圖用的歷史序列)不受此限制,仍應含這筆歷史資料。
        self.assertEqual(result.per_history, [("2025-08-01", 15.0)])

    def test_fresh_per_within_10_days_derivation_normal(self):
        # 對照組:PER 最新一筆與 price_date 只差 3 天,應正常派生。
        def _router(url):
            if "TaiwanStockPER" in url:
                return {"status": 200, "data": [
                    {"date": "2026-01-27", "PER": 20.0, "dividend_yield": 2.5},
                ]}
            return self._price(url)

        with patch.object(providers, "_http_get_json", side_effect=_router):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="pe_band")

        self.assertTrue(result.ok())
        self.assertEqual(result.per, 20.0)
        self.assertAlmostEqual(result.dividend_yield, 0.025)
        self.assertEqual(result.trailing_eps, round(500.0 / 20.0, 4))


# =========================================================================== #
#  D4 point 4 完整回切:pe_band → price_band → pe_band(reviewer 修正包 F3)
# =========================================================================== #
class MethodFullRoundTripPeBandThenPriceBandThenPeBandTest(_HistoryStoreIntegrationTestCase):
    """SPEC D4-4 完整回切驗證:pe_band → price_band → pe_band。第二次
    (price_band)完全不該碰 PER dataset;第三次(切回 pe_band)per 的
    series_meta 在第一次之後沒被動過,應該走增量(store MAX(date)),不是
    又整段重抓,且派生欄位(F3 護欄下)依然正確。
    """

    def _price(self, url):
        return {"status": 200, "data": [{"date": "2021-08-10", "close": 400.0}]}

    def test_pe_band_then_price_band_then_pe_band_third_call_is_incremental(self):
        window_start = _window_start(5)

        # 第一次:pe_band。per 首次接觸 → 全量。
        per_urls_1 = []

        def _router_1(url):
            if "TaiwanStockPER" in url:
                per_urls_1.append(url)
                return {"status": 200, "data": [
                    {"date": "2021-08-10", "PER": 18.0, "dividend_yield": 2.0},
                ]}
            return self._price(url)

        with patch.object(providers, "_http_get_json", side_effect=_router_1):
            result1 = providers.fetch_tw("2330", "台積電", years=5, token="", method="pe_band")
        self.assertEqual(len(per_urls_1), 1)
        self.assertEqual(_start_date_of(per_urls_1[0]), window_start)
        self.assertEqual(result1.per, 18.0)

        # 第二次:切到 price_band。完全不該碰 TaiwanStockPER。
        def _router_2(url):
            if "TaiwanStockPER" in url:
                raise AssertionError("price_band 不應該打 TaiwanStockPER")
            return self._price(url)

        with patch.object(providers, "_http_get_json", side_effect=_router_2):
            providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        # per 的 meta 應該還停在第一次寫入的狀態,完全沒被第二次呼叫動過。
        meta_untouched = history_store.get_meta(self._tmpdir, "TW", "2330", "per")
        self.assertEqual(meta_untouched["requested_start"], window_start)

        # 第三次:切回 pe_band。per 應該走增量(start = store MAX(date)),
        # 不是又整段重抓("不重抓"的證據)。
        per_urls_3 = []

        def _router_3(url):
            if "TaiwanStockPER" in url:
                per_urls_3.append(url)
                return {"status": 200, "data": [
                    {"date": "2021-08-11", "PER": 18.5, "dividend_yield": 2.1},
                ]}
            return self._price(url)

        with patch.object(providers, "_http_get_json", side_effect=_router_3):
            result3 = providers.fetch_tw("2330", "台積電", years=5, token="", method="pe_band")
        self.assertEqual(len(per_urls_3), 1)
        self.assertEqual(_start_date_of(per_urls_3[0]), "2021-08-10")  # 增量,不是窗起點
        # F3 護欄下,1 天差距仍正確派生。
        self.assertEqual(result3.per, 18.5)
        self.assertAlmostEqual(result3.dividend_yield, 0.021)


# =========================================================================== #
#  D4 point 5:窗切片
# =========================================================================== #
class WindowSlicingTest(_HistoryStoreIntegrationTestCase):
    """鎖:store 內已經有比這次要求窗口更早的資料(模擬先前抓過更長歷史),這次
    只要求較淺的窗口 → 組裝回傳的序列起點必須正確落在要求窗內,不能把 store
    裡更早的資料也混進來;但 store 本身仍完整保留那筆更早資料(切片只影響
    「這次回傳給呼叫端」的組裝結果)。

    工單 020:`in_window_date` 改為相對「現在」動態生成(不再寫死
    "2021-08-10")——這裡的斷言直接拿 `in_window_date` 與動態算出的
    `window_start_5y` 比較(`all(d >= window_start_5y ...)`),寫死的絕對日期
    一旦被真實日期滑動的窗起點追上/超過就會翻紅(工單 020 根因同款;`in_window_date`
    當時剛好落在窗邊界上,次日即引爆,見工單 020 REPORT)。`very_old_date` 維持
    寫死("2010-01-01")不受影響——窗起點只會隨時間單調前移,一旦排除在窗外就
    永遠排除,不會反向「追不上」而重新落入窗內。
    """

    def test_shallower_request_slices_to_requested_window_not_full_store_depth(self):
        window_start_5y = _window_start(5)
        very_old_date = "2010-01-01"
        in_window_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        # 直接預先灌入 store(模擬先前抓過更長歷史),並把 meta.requested_start
        # 設成比這次 5 年窗更早 → 這次呼叫應該走「增量」分支(窗沒有變深)。
        history_store.upsert_price(self._tmpdir, "TW", "2330",
                                    [(very_old_date, 111.0), (in_window_date, 400.0)])
        history_store.set_meta(self._tmpdir, "TW", "2330", "price",
                                requested_start="2005-01-01", last_success="2020-01-01T00:00:00+00:00")

        urls = []

        def _price(url):
            urls.append(url)
            return {"status": 200, "data": [{"date": in_window_date, "close": 400.0}]}

        with patch.object(providers, "_http_get_json", side_effect=_price):
            result = providers.fetch_tw("2330", "台積電", years=5, token="", method="")

        self.assertEqual(len(urls), 1)
        self.assertEqual(_start_date_of(urls[0]), in_window_date)  # 增量:store MAX(date)

        # 工單 014 reviewer 修正包 F5(b):`all(...)` 對空序列恆真,先前沒有明確
        # 的非空斷言,萬一組裝壞成永遠回傳 [] 也會被 all() 誤判通過——先鎖住
        # 「真的有資料」,再驗證起點正確。
        self.assertTrue(result.price_history)
        # 回傳序列起點正確落在 5 年窗內,不含 2010 年那筆更早的資料。
        self.assertTrue(all(d >= window_start_5y for d, _ in result.price_history))
        self.assertNotIn(very_old_date, [d for d, _ in result.price_history])

        # store 本身仍完整保留那筆更早資料。
        full_store_rows = history_store.get_price(self._tmpdir, "TW", "2330")
        self.assertIn(very_old_date, [d for d, _ in full_store_rows])


# =========================================================================== #
#  D4 point 6:US 快照 DELETE+INSERT
# =========================================================================== #
class UsSnapshotFullReplaceTest(_HistoryStoreIntegrationTestCase):
    """鎖:美股兩次成功抓取,store 內該 ticker 的 `daily_price` 應該是「第二次
    全量」,不是新舊混拼;讀路徑(`fetch_us` 回傳值)完全由這次 yfinance 結果
    決定,不受 store 影響(逐位相同於改動前的邏輯)。
    什麼突變會翻紅:如果 `replace_us_snapshot` 被誤改成 upsert(而非先
    DELETE 再 INSERT),第二次抓取後 store 會同時看到兩次的資料混在一起。
    """

    def test_two_successful_fetches_store_holds_only_second_snapshot(self):
        fake_yf_1 = _fake_yf_module([("2026-01-01", 100.0), ("2026-01-02", 101.0)])
        with patch.dict(sys.modules, {"yfinance": fake_yf_1}):
            result1 = providers.fetch_us("AAPL", "Apple", years=1)
        self.assertEqual(result1.price_history, [("2026-01-01", 100.0), ("2026-01-02", 101.0)])
        self.assertEqual(
            history_store.get_price(self._tmpdir, "US", "AAPL"),
            [("2026-01-01", 100.0), ("2026-01-02", 101.0)],
        )

        # 第二次抓到「完全不同」的序列(模擬 auto_adjust 回溯改寫 + 新的一天)。
        fake_yf_2 = _fake_yf_module([("2026-02-01", 555.0)])
        with patch.dict(sys.modules, {"yfinance": fake_yf_2}):
            result2 = providers.fetch_us("AAPL", "Apple", years=1)
        self.assertEqual(result2.price_history, [("2026-02-01", 555.0)])  # 讀路徑只看這次結果

        # store 應該「只剩第二次」,不是新舊混拼。
        stored = history_store.get_price(self._tmpdir, "US", "AAPL")
        self.assertEqual(stored, [("2026-02-01", 555.0)])


# =========================================================================== #
#  補強:fetch_tw 層級的呼叫數 regression(S8 的互補鎖點)
# =========================================================================== #
class CallCountAtFetchTwLevelTest(_HistoryStoreIntegrationTestCase):
    """鎖:工單 014 的 store 接線沒有偷加 FinMind 呼叫次數——直接測 `fetch_tw`
    (tests/test_providers_fallback.py 的 S8 是走 `fetch()` 入口),兩處互補。
    price_band=1、pe_band=2、yield_band=2,無論走全量或增量分支,呼叫數都應
    該不變(分支只影響 start_date 參數值,不影響次數)。
    """

    def _router(self, urls):
        def _h(url):
            urls.append(url)
            if "TaiwanStockPrice" in url:
                return {"status": 200, "data": [{"date": "2026-01-01", "close": 100.0}]}
            if "TaiwanStockPER" in url:
                return {"status": 200, "data": [
                    {"date": "2026-01-01", "PER": 15.0, "dividend_yield": 1.0}]}
            if "TaiwanStockDividend" in url:
                return {"status": 200, "data": [
                    {"CashExDividendTradingDate": "2026-01-01", "CashEarningsDistribution": 1.0}]}
            raise AssertionError(f"unexpected url: {url}")
        return _h

    def test_price_band_one_pe_band_two_yield_band_two(self):
        for method, expected_calls in (("", 1), ("pe_band", 2), ("yield_band", 2)):
            with self.subTest(method=method):
                tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_history_store_callcount_")
                try:
                    with patch.object(providers, "CACHE_DIR", tmpdir):
                        urls = []
                        with patch.object(providers, "_http_get_json", side_effect=self._router(urls)):
                            result = providers.fetch_tw("2330", "台積電", years=5, token="", method=method)
                        self.assertTrue(result.ok())
                        self.assertEqual(len(urls), expected_calls)
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

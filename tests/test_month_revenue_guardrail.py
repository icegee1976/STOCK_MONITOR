"""月營收假設健全度護欄(工單 018)。完全離線(FinMind schema 已由 executor
在 REPORT 記錄的 1 次真實呼叫確認過欄位,此檔全部 mock,0 API)。

拍板 SPEC(見 docs/tickets/018-monthly-revenue-guardrail.md):A+B 並示、A 法
(TTM 實際 vs 假設軌跡)設 -15% 警告門檻、B 法(TTM YoY vs 假設 CAGR)純資訊
不設門檻,範圍限台股含 `derive` 的標的,只警告不自動改 watchlist。

涵蓋:
1. `providers._monthrev_start_date` 純函數(近 30 個月窗起點,整數月運算)。
2. `valuation._compute_revenue_check` 手算 oracle(A 法 years_frac 年中錨定、
   -15% 門檻雙側邊界、B 法 TTM YoY、資料不足 <12/<24 降級)。
3. `valuation.format_revenue_check_line` 顯示字串組裝(兆/億/萬量級)。
4. `valuation.compute_zones` 接線:revenue_check 附加、-15% 警告、**zones 逐位
   不變**(命脈 gate:同 cfg 有/無 month_revenue 跑兩次,zones dict
   assertEqual)、非 pe_band/非 derive 一律 revenue_check=None。
5. `providers` 月營收獨立快取(TTL 7 天,假時鐘)、FinMind 抓取失敗靜默、
   欄位解析防呆(revenue_year/revenue_month,非 date 欄位)。
6. `providers.fetch()`/`fetch_tw()` 接線呼叫數:非 TW+derive 標的 0 新增呼叫、
   derive 標的 cache-miss 時恰好多打 1 次 MonthRevenue、月營收快取命中時
   即使價格快取失效也不重打 MonthRevenue、MonthRevenue 失敗不影響價格結果。
7. StockData blob 相容 round-trip(新欄位 `month_revenue`)。
8. CLI 顯示行(report.py::render_stock_card,HAS_RICH=False + redirect_stdout
   手法,比照 tests/test_report_roi_display.py / test_quality_warnings.py)。

mock 紀律同 tests/test_eod_cache.py / tests/test_quality_warnings.py:
CACHE_DIR 隔離到 tempdir、urlopen 保險絲、_http_get_json 逐條 mock、
`_now_tpe` 可注入假時鐘。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from aimonitor import providers, report
from aimonitor.providers import StockData, TPE
from aimonitor.valuation import (
    compute_zones,
    _compute_revenue_check,
    _fmt_ntd,
    format_revenue_check_line,
)


# =========================================================================== #
#  共用 fixture 建構工具
# =========================================================================== #
def _seq_months(start_y, start_m, n):
    """回傳連續 n 個 "YYYY-MM" 字串,從 start_y-start_m 起。"""
    out = []
    y, m = start_y, start_m
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m = 1
            y += 1
    return out


def _uniform_rows(monthly_value, start_y=2025, start_m=1, n=12):
    """預設窗口 2025-01~2025-12(整年,終月=12 月)。工單 018 修正包 H1 之後,
    `years_frac` 用「TTM 終月 − base_year 12 月」量測,終月落在 12 月時
    exponent 是乾淨整數(對 base_year=2024 的 fixture 而言 years_frac=1.0),
    數字比終月落在年中好算/好核對,故預設窗口改用整年制(舊版預設 2024-07
    起、終月=2025-06,是配合已修正的舊公式而選的窗口,現已不適用)。"""
    return [(m, monthly_value) for m in _seq_months(start_y, start_m, n)]


def _two_block_rows(prior_val, last_val, start_y=2024, start_m=1):
    """24 個月,前 12 個月(整年)= prior_val、後 12 個月(次一整年)= last_val
    (皆為每月固定值)。預設 2024 全年 + 2025 全年,終月=2025-12(理由同
    `_uniform_rows` 的 docstring)。"""
    months = _seq_months(start_y, start_m, 24)
    return [(m, prior_val) for m in months[:12]] + [(m, last_val) for m in months[12:]]


def _derive_cfg(base_revenue=1_000_000.0, cagr=0.20, base_year=2024,
                 net_margin=0.20, shares=1000.0, target_year=2029):
    return {"base_revenue": base_revenue, "net_margin": net_margin,
            "revenue_cagr": cagr, "shares": shares, "base_year": base_year,
            "target_year": target_year}


def _stock_cfg_pe_derive(market="TW", **derive_overrides):
    return {
        "ticker": "TESTMV", "market": market, "name": "測試月營收標的",
        "valuation": {
            "method": "pe_band",
            "derive": _derive_cfg(**derive_overrides),
            "pe_bands": {"super_bargain": 10, "cheap": 15, "fair": 20,
                         "expensive": 25, "euphoria": 30},
        },
    }


# 2330 黃金值 fixture(從 tests/test_golden_valuation.py 抄錄,刻意獨立複製而非
# 跨檔 import——比照該檔自己的原則「fixture 快照寫死,不跨檔耦合」)。
STOCK_CFG_2330 = {
    "ticker": "2330", "market": "TW", "name": "台積電 TSMC",
    "valuation": {
        "method": "pe_band",
        "derive": {
            "base_revenue": 2.89e12, "net_margin": 0.41362,
            "revenue_cagr": 0.24, "shares": 25.93e9, "target_year": 2029,
        },
        "pe_bands": {"super_bargain": 12.81, "cheap": 16.49, "fair": 23.85,
                     "expensive": 27.53, "euphoria": 31.2},
    },
}


# =========================================================================== #
#  1. providers._monthrev_start_date 純函數
# =========================================================================== #
class MonthrevStartDateTest(unittest.TestCase):
    def _tpe(self, y, m, d):
        return datetime(y, m, d, 12, 0, tzinfo=TPE)

    def test_2026_08_matches_real_finmind_call_used_in_report(self):
        # 對照工單 018 REPORT 記錄的真實 FinMind 呼叫(2026-08-20,now=2026-08):
        # start_date="2024-02-01",實測回傳 31 筆(2024-01~2026-07 營收月)。
        self.assertEqual(providers._monthrev_start_date(self._tpe(2026, 8, 20)), "2024-02-01")

    def test_january_wraps_to_previous_year(self):
        self.assertEqual(providers._monthrev_start_date(self._tpe(2026, 1, 15)), "2023-07-01")

    def test_december_stays_within_current_year_minus_window(self):
        self.assertEqual(providers._monthrev_start_date(self._tpe(2026, 12, 1)), "2024-06-01")

    def test_default_now_uses_now_tpe_when_not_injected(self):
        fake_now = self._tpe(2026, 8, 20)
        with patch.object(providers, "_now_tpe", return_value=fake_now):
            self.assertEqual(providers._monthrev_start_date(), "2024-02-01")


# =========================================================================== #
#  2. valuation._compute_revenue_check 手算 oracle
# =========================================================================== #
class RevenueCheckAMethodOracleTest(unittest.TestCase):
    """base_revenue=1,000,000 / cagr=0.20 / base_year=2024,TTM 終月=2025-06
    → months_elapsed=(2025-2024)*12+(6-6)=12 → years_frac=1.0 →
    expected_ttm = 1,000,000 × 1.2^1.0 = 1,200,000(python 實測值,見 REPORT)。
    """

    DERIVE = {"base_revenue": 1_000_000.0, "revenue_cagr": 0.20, "base_year": 2024}

    def test_expected_ttm_years_frac_one(self):
        rc = _compute_revenue_check(_uniform_rows(100000.0), self.DERIVE)
        self.assertEqual(rc["expected"], 1200000.0)

    def test_dev_exactly_minus15_is_boundary_not_below(self):
        # monthly=85000 → ttm=1,020,000 → dev=(1020000/1200000-1)*100=-15.0 精確。
        rc = _compute_revenue_check(_uniform_rows(85000.0), self.DERIVE)
        self.assertEqual(rc["ttm"], 1020000.0)
        self.assertEqual(rc["dev_pct"], -15.0)
        self.assertFalse(rc["dev_pct"] < -15.0)  # SPEC:dev < -15% 才警告,-15.0 本身不算

    def test_dev_just_below_minus15_triggers_warn_condition(self):
        # monthly=84990 → ttm=1,019,880 → dev=-15.01(python 實測,見 REPORT)。
        rc = _compute_revenue_check(_uniform_rows(84990.0), self.DERIVE)
        self.assertEqual(rc["ttm"], 1019880.0)
        self.assertEqual(rc["dev_pct"], -15.01)
        self.assertTrue(rc["dev_pct"] < -15.0)

    def test_dev_clearly_negative_20pct(self):
        rc = _compute_revenue_check(_uniform_rows(80000.0), self.DERIVE)
        self.assertEqual(rc["dev_pct"], -20.0)

    def test_dev_exact_match_zero(self):
        rc = _compute_revenue_check(_uniform_rows(100000.0), self.DERIVE)
        self.assertEqual(rc["dev_pct"], 0.0)

    def test_dev_positive_ahead_of_assumption(self):
        rc = _compute_revenue_check(_uniform_rows(105000.0), self.DERIVE)
        self.assertEqual(rc["dev_pct"], 5.0)

    def test_cagr_pct_reflects_assumption(self):
        rc = _compute_revenue_check(_uniform_rows(100000.0), self.DERIVE)
        self.assertEqual(rc["cagr_pct"], 20.0)


class RevenueCheckYearsFracAnchorTest(unittest.TestCase):
    """修正包 H1(P1-1 根修):`years_frac` 錨定公式改為
    `((ttm_end_year-base_year)*12 + (ttm_end_month-12)) / 12`——base_revenue
    是 base_year **全年 12 個月合計**,TTM_actual 也是「近 12 個月合計」,
    兩個等長窗口比較必須用同一個參照基準點(這裡統一取「窗口終點」),不能像
    舊公式那樣只把 base 那一側錨到年中(base_year 6月底)、TTM 那一側卻仍用
    終點——兩側基準不對稱,系統性多算了半年(見 aimonitor/valuation.py
    `_compute_revenue_check` docstring 的完整推導)。

    python 實測值(見 REPORT「手算 oracle」段,舊值已標示為修正前的錯誤版本
    供追溯):monthly value 固定為 100000,只關心 `expected` 欄位(隨 TTM 終月
    變化)。
    """

    DERIVE = {"base_revenue": 1_000_000.0, "revenue_cagr": 0.20, "base_year": 2024}

    def test_ttm_end_at_base_year_december_self_consistency_zero_years(self):
        # TTM 終月=2024-12(窗口 2024-01..2024-12,恰為 base_year 整個日曆年)
        # → months=(2024-2024)*12+(12-12)=0 → years_frac=0.0 →
        # expected=base_revenue 原封(1+cagr)^0=1,不受 cagr 值影響。
        rows = _uniform_rows(100000.0, start_y=2024, start_m=1, n=12)
        rc = _compute_revenue_check(rows, self.DERIVE)
        self.assertEqual(rc["expected"], 1000000.0)

    def test_ttm_end_half_year_before_anchor_negative_years_frac(self):
        # TTM 終月=2024-06(窗口 2023-07..2024-06)→ months=(2024-2024)*12+
        # (6-12)=-6 → years_frac=-0.5(允許負值:TTM 窗口早於 base_year 12月
        # 這個參照終點半年)→ expected=1,000,000×1.2^-0.5=912870.9291752769
        rows = _uniform_rows(100000.0, start_y=2023, start_m=7, n=12)
        rc = _compute_revenue_check(rows, self.DERIVE)
        self.assertEqual(rc["expected"], 912870.93)

    def test_ttm_end_one_year_after_anchor(self):
        # TTM 終月=2025-12(窗口 2025-01..2025-12)→ months=12 →
        # years_frac=1.0 → expected=1,000,000×1.2=1,200,000.0
        rows = _uniform_rows(100000.0, start_y=2025, start_m=1, n=12)
        rc = _compute_revenue_check(rows, self.DERIVE)
        self.assertEqual(rc["expected"], 1200000.0)

    def test_ttm_end_one_and_half_years_after_anchor(self):
        # TTM 終月=2026-06(窗口 2025-07..2026-06)→ months=18 →
        # years_frac=1.5 → expected=1,000,000×1.2^1.5=1314534.1380123985
        rows = _uniform_rows(100000.0, start_y=2025, start_m=7, n=12)
        rc = _compute_revenue_check(rows, self.DERIVE)
        self.assertEqual(rc["expected"], 1314534.14)

    def test_ttm_end_two_years_after_anchor(self):
        # TTM 終月=2026-12(窗口 2026-01..2026-12)→ months=24 →
        # years_frac=2.0 → expected=1,000,000×1.2^2=1,440,000.0
        rows = _uniform_rows(100000.0, start_y=2026, start_m=1, n=12)
        rc = _compute_revenue_check(rows, self.DERIVE)
        self.assertEqual(rc["expected"], 1440000.0)


class RevenueCheckSelfConsistencyExactZeroDevTest(unittest.TestCase):
    """修正包 H1 item(a):自恰測試——TTM 窗口恰為 base_year 整個日曆年時,
    `TTM_actual` 與 `expected_ttm` 理論上是同一件事的兩種算法(前者直接加總
    這 12 個月的實際值、後者用 base_revenue 往回推 0 年),只要 TTM_actual
    剛好等於 base_revenue,dev_pct 必須**精確** 0.0(不是近似值)。用
    base_revenue=1,200,000(可被 12 整除)搭配每月 100,000 均分,兩邊都是
    乾淨數字,便於精確比對。

    mutation 自查(見 REPORT):把 `_compute_revenue_check` 的 `months_elapsed`
    公式從 `(...+ (ttm_end_month-12))` 改回舊版 `(...+ (ttm_end_month-6))`,
    這裡的 `years_frac` 會變成 0.5(而非 0.0),`expected` 隨之偏離
    base_revenue,`dev_pct` 不再是 0.0 而是 -8.71 左右——此測試會翻紅,直接
    鑑別這個回歸。
    """

    def test_ttm_equals_base_year_total_gives_exact_zero_dev(self):
        derive = {"base_revenue": 1_200_000.0, "revenue_cagr": 0.20, "base_year": 2024}
        rows = _uniform_rows(100000.0, start_y=2024, start_m=1, n=12)  # 2024 全年,合計 1,200,000
        rc = _compute_revenue_check(rows, derive)
        self.assertEqual(rc["ttm"], 1200000.0)
        self.assertEqual(rc["expected"], 1200000.0)
        self.assertEqual(rc["dev_pct"], 0.0)

    def test_self_consistency_holds_regardless_of_cagr_value(self):
        # years_frac=0 時 (1+cagr)^0=1 恆成立,dev 應該仍是 0,不管 cagr 填多少
        # (驗證這個自恰性質不是「剛好選到某個 cagr 才成立」的巧合)。
        for cagr in (0.0, 0.05, 0.24, 0.9, -0.3):
            with self.subTest(cagr=cagr):
                derive = {"base_revenue": 1_200_000.0, "revenue_cagr": cagr, "base_year": 2024}
                rows = _uniform_rows(100000.0, start_y=2024, start_m=1, n=12)
                rc = _compute_revenue_check(rows, derive)
                self.assertEqual(rc["dev_pct"], 0.0)


class RevenueCheckMultiYearToleranceSanityTest(unittest.TestCase):
    """修正包 H1 item(b):合成一段「每個日曆年總營收精確等於
    base×(1+cagr)^(年-base_year)、年內均分到 12 個月」的跨年序列,取一個
    **不對齊年底**的 TTM 窗口(跨兩個日曆年,各佔一半)。因為真實假設是連續
    複利(逐月成長),但這裡的合成資料是「每年一次跳階、年內打平」的階梯函數,
    非年底對齊的窗口理論上會跟連續複利曲線有一點點差距(階梯 vs 連續的中間
    地帶失真),這裡驗證這個差距在合理容差內(±0.5%),而不是要求精確為 0
    (只有年底對齊的窗口才會精確為 0,見上面的自恰測試)。

    python 實測值(見 REPORT):base_revenue=1,200,000、cagr=0.20、
    base_year=2024;2024 全年月營收=100,000/mo、2025 全年=120,000/mo、
    2026 全年=144,000/mo(各自等於 base×(1+cagr)^(年-2024) 均分 12 個月)。
    TTM 窗口取 2025-07~2026-06(2025 後半年 + 2026 前半年各 6 個月)。
    """

    def test_non_year_aligned_ttm_window_within_half_percent_tolerance(self):
        base_revenue, cagr, base_year = 1_200_000.0, 0.20, 2024
        derive = {"base_revenue": base_revenue, "revenue_cagr": cagr, "base_year": base_year}
        y2024, y2025, y2026 = (base_revenue * (1 + cagr) ** k for k in (0, 1, 2))
        # 只補到 2026-06(6 個月,非整年)——這樣 `rows[-12:]`(compute_revenue_check
        # 內部近12月窗口)才會自然落在 2025-07~2026-06(跨年、非年底對齊);若這裡
        # 誤放整年 2026(n=12),`rows[-12:]` 會直接等於整個 2026 年(年底對齊),
        # 就測不到「跨年窗口」這個情境了(執行期已用這個手誤驗證過一次,見 REPORT)。
        rows = (
            _uniform_rows(y2024 / 12, start_y=2024, start_m=1, n=12)
            + _uniform_rows(y2025 / 12, start_y=2025, start_m=1, n=12)
            + _uniform_rows(y2026 / 12, start_y=2026, start_m=1, n=6)
        )
        rc = _compute_revenue_check(rows, derive)
        self.assertIsNotNone(rc)
        # python 實測 ttm=1,584,000.0 / expected=1,577,440.97 / dev_pct=0.42
        # (見 REPORT),遠在 ±0.5% 容差內。
        self.assertEqual(rc["ttm"], 1584000.0)
        self.assertEqual(rc["expected"], 1577440.97)
        self.assertTrue(-0.5 <= rc["dev_pct"] <= 0.5,
                         f"dev_pct={rc['dev_pct']} 超出 ±0.5% 容差")


class RevenueCheckBMethodYoyOracleTest(unittest.TestCase):
    """24 個月 TTM YoY 手算 oracle(python 實測值見 REPORT)。"""

    DERIVE = {"base_revenue": 1_000_000.0, "revenue_cagr": 0.20, "base_year": 2024}

    def test_informational_no_warning_combo(self):
        # 前12月=80000/mo(960,000)、後12月=100000/mo(1,200,000):
        # dev=0.0(不警告),yoy=(1200000/960000-1)*100=25.0。
        rows = _two_block_rows(80000.0, 100000.0)
        rc = _compute_revenue_check(rows, self.DERIVE)
        self.assertEqual(rc["ttm"], 1200000.0)
        self.assertEqual(rc["dev_pct"], 0.0)
        self.assertEqual(rc["yoy_pct"], 25.0)
        self.assertEqual(rc["cagr_pct"], 20.0)

    def test_warning_and_informational_both_present_combo(self):
        # 前12月=75000/mo(900,000)、後12月=80000/mo(960,000):
        # dev=-20.0(警告),yoy=(960000/900000-1)*100=6.67(四捨五入)。
        rows = _two_block_rows(75000.0, 80000.0)
        rc = _compute_revenue_check(rows, self.DERIVE)
        self.assertEqual(rc["dev_pct"], -20.0)
        self.assertEqual(rc["yoy_pct"], 6.67)

    def test_string_typed_yaml_numbers_are_coerced(self):
        # 比照 aimonitor/valuation.py::_forward_eps 已知的 YAML 無正負號指數字串
        # 問題(watchlist.yaml 實際存的是字串 "2.89e12"),_compute_revenue_check
        # 必須一樣做 float() 強制轉換,不能假設 derive 值已經是 float。
        derive_str = {"base_revenue": "1000000.0", "revenue_cagr": "0.20", "base_year": 2024}
        rows = _two_block_rows(80000.0, 100000.0)
        rc = _compute_revenue_check(rows, derive_str)
        self.assertEqual(rc["dev_pct"], 0.0)
        self.assertEqual(rc["yoy_pct"], 25.0)


class RevenueCheckDataSufficiencyTest(unittest.TestCase):
    DERIVE = {"base_revenue": 1_000_000.0, "revenue_cagr": 0.20, "base_year": 2024}

    def test_below_12_months_returns_none(self):
        rows = _uniform_rows(85000.0, n=11)
        self.assertIsNone(_compute_revenue_check(rows, self.DERIVE))

    def test_exactly_12_months_is_sufficient_for_a_method(self):
        rows = _uniform_rows(85000.0, n=12)
        rc = _compute_revenue_check(rows, self.DERIVE)
        self.assertIsNotNone(rc)
        self.assertIsNone(rc["yoy_pct"])  # 不足 24 個月

    def test_18_months_still_insufficient_for_b_method(self):
        # 6 個月墊底(值不影響 A/B 計算,因為 A/B 只看最後 12/24 筆;墊底
        # 2024-01~06 與主區塊 2025-01~12 之間本身有縫隙,但不影響這個測試——
        # 這裡驗證的是「總數 18<24」這個絕對數量門檻,不是連續性,見下面
        # `RevenueCheckWindowContinuityGuardTest` 才是專門測連續性護欄的類別)
        # + 既有 12 月區塊。
        padding = [(m, 50000.0) for m in _seq_months(2024, 1, 6)]
        rows = padding + _uniform_rows(80000.0)
        rc = _compute_revenue_check(rows, self.DERIVE)
        self.assertEqual(rc["dev_pct"], -20.0)  # A 法不受影響(rows[-12:] 本身連續)
        self.assertIsNone(rc["yoy_pct"])         # B 法仍不足 24 個月

    def test_24_months_enables_b_method(self):
        rows = _two_block_rows(80000.0, 100000.0)
        rc = _compute_revenue_check(rows, self.DERIVE)
        self.assertIsNotNone(rc["yoy_pct"])

    def test_duplicate_month_last_value_wins(self):
        # 覆寫窗口第一個月(_uniform_rows 預設窗口=2025-01~2025-12,第一個月是
        # 2025-01)——覆寫不新增月份(仍是 12 個唯一月,H2 連續性檢查不受影響)。
        rows = _uniform_rows(85000.0) + [(_seq_months(2025, 1, 1)[0], 999999.0)]
        rc = _compute_revenue_check(rows, self.DERIVE)
        # 2025-01 被覆寫成 999999,其餘 11 個月仍是 85000 → ttm 需反映覆寫後的值。
        expected_ttm_actual = 999999.0 + 85000.0 * 11
        self.assertEqual(rc["ttm"], expected_ttm_actual)


# =========================================================================== #
#  2b. 修正包 H2(P2-1):近 12/24 個月窗口連續性護欄(缺月靜默降級)
# =========================================================================== #
class RevenueCheckWindowContinuityGuardTest(unittest.TestCase):
    """「近 12 個月」的語意是連續 12 個日曆月,不是「隨便湊滿 12 筆資料」——
    資料源可能因為抓取視窗邊界/API 暫時性問題導致中間缺一個月,若照樣加總會
    產生看似合理實則偏差的 TTM 數字。缺月時 `_compute_revenue_check` 整組回
    None(A 法,因為 TTM_actual 本身就不可信);B 法額外檢查 `rows[-24:-12]`
    (前 12 個月)自己是否連續,缺月時只單獨降級 `yoy_pct=None`,不影響已經
    算好的 A 法結果(見 aimonitor/valuation.py `_compute_revenue_check`
    docstring 與 `_is_consecutive_window`)。

    mutation 自查(見 REPORT):把 `_compute_revenue_check` 內
    `if not _is_consecutive_window(rows, 12): return None` 整段刪除、以及把
    `if _is_consecutive_window(prior12, 12):` 改成恆真,下面「缺月 → None /
    yoy_pct=None」兩個測試會翻紅(缺月被靜默用「不連續的 12 筆」湊出一個
    看似合理、實則錯誤的 TTM/YoY 數字)。
    """

    DERIVE = {"base_revenue": 1_000_000.0, "revenue_cagr": 0.20, "base_year": 2024}

    def test_gap_inside_last_12_window_returns_none(self):
        # 12 個「唯一月」,但中間缺 2025-06(用 2026-01 補到滿 12 筆數量,製造
        # 「總數看起來夠、但不是連續 12 個月」的情境——刻意跟「筆數不足」的
        # test_below_12_months_returns_none 區分開,這裡測的是連續性,不是數量)。
        months = [m for m in _seq_months(2025, 1, 12) if m != "2025-06"] + ["2026-01"]
        rows = [(m, 85000.0) for m in months]
        self.assertEqual(len(rows), 12)  # 確認真的是「數量夠但有缺口」,不是筆數不足
        self.assertIsNone(_compute_revenue_check(rows, self.DERIVE))

    def test_continuous_12_months_unaffected_regression(self):
        # 對照組:既有連續案例(現有 fixture 本就連續)確認仍正常計算,證明
        # H2 不是「所有案例一律擋掉」的過度保守實作。
        rc = _compute_revenue_check(_uniform_rows(85000.0), self.DERIVE)
        self.assertIsNotNone(rc)
        self.assertEqual(rc["dev_pct"], -15.0)

    def test_gap_inside_prior_12_window_degrades_only_yoy_not_dev(self):
        # last12(2025-01~12,_uniform_rows 預設窗口)本身連續、dev 正常計算;
        # prior12(2024-01~12)中間缺 2024-06(用 2023-01 補到滿 12 筆維持總數
        # 24)→ yoy_pct 應降級為 None,但 dev_pct 完全不受影響(A 法只看
        # last12,不管 prior12 好壞——兩者連續性各自獨立判斷)。
        # python 實測(見 REPORT):dev_pct=0.0(last12 monthly=100000 剛好等於
        # expected)、yoy_pct=None。
        prior_months = [m for m in _seq_months(2024, 1, 12) if m != "2024-06"] + ["2023-01"]
        rows = [(m, 80000.0) for m in prior_months] + _uniform_rows(100000.0)
        self.assertEqual(len(rows), 24)
        rc = _compute_revenue_check(rows, self.DERIVE)
        self.assertIsNotNone(rc)
        self.assertEqual(rc["dev_pct"], 0.0)
        self.assertIsNone(rc["yoy_pct"])

    def test_gap_at_boundary_between_prior12_and_last12_does_not_block_yoy(self):
        # H2 刻意不要求 prior12 與 last12 之間零縫隙相接(見
        # aimonitor/valuation.py `_compute_revenue_check` 內的設計取捨註解)
        # ——這裡驗證兩個窗口各自連續、但窗口「之間」本身有一個月縫隙
        # (prior12 結尾 2024-05,last12 開頭 2024-07,中間缺 2024-06)時,
        # yoy_pct 依然正常計算(不因為窗口間縫隙被擋掉)。python 實測(見
        # REPORT):yoy_pct=25.0(=(100000×12)/(80000×12)−1)×100,與有無縫隙
        # 無關,純粹兩個窗口各自的合計比較)。
        prior12 = _uniform_rows(80000.0, start_y=2023, start_m=6, n=12)   # 2023-06~2024-05
        last12 = _uniform_rows(100000.0, start_y=2024, start_m=7, n=12)  # 2024-07~2025-06(縫隙:缺2024-06)
        rows = prior12 + last12
        rc = _compute_revenue_check(rows, self.DERIVE)
        self.assertIsNotNone(rc)
        self.assertEqual(rc["yoy_pct"], 25.0)


# =========================================================================== #
#  3. valuation.format_revenue_check_line / _fmt_ntd 顯示字串
# =========================================================================== #
class FmtNtdMagnitudeTest(unittest.TestCase):
    def test_none_is_em_dash(self):
        self.assertEqual(_fmt_ntd(None), "—")

    def test_trillion_scale_uses_zhao(self):
        self.assertEqual(_fmt_ntd(4580000000000.0), "4.58兆")

    def test_hundred_million_scale_uses_yi(self):
        self.assertEqual(_fmt_ntd(2500000000.0), "25.0億")

    def test_ten_thousand_scale_uses_wan(self):
        self.assertEqual(_fmt_ntd(960000.0), "96.0萬")

    def test_small_value_uses_plain_comma_format(self):
        self.assertEqual(_fmt_ntd(1234.0), "1,234")


class FormatRevenueCheckLineTest(unittest.TestCase):
    def test_none_returns_empty_string(self):
        self.assertEqual(format_revenue_check_line(None), "")

    def test_full_a_and_b_present(self):
        rc = {"ttm": 960000.0, "expected": 1200000.0, "dev_pct": -20.0,
              "yoy_pct": 6.67, "cagr_pct": 20.0}
        self.assertEqual(
            format_revenue_check_line(rc),
            "TTM 96.0萬 vs 假設 120.0萬(-20.0%);YoY +6.7% vs 假設 CAGR 20.0%",
        )

    def test_yoy_insufficient_shows_fallback_with_cagr(self):
        rc = {"ttm": 1020000.0, "expected": 1200000.0, "dev_pct": -15.0,
              "yoy_pct": None, "cagr_pct": 20.0}
        self.assertEqual(
            format_revenue_check_line(rc),
            "TTM 102.0萬 vs 假設 120.0萬(-15.0%);YoY 資料不足(需24個月);假設 CAGR 20.0%",
        )

    def test_trillion_scale_line(self):
        rc = {"ttm": 4580000000000.0, "expected": 4520000000000.0, "dev_pct": 1.33,
              "yoy_pct": 32.22, "cagr_pct": 24.0}
        self.assertEqual(
            format_revenue_check_line(rc),
            "TTM 4.58兆 vs 假設 4.52兆(+1.3%);YoY +32.2% vs 假設 CAGR 24.0%",
        )


# =========================================================================== #
#  4. compute_zones 接線:revenue_check 附加 + -15% 警告 + 逐位不變命脈 gate
# =========================================================================== #
class ComputeZonesRevenueCheckIntegrationTest(unittest.TestCase):
    def test_warning_appended_with_exact_wording_below_threshold(self):
        cfg = _stock_cfg_pe_derive()
        data = types.SimpleNamespace(price=None, trailing_eps=None,
                                      month_revenue=_uniform_rows(80000.0))  # dev=-20.0
        result = compute_zones(cfg, data, {})
        self.assertIsNotNone(result["revenue_check"])
        self.assertEqual(result["revenue_check"]["dev_pct"], -20.0)
        self.assertIn(
            "近 12 月實際營收較假設軌跡低 20.0%;"
            "forward EPS 假設可能過高,便宜價可能因此偏高,請檢視 watchlist 假設。",
            result["warnings"],
        )

    def test_no_warning_at_exact_minus15_boundary(self):
        cfg = _stock_cfg_pe_derive()
        data = types.SimpleNamespace(price=None, trailing_eps=None,
                                      month_revenue=_uniform_rows(85000.0))  # dev=-15.0 exactly
        result = compute_zones(cfg, data, {})
        self.assertEqual(result["revenue_check"]["dev_pct"], -15.0)
        self.assertEqual(result["warnings"], [])

    def test_no_warning_when_ahead_of_assumption(self):
        cfg = _stock_cfg_pe_derive()
        data = types.SimpleNamespace(price=None, trailing_eps=None,
                                      month_revenue=_uniform_rows(105000.0))  # dev=+5.0
        result = compute_zones(cfg, data, {})
        self.assertEqual(result["warnings"], [])

    def test_revenue_check_none_when_month_revenue_below_12(self):
        cfg = _stock_cfg_pe_derive()
        data = types.SimpleNamespace(price=None, trailing_eps=None,
                                      month_revenue=_uniform_rows(80000.0, n=11))
        result = compute_zones(cfg, data, {})
        self.assertIsNone(result["revenue_check"])
        self.assertEqual(result["warnings"], [])

    def test_revenue_check_none_when_data_has_no_month_revenue_attribute(self):
        # 沒有 month_revenue 屬性(舊呼叫端/測試 stub)→ getattr 防禦,不炸例外。
        cfg = _stock_cfg_pe_derive()
        data = types.SimpleNamespace(price=None, trailing_eps=None)
        result = compute_zones(cfg, data, {})
        self.assertIsNone(result["revenue_check"])

    def test_revenue_check_none_when_forward_eps_direct_no_derive(self):
        cfg = {
            "ticker": "X", "market": "TW", "name": "X",
            "valuation": {"method": "pe_band", "forward_eps": 10.0, "target_year": 2029,
                          "pe_bands": {"super_bargain": 10, "cheap": 15, "fair": 20,
                                       "expensive": 25, "euphoria": 30}},
        }
        data = types.SimpleNamespace(price=None, trailing_eps=None,
                                      month_revenue=_uniform_rows(80000.0))
        result = compute_zones(cfg, data, {})
        self.assertIsNone(result["revenue_check"])
        self.assertEqual(result["warnings"], [])

    def test_revenue_check_key_present_and_none_for_non_pe_band_method(self):
        cfg = {
            "ticker": "Y", "market": "TW", "name": "Y",
            "valuation": {"method": "price_band", "bands": {
                "super_bargain": 10, "cheap": 15, "fair": 20, "expensive": 25, "euphoria": 30}},
        }
        data = types.SimpleNamespace(price=100.0, trailing_eps=None,
                                      month_revenue=_uniform_rows(80000.0))
        result = compute_zones(cfg, data, {})
        self.assertIn("revenue_check", result)
        self.assertIsNone(result["revenue_check"])

    def test_revenue_check_none_for_us_market_even_with_derive_and_month_revenue(self):
        # 修正包 H4(P3-3):market gate 防呆。即使(不現實地)一支美股 cfg 帶了
        # pe_band+derive 且 data 帶了看起來合格的 month_revenue(>=12 個月),
        # market!="TW" 就是不能算 revenue_check——新台幣「元」單位的月營收數字
        # 不該被拿去跟美股假設比較。
        cfg = _stock_cfg_pe_derive(market="US")
        data = types.SimpleNamespace(price=None, trailing_eps=None,
                                      month_revenue=_uniform_rows(80000.0))  # 若是 TW 會是 dev=-20.0
        result = compute_zones(cfg, data, {})
        self.assertIsNone(result["revenue_check"])
        self.assertEqual(result["warnings"], [])

    def test_revenue_check_none_for_lowercase_market_variant_still_recognized_as_tw(self):
        # 反向確認:market gate 用 .upper() 比較,"tw"(小寫)仍應視為 TW、
        # 正常計算 revenue_check——不是「非精確等於大寫 TW 字串就一律擋掉」。
        cfg = _stock_cfg_pe_derive(market="tw")
        data = types.SimpleNamespace(price=None, trailing_eps=None,
                                      month_revenue=_uniform_rows(80000.0))
        result = compute_zones(cfg, data, {})
        self.assertIsNotNone(result["revenue_check"])
        self.assertEqual(result["revenue_check"]["dev_pct"], -20.0)


class ZonesUnchangedByMonthRevenuePresenceTest(unittest.TestCase):
    """命脈 gate(SPEC 明文要求):同一份 cfg,有/無 month_revenue 各跑一次
    compute_zones,zones dict 必須逐位相同——即使 month_revenue 觸發了 -15%
    警告,也絕對不能反過來影響 zones 的任何一個數字。黃金值(cheap=2228.57 /
    super_bargain=1731.23)亦一併鎖住,證明本工單沒有動到黃金值一絲一毫。
    """

    def test_zones_identical_with_and_without_month_revenue(self):
        data_without = types.SimpleNamespace(price=2460.0, trailing_eps=None)
        # 刻意選一組會觸發 -15% 警告的月營收(base_revenue=2.89e12、cagr=0.24、
        # base_year 預設 2024,TTM 終月=2025-06 → 修正包 H1 公式:
        # months=(2025-2024)*12+(6-12)=6 → years_frac=0.5 →
        # expected=2.89e12×1.24^0.5≈3.218167801715...e12;每月 2e11 →
        # ttm=2.4e12 → dev_pct≈-25.42(python 實測,見 REPORT),明顯 <-15%。
        # (H1 之前的舊公式算出 years_frac=1.0/expected≈3.5836e12/dev≈-25.4x
        # 附近的不同數字,但一樣 <-15%,此測試本身不需要跟著改動,只更正註解。)
        data_with = types.SimpleNamespace(
            price=2460.0, trailing_eps=None,
            month_revenue=_uniform_rows(2e11, start_y=2024, start_m=7, n=12),
        )
        result_without = compute_zones(STOCK_CFG_2330, data_without, {})
        result_with = compute_zones(STOCK_CFG_2330, data_with, {})

        self.assertEqual(result_without["zones"], result_with["zones"])
        # 黃金值本身逐位不變(對照 tests/test_golden_valuation.py)。
        self.assertEqual(result_with["zones"]["cheap"], 2228.57)
        self.assertEqual(result_with["zones"]["super_bargain"], 1731.23)
        self.assertEqual(result_without["anchor"], 135.147)
        self.assertEqual(result_with["anchor"], 135.147)
        # 但 warnings 應該有差異(有月營收那次應該多一條 -15% 護欄警告)。
        self.assertEqual(result_without["warnings"], [])
        self.assertTrue(any("近 12 月實際營收較假設軌跡低" in w for w in result_with["warnings"]))
        self.assertIsNone(result_without["revenue_check"])
        self.assertIsNotNone(result_with["revenue_check"])


# =========================================================================== #
#  5. providers 月營收獨立快取(TTL 7 天,假時鐘)
# =========================================================================== #
class MonthrevCacheTTLTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_monthrev_cache_")
        self._cache_patch = patch.object(providers, "CACHE_DIR", self._tmpdir)
        self._cache_patch.start()

    def tearDown(self):
        self._cache_patch.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_with_age(self, ticker, rows, age: timedelta, now: datetime | None = None):
        providers._save_monthrev_cache(ticker, rows)
        path = providers._monthrev_cache_path(ticker)
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
        base = now if now is not None else datetime.now(timezone.utc)
        blob["_fetched_at"] = (base - age).isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)

    def test_missing_file_returns_none(self):
        self.assertIsNone(providers._load_monthrev_cache("9999"))

    def test_corrupted_json_returns_none_not_raise(self):
        path = providers._monthrev_cache_path("9999")
        os.makedirs(self._tmpdir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        self.assertIsNone(providers._load_monthrev_cache("9999"))

    def test_fresh_cache_within_ttl_returns_rows(self):
        self._write_with_age("2330", [("2026-06", 100.0)], timedelta(days=6))
        rows = providers._load_monthrev_cache("2330")
        self.assertEqual(rows, [("2026-06", 100.0)])

    def test_cache_exactly_at_ttl_boundary_is_still_fresh(self):
        # 恰好 7 天(= MONTHREV_CACHE_TTL_MINUTES 分鐘)→ 邊界含在新鮮那側(> 判斷)。
        age = timedelta(minutes=providers.MONTHREV_CACHE_TTL_MINUTES)
        fake_now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        self._write_with_age("2330", [("2026-06", 100.0)], age, now=fake_now)
        rows = providers._load_monthrev_cache("2330", now=fake_now)
        self.assertIsNotNone(rows)

    def test_cache_just_past_ttl_boundary_is_expired(self):
        age = timedelta(minutes=providers.MONTHREV_CACHE_TTL_MINUTES + 1)
        fake_now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        self._write_with_age("2330", [("2026-06", 100.0)], age, now=fake_now)
        rows = providers._load_monthrev_cache("2330", now=fake_now)
        self.assertIsNone(rows)

    def test_8_days_old_is_expired(self):
        self._write_with_age("2330", [("2026-06", 100.0)], timedelta(days=8))
        self.assertIsNone(providers._load_monthrev_cache("2330"))


# =========================================================================== #
#  6. providers.fetch_month_revenue 靜默失敗 + 欄位解析防呆
# =========================================================================== #
class FetchMonthRevenueSilentFailureTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_monthrev_fetch_")
        self._cache_patch = patch.object(providers, "CACHE_DIR", self._tmpdir)
        self._cache_patch.start()
        self._urlopen_patch = patch.object(
            providers.urllib.request, "urlopen",
            side_effect=AssertionError("real network! _http_get_json 應該被 mock 攔住"),
        )
        self._urlopen_patch.start()
        self._sleep_patch = patch.object(providers.time, "sleep")
        self._sleep_patch.start()

    def tearDown(self):
        self._sleep_patch.stop()
        self._urlopen_patch.stop()
        self._cache_patch.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_http_failure_returns_empty_list_not_raise(self):
        with patch.object(providers, "_http_get_json",
                           side_effect=RuntimeError("FinMind 掛了(fake)")):
            rows = providers.fetch_month_revenue("2330", token="")
        self.assertEqual(rows, [])

    def test_http_failure_does_not_write_cache(self):
        with patch.object(providers, "_http_get_json",
                           side_effect=RuntimeError("FinMind 掛了(fake)")):
            providers.fetch_month_revenue("2330", token="")
        self.assertFalse(os.path.exists(providers._monthrev_cache_path("2330")))

    def test_success_writes_cache_second_call_zero_http(self):
        def _resp(url):
            return {"status": 200, "data": [
                {"date": "2024-02-01", "stock_id": "2330", "country": "Taiwan",
                 "revenue": 100000000000.0, "revenue_month": 1, "revenue_year": 2024,
                 "create_time": ""},
            ]}

        with patch.object(providers, "_http_get_json", side_effect=_resp) as mock_http:
            rows1 = providers.fetch_month_revenue("2330", token="")
            self.assertEqual(mock_http.call_count, 1)
            rows2 = providers.fetch_month_revenue("2330", token="")
            self.assertEqual(mock_http.call_count, 1)  # 第二次命中快取,沒有再打

        self.assertEqual(rows1, [("2024-01", 100000000000.0)])
        self.assertEqual(rows1, rows2)

    def test_uses_revenue_year_month_not_date_field(self):
        # 實測 FinMind 的 date 欄位是「發布月」(營收所屬月的次月),不能直接用;
        # 這裡刻意讓 date 與 revenue_year/revenue_month 不一致,驗證解析用的是後者。
        def _resp(url):
            return {"status": 200, "data": [
                {"date": "2099-12-01", "revenue": 500.0,
                 "revenue_month": 3, "revenue_year": 2024, "create_time": ""},
            ]}

        with patch.object(providers, "_http_get_json", side_effect=_resp):
            rows = providers.fetch_month_revenue("2330", token="")
        self.assertEqual(rows, [("2024-03", 500.0)])

    def test_malformed_rows_are_skipped_gracefully(self):
        def _resp(url):
            return {"status": 200, "data": [
                {"revenue_year": 2024, "revenue_month": 1, "revenue": 100.0},   # 好
                {"revenue_year": None, "revenue_month": 2, "revenue": 100.0},   # 缺年
                {"revenue_year": 2024, "revenue_month": 13, "revenue": 100.0},  # 月份非法
                {"revenue_year": 2024, "revenue_month": 3, "revenue": 0},       # 營收 0
                {"revenue_year": 2024, "revenue_month": 4, "revenue": -50.0},   # 營收負
                {"revenue_year": "bad", "revenue_month": 5, "revenue": 100.0},  # 年份非數字
                {"revenue_year": 2024, "revenue_month": 6, "revenue": 200.0},   # 好
            ]}

        with patch.object(providers, "_http_get_json", side_effect=_resp):
            rows = providers.fetch_month_revenue("2330", token="")
        self.assertEqual(rows, [("2024-01", 100.0), ("2024-06", 200.0)])

    def test_duplicate_month_dedup_last_wins_and_sorted(self):
        def _resp(url):
            return {"status": 200, "data": [
                {"revenue_year": 2024, "revenue_month": 2, "revenue": 100.0},
                {"revenue_year": 2024, "revenue_month": 1, "revenue": 50.0},
                {"revenue_year": 2024, "revenue_month": 1, "revenue": 999.0},  # 覆寫上一筆
            ]}

        with patch.object(providers, "_http_get_json", side_effect=_resp):
            rows = providers.fetch_month_revenue("2330", token="")
        self.assertEqual(rows, [("2024-01", 999.0), ("2024-02", 100.0)])


# =========================================================================== #
#  7. providers._needs_month_revenue 抓取範圍防呆
# =========================================================================== #
class NeedsMonthRevenueGatingTest(unittest.TestCase):
    """修正包 H3:`_needs_month_revenue` 現在要求 TW + `method=="pe_band"` +
    `derive` 三者皆真(SPEC 計算範圍本就限 pe_band+derive)。"""

    def test_tw_with_derive_is_true(self):
        cfg = {"ticker": "2330", "market": "TW",
               "valuation": {"method": "pe_band", "derive": {"base_revenue": 1}}}
        self.assertTrue(providers._needs_month_revenue(cfg))

    def test_tw_lowercase_market_is_true(self):
        cfg = {"ticker": "2330", "market": "tw",
               "valuation": {"method": "pe_band", "derive": {"base_revenue": 1}}}
        self.assertTrue(providers._needs_month_revenue(cfg))

    def test_tw_without_derive_is_false(self):
        cfg = {"ticker": "2317", "market": "TW", "valuation": {"method": "price_band"}}
        self.assertFalse(providers._needs_month_revenue(cfg))

    def test_tw_without_valuation_key_is_false(self):
        cfg = {"ticker": "2317", "market": "TW"}
        self.assertFalse(providers._needs_month_revenue(cfg))

    def test_us_with_derive_like_block_is_false(self):
        cfg = {"ticker": "NVDA", "market": "US",
               "valuation": {"method": "pe_band", "derive": {"base_revenue": 1}}}
        self.assertFalse(providers._needs_month_revenue(cfg))

    def test_empty_derive_dict_is_false(self):
        cfg = {"ticker": "2330", "market": "TW",
               "valuation": {"method": "pe_band", "derive": {}}}
        self.assertFalse(providers._needs_month_revenue(cfg))

    def test_derive_present_but_method_not_pe_band_is_false(self):
        # H3 新增:derive 區塊存在,但 method 不是 pe_band(例如手動/未來組出的
        # 非常態組合)→ 0 呼叫,不該打 MonthRevenue(SPEC 範圍本就限 pe_band)。
        cfg = {"ticker": "2330", "market": "TW",
               "valuation": {"method": "price_band", "derive": {"base_revenue": 1}}}
        self.assertFalse(providers._needs_month_revenue(cfg))

    def test_derive_present_method_missing_is_false(self):
        # method 完全沒填(空字串預設)+ derive 存在 → 同樣要被 H3 的新 method
        # 條件擋下,不是只有「method 明確等於別的字串」才算數。
        cfg = {"ticker": "2330", "market": "TW", "valuation": {"derive": {"base_revenue": 1}}}
        self.assertFalse(providers._needs_month_revenue(cfg))


# =========================================================================== #
#  8. providers.fetch()/fetch_tw() 接線呼叫數(整合,mock 紀律同 test_eod_cache.py)
# =========================================================================== #
class FetchWiringCallCountTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_monthrev_wiring_")
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

    def _providers_cfg(self, **overrides):
        cfg = {"finmind_token": "", "finnhub_api_key": "", "cache_minutes": 15}
        cfg.update(overrides)
        return cfg

    @staticmethod
    def _price_resp(url):
        return {"status": 200, "data": [{"date": "2026-08-10", "close": 600.0}]}

    @staticmethod
    def _per_resp(url):
        return {"status": 200, "data": [{"date": "2026-08-10", "PER": 20.0, "dividend_yield": 1.5}]}

    @staticmethod
    def _monthrev_resp(url):
        rows = [{"revenue_year": 2024, "revenue_month": m, "revenue": 1e11 + m}
                for m in range(1, 13)]
        return {"status": 200, "data": rows}

    def _router(self, monthrev_calls):
        def _dispatch(url):
            if "TaiwanStockMonthRevenue" in url:
                monthrev_calls.append(url)
                return self._monthrev_resp(url)
            if "TaiwanStockPER" in url:
                return self._per_resp(url)
            if "TaiwanStockPrice" in url:
                return self._price_resp(url)
            raise AssertionError(f"unexpected FinMind dataset url: {url}")
        return _dispatch

    def test_non_derive_tw_ticker_never_calls_month_revenue(self):
        monthrev_calls = []
        cfg = {"ticker": "2317", "market": "TW", "name": "鴻海"}  # 無 valuation → method=""
        with patch.object(providers, "_http_get_json", side_effect=self._router(monthrev_calls)):
            result = providers.fetch(cfg, self._providers_cfg(), history_years=5, use_cache=True)
        self.assertTrue(result.ok())
        self.assertEqual(monthrev_calls, [])
        self.assertEqual(result.month_revenue, [])

    def test_pe_band_without_derive_never_calls_month_revenue(self):
        # method=="pe_band" 但沒有 derive(forward_eps 直填)→ 仍不該打 MonthRevenue。
        monthrev_calls = []
        cfg = {"ticker": "2317", "market": "TW", "name": "鴻海",
               "valuation": {"method": "pe_band", "forward_eps": 10.0}}
        with patch.object(providers, "_http_get_json", side_effect=self._router(monthrev_calls)):
            result = providers.fetch(cfg, self._providers_cfg(), history_years=5, use_cache=True)
        self.assertTrue(result.ok())
        self.assertEqual(monthrev_calls, [])

    def test_derive_present_but_non_pe_band_method_never_calls_month_revenue(self):
        # 修正包 H3:derive 區塊存在,但 method 不是 pe_band(price_band)→ 0 呼叫,
        # 端對端經 fetch() 接線確認(不只是 _needs_month_revenue 這個純函數本身)。
        monthrev_calls = []
        cfg = {"ticker": "2330", "market": "TW", "name": "台積電",
               "valuation": {"method": "price_band", "derive": {"base_revenue": 1e12}}}
        with patch.object(providers, "_http_get_json", side_effect=self._router(monthrev_calls)):
            result = providers.fetch(cfg, self._providers_cfg(), history_years=5, use_cache=True)
        self.assertTrue(result.ok())
        self.assertEqual(monthrev_calls, [])
        self.assertEqual(result.month_revenue, [])

    def test_derive_ticker_cache_miss_calls_month_revenue_exactly_once(self):
        monthrev_calls = []
        cfg = {"ticker": "2330", "market": "TW", "name": "台積電",
               "valuation": {"method": "pe_band", "derive": {"base_revenue": 1e12}}}
        with patch.object(providers, "_http_get_json", side_effect=self._router(monthrev_calls)):
            result = providers.fetch(cfg, self._providers_cfg(), history_years=5, use_cache=True)
        self.assertTrue(result.ok())
        self.assertEqual(len(monthrev_calls), 1)
        self.assertEqual(len(result.month_revenue), 12)
        self.assertIn(("2024-01", 1e11 + 1), result.month_revenue)

    def test_derive_ticker_with_warm_monthrev_cache_skips_refetch_even_if_price_cache_cold(self):
        # 先預熱月營收快取(獨立於價格 blob),再故意不寫價格 blob(讓 fetch() 一定
        # 會呼叫 fetch_tw 重打 Price/PER)——月營收不該因此被連帶重打。
        providers._save_monthrev_cache("2330", [("2026-01", 999.0)])
        monthrev_calls = []
        cfg = {"ticker": "2330", "market": "TW", "name": "台積電",
               "valuation": {"method": "pe_band", "derive": {"base_revenue": 1e12}}}
        with patch.object(providers, "_http_get_json", side_effect=self._router(monthrev_calls)):
            result = providers.fetch(cfg, self._providers_cfg(), history_years=5, use_cache=True)
        self.assertTrue(result.ok())
        self.assertEqual(monthrev_calls, [])  # 沒有重打 MonthRevenue
        self.assertEqual(result.month_revenue, [("2026-01", 999.0)])  # 沿用預熱的快取內容

    def test_derive_ticker_month_revenue_failure_does_not_fail_overall_fetch(self):
        def _dispatch(url):
            if "TaiwanStockMonthRevenue" in url:
                raise RuntimeError("FinMind MonthRevenue 掛了(fake)")
            if "TaiwanStockPER" in url:
                return self._per_resp(url)
            if "TaiwanStockPrice" in url:
                return self._price_resp(url)
            raise AssertionError(f"unexpected url: {url}")

        cfg = {"ticker": "2330", "market": "TW", "name": "台積電",
               "valuation": {"method": "pe_band", "derive": {"base_revenue": 1e12}}}
        with patch.object(providers, "_http_get_json", side_effect=_dispatch):
            result = providers.fetch(cfg, self._providers_cfg(), history_years=5, use_cache=True)
        self.assertTrue(result.ok())
        self.assertEqual(result.price, 600.0)
        self.assertEqual(result.month_revenue, [])


# =========================================================================== #
#  9. StockData blob 相容 round-trip(新欄位 month_revenue)
# =========================================================================== #
class BlobCompatibilityMonthRevenueTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="aimonitor_test_monthrev_blob_")
        self._cache_patch = patch.object(providers, "CACHE_DIR", self._tmpdir)
        self._cache_patch.start()

    def tearDown(self):
        self._cache_patch.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_new_blob_roundtrip_preserves_month_revenue(self):
        data = StockData(ticker="2330", market="TW", name="台積電", currency="TWD",
                          price=600.0, price_date="2026-01-01", source="FinMind",
                          month_revenue=[("2026-01", 100.0), ("2026-02", 110.0)])
        providers._save_cache(data)
        loaded = providers._load_cache_raw("TW", "2330")
        self.assertIsNotNone(loaded)
        loaded_data, _ = loaded
        self.assertEqual(loaded_data.month_revenue, [["2026-01", 100.0], ["2026-02", 110.0]])

    def test_old_blob_missing_month_revenue_key_loads_with_default_empty_list(self):
        from dataclasses import asdict
        data = StockData(ticker="2330", market="TW", name="台積電", currency="TWD",
                          price=600.0, price_date="2026-01-01", source="FinMind")
        blob = asdict(data)
        del blob["month_revenue"]  # 模擬 018 之前寫入的舊快取檔
        blob["_fetched_at"] = datetime.now(timezone.utc).isoformat()
        path = providers._cache_path("TW", "2330")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)

        loaded = providers._load_cache_raw("TW", "2330")
        self.assertIsNotNone(loaded)
        loaded_data, _ = loaded
        self.assertEqual(loaded_data.month_revenue, [])
        self.assertEqual(loaded_data.price, 600.0)


# =========================================================================== #
#  10. CLI 顯示行:report.py::render_stock_card
# =========================================================================== #
def _make_item_with_revenue_check(rc):
    cfg = {"ticker": "2330", "market": "TW", "name": "台積電"}
    data = StockData(ticker="2330", market="TW", name="台積電", currency="TWD",
                      price=600.0, price_date="2026-01-01", source="FinMind")
    zones = {
        "zones": {"super_bargain": 400.0, "cheap": 500.0, "fair": 600.0,
                  "expensive": 700.0, "euphoria": 800.0},
        "anchor": 30.0, "anchor_kind": "forward_EPS", "assumptions": "測試假設",
        "target_year": 2029, "implied_multiple": None, "warnings": [],
        "revenue_check": rc,
    }
    analysis = {"region": "合理價區", "is_buy": False, "drop_to_cheap_pct": 10.0,
                "annual_vol_pct": None, "prob_hit_cheap": {}, "price_percentile": None}
    return {"cfg": cfg, "data": data, "zones": zones, "analysis": analysis, "error": ""}


class RenderStockCardRevenueCheckLineTest(unittest.TestCase):
    def _render(self, item):
        buf = io.StringIO()
        with patch.object(report, "HAS_RICH", False):
            with contextlib.redirect_stdout(buf):
                report.render_stock_card(item, config={})
        return buf.getvalue()

    def test_line_shown_with_both_a_and_b_when_present(self):
        rc = {"ttm": 960000.0, "expected": 1200000.0, "dev_pct": -20.0,
              "yoy_pct": 6.67, "cagr_pct": 20.0}
        out = self._render(_make_item_with_revenue_check(rc))
        self.assertIn("營收軌跡", out)
        self.assertIn("TTM 96.0萬", out)
        self.assertIn("假設 120.0萬", out)
        self.assertIn("-20.0%", out)
        self.assertIn("YoY +6.7%", out)
        self.assertIn("CAGR 20.0%", out)

    def test_no_line_when_revenue_check_is_none(self):
        out = self._render(_make_item_with_revenue_check(None))
        self.assertNotIn("營收軌跡", out)

    def test_assumptions_line_unaffected(self):
        rc = {"ttm": 960000.0, "expected": 1200000.0, "dev_pct": -20.0,
              "yoy_pct": 6.67, "cagr_pct": 20.0}
        out = self._render(_make_item_with_revenue_check(rc))
        self.assertIn("假設: 測試假設", out)


if __name__ == "__main__":
    unittest.main()

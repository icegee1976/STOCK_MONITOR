"""aimonitor.report.render_roi 顯示測試(完全離線,不 import providers/不打 API)。

目的:鎖住工單 009 新增的美股股利「毛/淨並列」註記行 —— 只驗證**顯示文字**,
不重算任何 ROI 數值(us_div_withholding / dividend_yield_pct 皆為 stub 常數)。
render_roi 內部用 `report._p` 印字,rich 有裝的話 `_p` 會走 `Console.print`
(不吐純文字、也不方便用 redirect_stdout 斷言),所以測試強制
`patch("aimonitor.report.HAS_RICH", False)`,讓 `_p` 走 `print(...)` 分支,
再用 `contextlib.redirect_stdout` 擷取輸出。

TW baseline(us_div_withholding=None)一段文字為改動前既有行為,鎖住逐字不變,
確保本工單「非美股輸出不變」的紅線。
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

from aimonitor import report


def _make_result(market, dividend_yield_pct, us_div_withholding):
    """建構 render_roi 需要的最小 stub result dict(比照 aimonitor/roi.py 回傳形狀)。"""
    return {
        "ticker": "TEST", "name": "測試標的", "market": market,
        "stock_ccy": "USD" if market == "US" else "TWD",
        "cap_ccy": "USD" if market == "US" else "TWD",
        "price": 100.0, "shares": 10.0, "spent": 1000.0,
        "dividend_yield_pct": dividend_yield_pct,
        "target_year": 2029, "fx_usdtwd": 32.0,
        "fx_note": False,
        "us_div_withholding": us_div_withholding,
        "scenarios": [
            {"zone": "fair", "label": "合理價區", "target_price": 100.0,
             "rows": [{"years": 1, "total_return_pct": 5.0, "annualized_pct": 5.0}]},
        ],
    }


class RenderRoiDividendNoteTest(unittest.TestCase):
    def _render(self, r):
        buf = io.StringIO()
        with patch.object(report, "HAS_RICH", False):
            with contextlib.redirect_stdout(buf):
                report.render_roi(r, config={})
        return buf.getvalue()

    def test_us_shows_gross_and_net_after_tax(self):
        r = _make_result("US", dividend_yield_pct=2.0, us_div_withholding=0.30)
        out = self._render(r)
        self.assertIn("稅後", out)
        self.assertIn("30%", out)
        self.assertIn("1.4%", out)          # 淨 = 2.0 * (1-0.30) = 1.4
        self.assertIn("已含股利率 2.0%/年 之簡化累積", out)

    def test_tw_baseline_unchanged_no_tax_wording(self):
        r = _make_result("TW", dividend_yield_pct=2.0, us_div_withholding=None)
        out = self._render(r)
        self.assertNotIn("稅後", out)
        self.assertIn("(已含股利率 2.0%/年 之簡化累積)", out)

    def test_other_lines_unchanged(self):
        r = _make_result("US", dividend_yield_pct=2.0, us_div_withholding=0.30)
        out = self._render(r)
        self.assertIn("投報率情境試算", out)
        self.assertIn("情境:若未來股價回到各價格帶", out)


class DisclaimerConstantLockTest(unittest.TestCase):
    """紅線 4 的可執行鎖:DISCLAIMER 常數逐字等值。render_roi 不印 DISCLAIMER
    (CLI 由 monitor.py 另行輸出),所以這裡直接鎖常數本身——任何人改寫免責
    措辭(哪怕一個字),此測試翻紅,強迫走明確決策而非順手改。"""

    def test_disclaimer_text_is_byte_for_byte_locked(self):
        self.assertEqual(
            report.DISCLAIMER,
            "\n[dim]──────────────────────────────────────────────\n"
            "本工具僅作資訊與教育用途,不構成投資建議。所有「便宜價/目標價/報酬」\n"
            "均建立在 watchlist.yaml 內可被你修改的假設上;免費數據為日收盤價(EOD,非盤中即時)。\n"
            "投資前請自行查證並承擔風險。[/dim]",
        )


if __name__ == "__main__":
    unittest.main()

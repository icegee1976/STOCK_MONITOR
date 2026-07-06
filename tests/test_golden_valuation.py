"""估價引擎黃金值 regression 測試(完全離線)。

目的:把「台積電黃金值人工比對」自動化,直接呼叫
`aimonitor.valuation.compute_zones` / `aimonitor.classify.classify_region`,
不經 providers、不讀 watchlist.yaml/config.yaml、不打任何網路 API。

fixture 快照(從 watchlist.yaml@8e510f7 抄錄,寫死於此,不得於測試期讀 yaml):
    base_revenue=2.89e12, net_margin=0.41362, revenue_cagr=0.24,
    shares=25.93e9, target_year=2029(base_year 未填 → 預設 2024,年數=5)
    pe_bands: super_bargain=12.81 cheap=16.49 fair=23.85 expensive=27.53 euphoria=31.2

手算 oracle(對照 aimonitor/valuation.py::_forward_eps 的 derive 分支):
    forward_EPS = base_revenue × (1+revenue_cagr)^years × net_margin ÷ shares
                = 2.89e12 × 1.24^5 × 0.41362 ÷ 25.93e9
                = 135.14664911359722...  ≈ 135.147

    zones[k] = round(forward_EPS × pe_bands[k], 2)
    cheap         = round(135.14664911359722 × 16.49, 2) = 2228.57
    super_bargain = round(135.14664911359722 × 12.81, 2) = 1731.23

PDF 原值對照(孫慶龍《AI 投資藍圖》台積電):便宜≈2,226 / 大特≈1,729。
本測試斷言值(2228.57 / 1731.23)與 PDF 原值差距 ~0.1-0.12%,屬校正用途的
四捨五入差異(watchlist.yaml 中 pe_bands 為回推校正後的係數),非引擎錯誤。
"""

from __future__ import annotations

import types
import unittest

from aimonitor.valuation import ZONE_KEYS, compute_zones
from aimonitor.classify import classify_region


# 寫死的 2330 假設快照(不得於執行期讀 watchlist.yaml)。
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

# compute_zones 的 pe_band(非 auto)分支只實際取用 data.price 與
# data.trailing_eps(用於算 implied_pe / 護欄訊息),不需要 per_history 等欄位。
_STUB_DATA = types.SimpleNamespace(price=2460.0, trailing_eps=None)


class GoldenValuationTest(unittest.TestCase):
    """台積電 pe_band 黃金值(離線,鎖 aimonitor.valuation.compute_zones 現狀)。

    斷言採**精確等值**:derive 路徑是純確定性浮點運算(無 datetime.now()、
    無外部資料),引擎輸出已 round 到固定位數,任何係數/公式被改動都應立即翻紅。
    (PDF 原值 便宜≈2,226 / 大特≈1,729 與此處 2228.57 / 1731.23 差 ~0.12%,
    屬校正舍入差異,詳見檔頭說明——不作為斷言目標,僅供人工對照。)
    """

    @classmethod
    def setUpClass(cls):
        cls.result = compute_zones(STOCK_CFG_2330, _STUB_DATA, {})
        cls.zones = cls.result["zones"]

    def test_anchor_forward_eps(self):
        # 手算 oracle: 2.89e12×(1.24)^5×0.41362÷25.93e9 = 135.14664911359722
        # 引擎回傳 round(eps, 3) → 精確 135.147
        self.assertEqual(self.result["anchor"], 135.147)

    def test_zone_cheap_golden_value(self):
        # round(135.14664911359722 × 16.49, 2) = 2228.57
        self.assertEqual(self.zones["cheap"], 2228.57)

    def test_zone_super_bargain_golden_value(self):
        # round(135.14664911359722 × 12.81, 2) = 1731.23
        self.assertEqual(self.zones["super_bargain"], 1731.23)

    def test_zones_monotonic_increasing(self):
        vals = [self.zones[k] for k in ZONE_KEYS]
        self.assertEqual(
            vals, sorted(vals),
            "五個價格帶必須嚴格由低到高: super_bargain < cheap < fair < expensive < euphoria",
        )
        # 明確逐一比較,方便未來失敗時一眼看出是哪一段順序壞了
        self.assertLess(self.zones["super_bargain"], self.zones["cheap"])
        self.assertLess(self.zones["cheap"], self.zones["fair"])
        self.assertLess(self.zones["fair"], self.zones["expensive"])
        self.assertLess(self.zones["expensive"], self.zones["euphoria"])

    def test_no_warnings_for_well_formed_bands(self):
        # 這組快照本益比帶本身已單調、現價隱含本益比未超過瘋狂價,不應觸發任何 warning。
        self.assertEqual(self.result["warnings"], [])


class ClassifyRegionBoundaryTest(unittest.TestCase):
    """classify_region 邊界語意:測試鎖現狀(<=,非改規格)。"""

    @classmethod
    def setUpClass(cls):
        cls.zones = compute_zones(STOCK_CFG_2330, _STUB_DATA, {})["zones"]

    def test_representative_price_in_each_band(self):
        z = self.zones
        cases = [
            (z["super_bargain"] - 1, "大特價區"),
            ((z["cheap"] + z["fair"]) / 2, "合理價區"),
            ((z["fair"] + z["expensive"]) / 2, "昂貴價區"),
            ((z["expensive"] + z["euphoria"]) / 2, "瘋狂價區"),
            (z["euphoria"] + 1, "超瘋狂價區"),
        ]
        for price, expected_region in cases:
            with self.subTest(price=price):
                self.assertEqual(classify_region(price, z), expected_region)

    def test_boundary_exactly_at_cheap_is_inclusive_cheap_zone(self):
        # classify_region: price <= zones["cheap"] → "便宜價區"(現狀:邊界含在便宜價區,非合理價區)
        z = self.zones
        self.assertEqual(classify_region(z["cheap"], z), "便宜價區")

    def test_boundary_exactly_at_euphoria_is_inclusive_euphoria_zone(self):
        # classify_region: price <= zones["euphoria"] → "瘋狂價區"(現狀:邊界含在瘋狂價區,非超瘋狂價區)
        z = self.zones
        self.assertEqual(classify_region(z["euphoria"], z), "瘋狂價區")

    def test_boundary_exactly_at_super_bargain_is_inclusive(self):
        # classify_region: price <= zones["super_bargain"] → "大特價區"
        z = self.zones
        self.assertEqual(classify_region(z["super_bargain"], z), "大特價區")

    def test_boundary_exactly_at_fair_is_inclusive_fair_zone(self):
        z = self.zones
        self.assertEqual(classify_region(z["fair"], z), "合理價區")

    def test_boundary_exactly_at_expensive_is_inclusive_expensive_zone(self):
        z = self.zones
        self.assertEqual(classify_region(z["expensive"], z), "昂貴價區")


class NonMonotonicBandsReorderTest(unittest.TestCase):
    """鎖定 compute_zones 對「非單調 pe_bands」的現狀行為:自動重排 + 附 warning。

    這不是常態使用情境(watchlist.yaml 內的 pe_bands 理應本身遞增),但
    compute_zones 內建了防呆(見 aimonitor/valuation.py 尾端「確保單調遞增」段),
    此測試把該防呆行為鎖住,避免未來改動時被悄悄移除或改壞。
    """

    def test_out_of_order_pe_bands_get_sorted_with_warning(self):
        stock_cfg = {
            "ticker": "FAKE", "market": "TW", "name": "測試用非單調案例",
            "valuation": {
                "method": "pe_band",
                "forward_eps": 10.0,
                "target_year": 2029,
                # 刻意把 super_bargain(20) 設得比 cheap(15) 高 → 反轉,觸發防呆重排
                "pe_bands": {"super_bargain": 20, "cheap": 15, "fair": 23.85,
                             "expensive": 27.53, "euphoria": 31.2},
            },
        }
        data = types.SimpleNamespace(price=100.0, trailing_eps=None)
        result = compute_zones(stock_cfg, data, {})
        zones = result["zones"]

        # 重排後應為單調遞增(原始 super_bargain=200 與 cheap=150 對調)
        vals = [zones[k] for k in ZONE_KEYS]
        self.assertEqual(vals, sorted(vals))
        self.assertEqual(zones["super_bargain"], 150.0)
        self.assertEqual(zones["cheap"], 200.0)
        self.assertIn("價格帶非單調,已重新排序", result["warnings"])


if __name__ == "__main__":
    unittest.main()

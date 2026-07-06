"""aimonitor.classify 純函數單元測試(完全離線)。

鎖住 `classify_region` 六區邊界語意、`prob_hit_barrier` 首次穿越機率公式、
`annualized_vol` 樣本數門檻、`analyze` 整合輸出的現狀行為。手算 oracle 寫在
各案例註解供人工複核。不讀 watchlist.yaml/config.yaml、不打網路。
"""

from __future__ import annotations

import unittest

from aimonitor.classify import analyze, annualized_vol, classify_region, prob_hit_barrier

ZONES = {"super_bargain": 10, "cheap": 20, "fair": 30, "expensive": 40, "euphoria": 50}


class ClassifyRegionSixZoneTest(unittest.TestCase):
    """zones={sb:10, cheap:20, fair:30, expensive:40, euphoria:50}。"""

    def test_price_5_is_super_bargain(self):
        self.assertEqual(classify_region(5, ZONES), "大特價區")

    def test_price_10_boundary_is_super_bargain_inclusive(self):
        self.assertEqual(classify_region(10, ZONES), "大特價區")

    def test_price_20_boundary_is_cheap_inclusive(self):
        self.assertEqual(classify_region(20, ZONES), "便宜價區")

    def test_price_25_is_fair(self):
        self.assertEqual(classify_region(25, ZONES), "合理價區")

    def test_price_50_is_euphoria(self):
        self.assertEqual(classify_region(50, ZONES), "瘋狂價區")

    def test_price_50_01_is_super_euphoria(self):
        self.assertEqual(classify_region(50.01, ZONES), "超瘋狂價區")


class ProbHitBarrierTest(unittest.TestCase):
    def test_barrier_at_or_above_price_is_certain(self):
        self.assertEqual(prob_hit_barrier(price=100.0, barrier=100.0, vol=0.3, years=1), 1.0)
        self.assertEqual(prob_hit_barrier(price=100.0, barrier=120.0, vol=0.3, years=1), 1.0)

    def test_vol_none_returns_none(self):
        self.assertIsNone(prob_hit_barrier(price=100.0, barrier=80.0, vol=None, years=1))

    def test_vol_zero_returns_none(self):
        self.assertIsNone(prob_hit_barrier(price=100.0, barrier=80.0, vol=0.0, years=1))

    def test_years_zero_or_negative_returns_none(self):
        self.assertIsNone(prob_hit_barrier(price=100.0, barrier=80.0, vol=0.3, years=0))
        self.assertIsNone(prob_hit_barrier(price=100.0, barrier=80.0, vol=0.3, years=-1))

    def test_known_case_price100_barrier80_vol030_year1(self):
        # 手算 oracle(反射原理 P = 2*Phi(-x/(sigma*sqrt(T)))):
        #   x = ln(100/80) = ln(1.25) = 0.22314355131420976
        #   sigma_t = 0.3 * sqrt(1) = 0.3
        #   z = -x/sigma_t = -0.7438118377140326
        #   Phi(z) = 0.5*(1+erf(z/sqrt(2))) ;  P = 2*Phi(z) = 0.4569903175523975
        # 查表值(標準常態 CDF, z≈-0.744)約 0.2285 → 2x ≈ 0.4569，容差 ±0.002。
        p = prob_hit_barrier(price=100.0, barrier=80.0, vol=0.3, years=1)
        self.assertAlmostEqual(p, 0.4569903175523975, delta=0.002)


class AnnualizedVolTest(unittest.TestCase):
    def test_fewer_than_30_closes_returns_none(self):
        history = [(i, 100.0 + i) for i in range(29)]
        self.assertIsNone(annualized_vol(history))

    def test_constant_series_with_enough_samples_returns_zero(self):
        # 31 筆常數收盤價 → 30 個對數報酬,皆為 ln(100/100)=0 → pstdev=0 → vol=0*sqrt(252)=0.0
        history = [(i, 100.0) for i in range(31)]
        self.assertEqual(annualized_vol(history), 0.0)

    def test_alternating_series_locks_sqrt252_annualization_factor(self):
        # 鎖住 252 交易日年化因子(常數序列 0×任何因子=0,對此無鑑別力):
        # 31 筆 100/110 交替 → 30 個對數報酬 = ±ln(1.1) 各 15 個 → 平均 0、
        # 母體標準差 pstdev = ln(1.1) = 0.09531017980432486
        # vol = 0.09531017980432486 × sqrt(252)(=15.874507866387544)= 1.5130022(手算)
        # 若年化因子被改成 sqrt(365) 或被移除,此斷言立即翻紅。
        history = [(i, 110.0 if i % 2 else 100.0) for i in range(31)]
        self.assertAlmostEqual(annualized_vol(history), 1.5130022, places=4)


class AnalyzeTest(unittest.TestCase):
    def test_is_buy_true_exactly_at_cheap_boundary(self):
        result = analyze(price=20.0, zones=ZONES, price_history=[], horizons_years=[1])
        self.assertTrue(result["is_buy"])
        self.assertEqual(result["region"], "便宜價區")

    def test_drop_to_cheap_pct_is_always_below_100(self):
        # 手算: price=100, cheap=20 → decline_needed = (100-20)/100 = 0.8 → 80.0
        result = analyze(price=100.0, zones=ZONES, price_history=[], horizons_years=[1])
        self.assertEqual(result["drop_to_cheap_pct"], 80.0)
        self.assertLess(result["drop_to_cheap_pct"], 100.0)
        self.assertFalse(result["is_buy"])


if __name__ == "__main__":
    unittest.main()

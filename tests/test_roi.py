"""aimonitor.roi 純函數單元測試(完全離線)。

目的:鎖住 `_buy_cost` / `_sell_proceeds` / `scenario_roi` 的稅費與跨幣別現狀邏輯,
手算 oracle 寫在各測試案例註解供人工複核。不 import providers 的 fetch 功能、
不讀 watchlist.yaml/config.yaml、不打任何網路 API — `scenario_roi` 的 `data`
參數一律用 `types.SimpleNamespace` stub 出 price/currency/dividend_yield/annual_dividend。

工單 007 已補實作美股股利預扣 30%(見 docs/tickets/007-us-dividend-withholding.md):
`aimonitor/roi.py` 對 market=="US" 的 `per_share_div` 乘上 (1 − rate)、
rate 預設 0.30(`config["fees"]["us"].get("dividend_withholding", 0.30)`),
並在結果 dict 新增 `us_div_withholding` 欄位。見下方 UsDividendWithholdingTest。

fees fixture(寫死,對照工單 SPEC):
    tw.brokerage = 0.001425, tw.brokerage_discount = 1.0, tw.tax_sell = 0.003
    us.commission = 0.0(部分測試另外覆寫為 1.0 以驗證手續費路徑)
"""

from __future__ import annotations

import types
import unittest

from aimonitor.roi import _buy_cost, _sell_proceeds, scenario_roi

FEES = {
    "tw": {"brokerage": 0.001425, "brokerage_discount": 1.0, "tax_sell": 0.003},
    "us": {"commission": 0.0},
}


def _stub_data(price, currency="USD", dividend_yield=0.0, annual_dividend=0.0):
    return types.SimpleNamespace(
        price=price, currency=currency,
        dividend_yield=dividend_yield, annual_dividend=annual_dividend,
    )


class BuyCostTest(unittest.TestCase):
    def test_tw_whole_share_truncation(self):
        # 手算: shares = int(100000 / (100*(1+0.001425))) = int(100000/100.1425)
        #      = int(998.5757...) = 998
        # spent = 998*100*(1+0.001425) = 99800 + 99800*0.001425 = 99800 + 142.215
        #       = 99942.215
        # (工單 SPEC 原文寫 99,942.2215,經手算與實際跑值核對後為 99942.215,
        #  差在小數點多一位 1,研判為工單 SPEC 筆誤;此處採實際手算值,已與
        #  engine 現狀輸出比對一致——非引擎 bug,見 REPORT 說明)
        shares, spent = _buy_cost(price=100.0, capital=100_000.0, market="TW", fees=FEES)
        self.assertEqual(shares, 998)
        self.assertAlmostEqual(spent, 99942.215, places=3)

    def test_us_fractional_shares_no_commission(self):
        # 手算: shares = 10000/100 = 100.0(碎股,浮點);spent = 100*100 + 0 = 10000
        shares, spent = _buy_cost(price=100.0, capital=10_000.0, market="US", fees=FEES)
        self.assertEqual(shares, 100.0)
        self.assertEqual(spent, 10_000.0)

    def test_us_fractional_shares_with_commission(self):
        # 手算: shares = 10000/100 = 100.0;spent = 100*100 + 1.0(commission) = 10001.0
        fees = {"tw": FEES["tw"], "us": {"commission": 1.0}}
        shares, spent = _buy_cost(price=100.0, capital=10_000.0, market="US", fees=fees)
        self.assertEqual(shares, 100.0)
        self.assertEqual(spent, 10_001.0)


class SellProceedsTest(unittest.TestCase):
    def test_tw_sell_includes_brokerage_and_transaction_tax(self):
        # 手算: 998股 @120 → 119760 × (1 − 0.001425 − 0.003) = 119760 × 0.995575
        #      = 119230.062(驗證證交稅 0.3% 只在賣出端扣)
        proceeds = _sell_proceeds(price=120.0, shares=998, market="TW", fees=FEES)
        self.assertAlmostEqual(proceeds, 119230.062, places=3)

    def test_us_sell_no_tax_only_commission(self):
        # 手算: 100股 @120 − commission(0) = 12000.0(美股賣出無證交稅)
        proceeds = _sell_proceeds(price=120.0, shares=100.0, market="US", fees=FEES)
        self.assertEqual(proceeds, 12_000.0)


class ScenarioRoiCrossCurrencyTest(unittest.TestCase):
    """跨幣別:TWD 資金投美股標的。"""

    CONFIG = {"fees": FEES, "fx": {"USDTWD": 32.0}, "roi_horizons_years": [1, 3, 5]}
    ZONES_INFO = {
        "zones": {"cheap": 90.0, "fair": 100.0, "expensive": 120.0, "euphoria": 150.0},
        "target_year": 2029,
    }

    def test_twd_capital_into_usd_stock_converts_and_sets_fx_note(self):
        # 手算: capital_in_stock = 320000 / 32(USDTWD) = 10000(USD);
        #      shares = 10000/100 = 100.0 (美股碎股,commission=0)
        stock_cfg = {"ticker": "TEST_US", "market": "US", "name": "測試美股"}
        data = _stub_data(price=100.0, currency="USD")
        result = scenario_roi(stock_cfg, data, self.ZONES_INFO, capital=320_000.0,
                               config=self.CONFIG, capital_currency="TWD")
        self.assertNotIn("error", result)
        self.assertEqual(result["shares"], 100.0)
        self.assertEqual(result["spent"], 10_000.0)
        self.assertTrue(result["fx_note"])

    def test_same_currency_capital_has_no_fx_note(self):
        stock_cfg = {"ticker": "TEST_US", "market": "US", "name": "測試美股"}
        data = _stub_data(price=100.0, currency="USD")
        result = scenario_roi(stock_cfg, data, self.ZONES_INFO, capital=10_000.0,
                               config=self.CONFIG, capital_currency="USD")
        self.assertNotIn("error", result)
        self.assertFalse(result["fx_note"])


class ScenarioRoiGuardTest(unittest.TestCase):
    CONFIG = {"fees": FEES, "fx": {"USDTWD": 32.0}, "roi_horizons_years": [1]}
    ZONES_INFO = {
        "zones": {"cheap": 90.0, "fair": 100.0, "expensive": 120.0, "euphoria": 150.0},
        "target_year": 2029,
    }

    def test_price_none_returns_error_dict(self):
        stock_cfg = {"ticker": "TEST_US", "market": "US", "name": "測試美股"}
        data = _stub_data(price=None, currency="USD")
        result = scenario_roi(stock_cfg, data, self.ZONES_INFO, capital=10_000.0,
                               config=self.CONFIG)
        self.assertIn("error", result)

    def test_price_zero_or_negative_returns_error_dict(self):
        stock_cfg = {"ticker": "TEST_US", "market": "US", "name": "測試美股"}
        data = _stub_data(price=0.0, currency="USD")
        result = scenario_roi(stock_cfg, data, self.ZONES_INFO, capital=10_000.0,
                               config=self.CONFIG)
        self.assertIn("error", result)

    def test_tw_capital_insufficient_for_one_share_returns_error_dict(self):
        # 手算: shares = int(50 / (1000*(1+0.001425))) = int(0.04993...) = 0 → error
        stock_cfg = {"ticker": "TEST_TW", "market": "TW", "name": "測試台股"}
        data = _stub_data(price=1000.0, currency="TWD")
        zones_info = {
            "zones": {"cheap": 900.0, "fair": 1000.0, "expensive": 1200.0, "euphoria": 1500.0},
            "target_year": 2029,
        }
        result = scenario_roi(stock_cfg, data, zones_info, capital=50.0, config=self.CONFIG)
        self.assertIn("error", result)


class ScenarioRoiIntlDividendTest(unittest.TestCase):
    """market="INTL"(累積型 ETF)股利貢獻恆為 0,避免與 total-return 價格雙重計入。

    (美股股利預扣 30% 已由工單 007 實作,見下方 UsDividendWithholdingTest。)
    """

    CONFIG = {"fees": FEES, "fx": {"USDTWD": 32.0}, "roi_horizons_years": [1, 3]}
    ZONES_INFO = {
        "zones": {"cheap": 90.0, "fair": 100.0, "expensive": 120.0, "euphoria": 150.0},
        "target_year": 2029,
    }

    def test_intl_market_dividend_contribution_is_zero(self):
        stock_cfg = {"ticker": "TEST_INTL", "market": "INTL", "name": "測試累積型ETF"}
        # annual_dividend 故意給非零值,驗證 INTL 分支忽略它(per_share_div 強制為 0)
        data = _stub_data(price=100.0, currency="USD", annual_dividend=5.0)
        result = scenario_roi(stock_cfg, data, self.ZONES_INFO, capital=10_000.0,
                               config=self.CONFIG, capital_currency="USD")
        self.assertNotIn("error", result)
        # 手算(zone="fair", tprice=100.0, shares=100.0, yrs=1,美股 commission=0):
        #   proceeds = 100*100 - 0 = 10000.0；divs 若被計入應為 shares*5.0*1=500，
        #   但 INTL 應強制 per_share_div=0 → divs=0 → total=proceeds=spent(=10000.0)
        #   → total_return_pct = round((10000-10000)/10000*100, 1) = 0.0
        fair_scn = next(s for s in result["scenarios"] if s["zone"] == "fair")
        row_1y = next(r for r in fair_scn["rows"] if r["years"] == 1)
        self.assertEqual(row_1y["total_return_pct"], 0.0)
        # 直接鎖未經 round 的絕對金額:若 INTL 分支誤計股利(divs=500),此值變 10500
        self.assertEqual(row_1y["value_in_stock_ccy"], 10_000.0)


class UsDividendWithholdingTest(unittest.TestCase):
    """工單 007:market=="US" 股利乘上 (1 − dividend_withholding),預設稅率 0.30。"""

    ZONES_INFO = {
        "zones": {"cheap": 90.0, "fair": 100.0, "expensive": 120.0, "euphoria": 150.0},
        "target_year": 2029,
    }

    def test_us_default_withholding_030_applies_07_factor(self):
        # 手算(price=100, capital=10000→shares=100, annual_dividend=5.0, zone="fair"
        # tprice=100.0, yrs=1, commission=0):
        #   proceeds = 100*100 - 0 = 10000.0
        #   divs = shares * per_share_div * yrs = 100 * (5.0*0.7) * 1 = 350.0
        #   total = 10000 + 350 = 10350.0 → value_in_stock_ccy == 10350.0
        #   ret = (10350-10000)/10000 = 0.035 → total_return_pct == 3.5
        config = {"fees": FEES, "fx": {"USDTWD": 32.0}, "roi_horizons_years": [1]}
        stock_cfg = {"ticker": "TEST_US", "market": "US", "name": "測試美股"}
        data = _stub_data(price=100.0, currency="USD", annual_dividend=5.0)
        result = scenario_roi(stock_cfg, data, self.ZONES_INFO, capital=10_000.0,
                               config=config, capital_currency="USD")
        self.assertNotIn("error", result)
        self.assertEqual(result["us_div_withholding"], 0.30)
        fair_scn = next(s for s in result["scenarios"] if s["zone"] == "fair")
        row_1y = next(r for r in fair_scn["rows"] if r["years"] == 1)
        self.assertEqual(row_1y["value_in_stock_ccy"], 10_350.0)
        self.assertEqual(row_1y["total_return_pct"], 3.5)

    def test_us_custom_withholding_rate_zero_is_backward_compatible(self):
        # 手算:dividend_withholding=0.0 → per_share_div 不打折 → divs = 100*5.0*1 = 500.0
        #   total = 10000 + 500 = 10500.0
        fees = {"tw": FEES["tw"], "us": {"commission": 0.0, "dividend_withholding": 0.0}}
        config = {"fees": fees, "fx": {"USDTWD": 32.0}, "roi_horizons_years": [1]}
        stock_cfg = {"ticker": "TEST_US", "market": "US", "name": "測試美股"}
        data = _stub_data(price=100.0, currency="USD", annual_dividend=5.0)
        result = scenario_roi(stock_cfg, data, self.ZONES_INFO, capital=10_000.0,
                               config=config, capital_currency="USD")
        self.assertNotIn("error", result)
        self.assertEqual(result["us_div_withholding"], 0.0)
        fair_scn = next(s for s in result["scenarios"] if s["zone"] == "fair")
        row_1y = next(r for r in fair_scn["rows"] if r["years"] == 1)
        self.assertEqual(row_1y["value_in_stock_ccy"], 10_500.0)

    def test_tw_dividend_not_discounted_and_withholding_field_none(self):
        # 對照組:同構台股 case 股利不打折(維持現狀)。
        # 手算(price=100, capital=100000, market=TW, annual_dividend=5.0):
        #   shares = int(100000/(100*(1+0.001425))) = int(998.5757..) = 998
        #   spent = 998*100*1.001425 = 99942.215
        #   zone="fair" tprice=100.0, yrs=1: proceeds = 998*100*(1-0.001425-0.003)
        #     = 99800*0.995575 = 99358.385
        #   divs = 998*5.0*1 = 4990.0(不打折,無 0.7 因子)
        #   total = 99358.385 + 4990.0 = 104348.385 → round(total, 2) = 104348.38
        config = {"fees": FEES, "fx": {"USDTWD": 32.0}, "roi_horizons_years": [1]}
        stock_cfg = {"ticker": "TEST_TW", "market": "TW", "name": "測試台股"}
        zones_info = {
            "zones": {"cheap": 90.0, "fair": 100.0, "expensive": 120.0, "euphoria": 150.0},
            "target_year": 2029,
        }
        data = _stub_data(price=100.0, currency="TWD", annual_dividend=5.0)
        result = scenario_roi(stock_cfg, data, zones_info, capital=100_000.0,
                               config=config, capital_currency="TWD")
        self.assertNotIn("error", result)
        self.assertIsNone(result["us_div_withholding"])
        fair_scn = next(s for s in result["scenarios"] if s["zone"] == "fair")
        row_1y = next(r for r in fair_scn["rows"] if r["years"] == 1)
        # 104348.385 恰在 round 半值邊界(引擎實跑為 .38)。斷言用 delta=0.011
        # 容忍 .38/.39 兩種落點,避免綁死浮點半值行為;若 TW 股利被誤打 0.7 折
        # (差 1497 元)仍會翻紅。
        self.assertAlmostEqual(row_1y["value_in_stock_ccy"], 104_348.385, delta=0.011)


if __name__ == "__main__":
    unittest.main()

"""`aimonitor.providers._back_adjust_tw` 回歸測試(完全離線,全合成資料)。

目的:鎖住台股拆股/反分割還原邏輯——此函數是純函數(不打網路),
影響 price_band 百分位、波動率、殖利率河流圖(README §4 點名的關鍵啟發式)。
只 `from aimonitor.providers import _back_adjust_tw`;不 import/呼叫任何
fetch 系列函數,不打網路、不讀 watchlist.yaml/config.yaml。

演算法回顧(對照 aimonitor/providers.py::_back_adjust_tw 原始碼手算 oracle):
    1. price_history 先 sorted()(升冪)。
    2. 從最後一筆往前掃(i = n-1 down to 1):
       r = price[i].close / price[i-1].close
       若該日(price[i].date)不是現金除息日,且 r < 0.6 或 r > 1.7,
       視為真正的分割/反分割,把 r 累乘進 cum(cum 只會越乘越极端,
       方向由後往前累積 —— 即斷層越早發生,越多先前的點被還原)。
       factor[i-1] = cum(注意是設在 i-1,即斷層"之前"那一筆)。
    3. adj_px[i] = price[i].close × factor[i](factor 預設 1.0,只有
       斷層之前的點會被 <1 或 >1 的 cum 蓋掉)。
    4. 配息(div_history)用 bisect_right(dates, ex_date) - 1 找到
       「該除息日所屬」的 factor,同步還原(斷層前的配息也要縮放)。

手算 oracle 範例(情境 1,1:4 拆股):
    price = [(d1,100),(d2,100),(d3,25),(d4,25)], div=[]
    i=3: prev=price[2]=25, cur=price[3]=25, r=1.0 → 不觸發; factor[2]=cum=1.0
    i=2: prev=price[1]=100, cur=price[2]=25, r=0.25 <0.6 → cum=1.0*0.25=0.25; factor[1]=0.25
    i=1: prev=price[0]=100, cur=price[1]=100, r=1.0 → 不觸發; factor[0]=cum=0.25(沿用)
    factor = [0.25, 0.25, 1.0, 1.0]
    adj_px = [100*0.25, 100*0.25, 25*1.0, 25*1.0] = [25.0, 25.0, 25.0, 25.0]
    → d1、d2 從 100 還原為 25(與斷層後價格尺度一致)。
"""

from __future__ import annotations

import unittest

from aimonitor.providers import _back_adjust_tw


class SplitBackAdjustTest(unittest.TestCase):
    """情境 1:1:4 拆股(0050 型,r=0.25 < 0.6)應被還原。"""

    def test_1_to_4_split_restates_pre_split_prices(self):
        # oracle: 見檔頭手算,factor=[0.25,0.25,1.0,1.0]
        px = [("d1", 100.0), ("d2", 100.0), ("d3", 25.0), ("d4", 25.0)]
        adj_px, adj_div = _back_adjust_tw(px, [])
        self.assertEqual(
            adj_px,
            [("d1", 25.0), ("d2", 25.0), ("d3", 25.0), ("d4", 25.0)],
        )
        self.assertEqual(adj_div, [])


class ReverseSplitBackAdjustTest(unittest.TestCase):
    """情境 2:反分割(r > 1.7)同樣應被還原,方向相反(向上乘)。"""

    def test_reverse_split_restates_pre_split_prices_upward(self):
        # oracle: i=3: prev=price[2]=40,cur=price[3]=40,r=1.0→不觸發;factor[2]=1.0
        #         i=2: prev=price[1]=10,cur=price[2]=40,r=4.0 >1.7→cum=1.0*4.0=4.0;factor[1]=4.0
        #         i=1: prev=price[0]=10,cur=price[1]=10,r=1.0→不觸發;factor[0]=cum=4.0
        #         factor=[4.0,4.0,1.0,1.0]
        #         adj_px=[10*4.0, 10*4.0, 40*1.0, 40*1.0]=[40.0,40.0,40.0,40.0]
        px = [("d1", 10.0), ("d2", 10.0), ("d3", 40.0), ("d4", 40.0)]
        adj_px, adj_div = _back_adjust_tw(px, [])
        self.assertEqual(
            adj_px,
            [("d1", 40.0), ("d2", 40.0), ("d3", 40.0), ("d4", 40.0)],
        )
        self.assertEqual(adj_div, [])


class ExDividendNotMisdetectedAsSplitTest(unittest.TestCase):
    """情境 3:與情境 1 相同的價格跳空,但 d3 是現金除息日 → 不應誤判為分割。"""

    def test_large_ex_dividend_gap_is_not_treated_as_split(self):
        # oracle: ex_dates={"d3"}。掃到 i=2(prev=price[1]=100,cur=price[2]=25,
        # price_history[2][0]=="d3" in ex_dates)→ 該筆直接跳過累乘(即使
        # r=0.25 本應觸發),cum 全程維持 1.0 → factor 全為 1.0 → 價格原樣返回。
        px = [("d1", 100.0), ("d2", 100.0), ("d3", 25.0), ("d4", 25.0)]
        div = [("d3", 5.0)]  # d3 當日除息 5 元,跳空屬配息而非分割
        adj_px, adj_div = _back_adjust_tw(px, div)
        self.assertEqual(
            adj_px,
            [("d1", 100.0), ("d2", 100.0), ("d3", 25.0), ("d4", 25.0)],
        )
        # 配息本身也不受影響(factor 全 1.0,5.0*1.0=5.0)
        self.assertEqual(adj_div, [("d3", 5.0)])


class NormalVolatilityNoTriggerTest(unittest.TestCase):
    """情境 4:正常波動(0.6 <= r <= 1.7)不觸發還原,即使是單日 -35%。"""

    def test_single_day_minus_35_percent_does_not_trigger(self):
        # oracle: r = 65/100 = 0.65,介於 [0.6, 1.7] 之間(門檻用 < / >,
        # 邊界值本身不觸發)→ cum 維持 1.0 → 原樣返回。
        px = [("d1", 100.0), ("d2", 65.0)]
        adj_px, adj_div = _back_adjust_tw(px, [])
        self.assertEqual(adj_px, [("d1", 100.0), ("d2", 65.0)])
        self.assertEqual(adj_div, [])

    def test_boundary_ratio_exactly_0_6_does_not_trigger(self):
        # oracle: r = 60/100 = 0.6,門檻為 "r < 0.6"(嚴格小於)才觸發 → 0.6 不觸發。
        px = [("d1", 100.0), ("d2", 60.0)]
        adj_px, _ = _back_adjust_tw(px, [])
        self.assertEqual(adj_px, [("d1", 100.0), ("d2", 60.0)])

    def test_boundary_ratio_exactly_1_7_does_not_trigger(self):
        # oracle: r = 170/100 = 1.7,門檻為 "r > 1.7"(嚴格大於)才觸發 → 1.7 不觸發。
        px = [("d1", 100.0), ("d2", 170.0)]
        adj_px, _ = _back_adjust_tw(px, [])
        self.assertEqual(adj_px, [("d1", 100.0), ("d2", 170.0)])

    def test_ratio_just_below_0_6_triggers_locks_lower_threshold_value(self):
        # 門檻「數值」鑑別 case(0.6 不觸發只證明邊界含括,證明不了門檻是 0.6 而非 0.5):
        # r = 36/64 = 0.5625(二進位可精確表示)< 0.6 → 觸發,cum=0.5625,
        # adj d1 = 64×0.5625 = 36.0(精確)。若下限被誤改成 0.5,0.5625 不再觸發,
        # 此斷言翻紅。
        px = [("d1", 64.0), ("d2", 36.0)]
        adj_px, _ = _back_adjust_tw(px, [])
        self.assertEqual(adj_px, [("d1", 36.0), ("d2", 36.0)])

    def test_ratio_just_above_1_7_triggers_locks_upper_threshold_value(self):
        # 同上,鎖上限「數值」:r = 175/100 = 1.75(=7/4,二進位精確)> 1.7 → 觸發,
        # adj d1 = 100×1.75 = 175.0。若上限被誤改成 1.9,1.75 不再觸發,此斷言翻紅。
        px = [("d1", 100.0), ("d2", 175.0)]
        adj_px, _ = _back_adjust_tw(px, [])
        self.assertEqual(adj_px, [("d1", 175.0), ("d2", 175.0)])


class DividendSyncBackAdjustTest(unittest.TestCase):
    """情境 5:配息隨價格同步還原——斷層前的配息 ×factor,斷層後的配息不動。"""

    def test_dividend_before_gap_is_scaled_dividend_after_gap_is_untouched(self):
        # 沿用情境 1 的拆股(factor=[0.25,0.25,1.0,1.0])。
        # div_history 加入斷層前配息 (d1, 4.0) 與斷層後配息 (d4, 1.0)。
        # f_for(date) 用 bisect_right(dates, date)-1 找 date 所屬的 factor 索引:
        #   f_for("d1"): dates=["d1","d2","d3","d4"], bisect_right(...,"d1")=1, j=0 → factor[0]=0.25
        #   f_for("d4"): bisect_right(...,"d4")=4, j=3 → factor[3]=1.0
        # oracle: (d1,4.0) → 4.0*0.25=1.0;(d4,1.0) → 1.0*1.0=1.0(不動)
        px = [("d1", 100.0), ("d2", 100.0), ("d3", 25.0), ("d4", 25.0)]
        div = [("d1", 4.0), ("d4", 1.0)]
        adj_px, adj_div = _back_adjust_tw(px, div)
        self.assertEqual(
            adj_px,
            [("d1", 25.0), ("d2", 25.0), ("d3", 25.0), ("d4", 25.0)],
        )
        self.assertEqual(adj_div, [("d1", 1.0), ("d4", 1.0)])

    def test_dividend_exactly_on_pre_gap_price_date_is_scaled(self):
        # 除息日剛好落在斷層前的既有價格日(d2)上:f_for("d2") →
        # bisect_right(...,"d2")=2, j=1 → factor[1]=0.25 → 8.0×0.25=2.0。
        # 誠實註記(鑑別力範圍):此 fixture 中 factor[0]==factor[1]==0.25,
        # 故本測試鎖的是「同日除息取得斷層前尺度」的數值結果,**無法**區分
        # bisect_right/bisect_left。而且這在此函數的使用情境下是結構性的:
        # factor 階梯只出現在「被認定為分割」的日期,該日必然不在 ex_dates,
        # 反之除息日必在 ex_dates、同日斷層會被抑制(見情境 3)——所以
        # 「除息日恰逢 factor 階梯日」不可能發生,兩種 bisect 在所有可達輸入上等價。
        # oracle: (d2, 8.0) → 8.0*0.25 = 2.0
        px = [("d1", 100.0), ("d2", 100.0), ("d3", 25.0), ("d4", 25.0)]
        div = [("d2", 8.0)]
        _, adj_div = _back_adjust_tw(px, div)
        self.assertEqual(adj_div, [("d2", 2.0)])

    def test_dividend_earlier_than_all_price_dates_uses_first_factor(self):
        # f_for 的 j<0 fallback 分支(providers.py:154):除息日早於所有價格日
        # → bisect_right(dates,"d0")=0 → j=-1 → 改用 factor[0]。
        # oracle: 沿用情境 1(factor[0]=0.25),(d0, 10.0) → 10.0×0.25 = 2.5
        px = [("d1", 100.0), ("d2", 100.0), ("d3", 25.0), ("d4", 25.0)]
        div = [("d0", 10.0)]
        _, adj_div = _back_adjust_tw(px, div)
        self.assertEqual(adj_div, [("d0", 2.5)])


class BoundaryInputTest(unittest.TestCase):
    """情境 6:空序列 / 單筆 / close 為 None 或 0 的資料點邊界行為。"""

    def test_empty_price_history_returns_as_is(self):
        # oracle: n=0 < 2 → 直接 return price_history, div_history(空序列原樣)
        adj_px, adj_div = _back_adjust_tw([], [])
        self.assertEqual(adj_px, [])
        self.assertEqual(adj_div, [])

    def test_single_point_returns_as_is(self):
        # oracle: n=1 < 2 → 直接 return 原樣(sorted 後仍是同一筆)
        adj_px, adj_div = _back_adjust_tw([("d1", 100.0)], [])
        self.assertEqual(adj_px, [("d1", 100.0)])
        self.assertEqual(adj_div, [])

    def test_none_close_point_is_not_multiplied_and_does_not_crash(self):
        # oracle: price=[(d1,None),(d2,100),(d3,25)]
        #   i=2: prev=price[1]=100,cur=price[2]=25,不是 ex_date,r=0.25<0.6
        #        → cum=1.0*0.25=0.25;factor[1]=0.25
        #   i=1: prev=price[0]=None → `if prev and cur` 為假,跳過累乘;factor[0]=cum(=0.25)
        #   adj_px: d1 的 close 本身是 None →`(c*factor[i]) if c else c`短路回傳 None(不崩、不相乘)
        #           d2: 100*factor[1]=100*0.25=25.0
        #           d3: 25*factor[2]=25*1.0=25.0
        px = [("d1", None), ("d2", 100.0), ("d3", 25.0)]
        adj_px, adj_div = _back_adjust_tw(px, [])
        self.assertEqual(
            adj_px,
            [("d1", None), ("d2", 25.0), ("d3", 25.0)],
        )
        self.assertEqual(adj_div, [])

    def test_zero_close_point_is_not_multiplied_and_does_not_crash(self):
        # oracle: close=0 一樣是 falsy,`if prev and cur`/`if c else c` 短路行為
        # 與 None 相同(0 本身不參與 r 計算,也不被乘,原樣以 0 傳回)。
        px = [("d1", 0.0), ("d2", 100.0), ("d3", 25.0)]
        adj_px, adj_div = _back_adjust_tw(px, [])
        self.assertEqual(
            adj_px,
            [("d1", 0.0), ("d2", 25.0), ("d3", 25.0)],
        )
        self.assertEqual(adj_div, [])


class UnsortedInputTest(unittest.TestCase):
    """情境 7:輸入亂序時,函數內部 sorted() 會先整成升冪再處理(鎖現狀)。"""

    def test_unsorted_input_is_sorted_before_processing(self):
        # 輸入刻意打亂順序,函數第一行 `sorted(price_history)` 會按 tuple
        # (date, close) 之字典序排序,等效於情境 1 的 oracle(排序後
        # 變回 [(d1,100),(d2,100),(d3,25),(d4,25)],結果同情境 1)。
        px_unsorted = [("d3", 25.0), ("d1", 100.0), ("d2", 100.0), ("d4", 25.0)]
        adj_px, adj_div = _back_adjust_tw(px_unsorted, [])
        self.assertEqual(
            adj_px,
            [("d1", 25.0), ("d2", 25.0), ("d3", 25.0), ("d4", 25.0)],
        )
        self.assertEqual(adj_div, [])


if __name__ == "__main__":
    unittest.main()

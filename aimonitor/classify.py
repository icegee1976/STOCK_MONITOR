"""價位分類 + 「便宜價何時出現」的誠實回答。

關於規格第 2 點「預測便宜價何時出現」:
  價格何時觸及某價位 = 市場擇時,沒有人能可靠預測。本模組「不」給假裝
  精準的日期,而是給三個可驗證的客觀量:
    1. 缺口  : 現價距便宜價還要跌幾 %。
    2. 歷史 : 過去 N 年有多少比例的交易日落在便宜價(含)以下。
    3. 機率 : 以歷史年化波動率,用幾何布朗運動「首次穿越下界」公式估
             未來 T 年內「曾經」觸及便宜價的機率(零漂移,偏保守)。
  這是統計估計,不是預測。會在報表明白標示。
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime

from .valuation import ZONE_KEYS, ZONE_LABEL

# 現價落點 → 標籤 (6 區)
REGION_ORDER = ["大特價區", "便宜價區", "合理價區", "昂貴價區", "瘋狂價區", "超瘋狂價區"]


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def classify_region(price: float, zones: dict) -> str:
    if price <= zones["super_bargain"]:
        return "大特價區"
    if price <= zones["cheap"]:
        return "便宜價區"
    if price <= zones["fair"]:
        return "合理價區"
    if price <= zones["expensive"]:
        return "昂貴價區"
    if price <= zones["euphoria"]:
        return "瘋狂價區"
    return "超瘋狂價區"


def annualized_vol(price_history) -> float | None:
    """以日對數報酬的標準差年化。"""
    closes = [c for _, c in price_history if c and c > 0]
    if len(closes) < 30:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
            if closes[i - 1] > 0]
    if len(rets) < 20:
        return None
    sd = statistics.pstdev(rets)
    return sd * math.sqrt(252)


def prob_hit_barrier(price: float, barrier: float, vol: float, years: float) -> float | None:
    """未來 years 年內最低價 ≤ barrier 的機率(反射原理)。
       x = ln(S0/L) > 0;  P = 2·Φ(-x / (σ√T))。
       註:此處「零漂移」指 ln S 的漂移 = 0(非價格鞅 μ=0;後者 ln S 漂移
       為 −σ²/2、觸及下界機率會略高)。本式採較保守的版本,經蒙地卡羅驗證一致。"""
    if not vol or vol <= 0 or years <= 0:
        return None
    if barrier >= price:
        return 1.0
    x = math.log(price / barrier)
    sigma_t = vol * math.sqrt(years)
    if sigma_t <= 0:
        return 0.0
    return max(0.0, min(1.0, 2.0 * _norm_cdf(-x / sigma_t)))


def days_below_fraction(price_history, level: float) -> float | None:
    """過去歷史中,收盤 ≤ level 的交易日比例 (0-1)。"""
    closes = [c for _, c in price_history if c]
    if not closes:
        return None
    return sum(1 for c in closes if c <= level) / len(closes)


def analyze(price: float, zones: dict, price_history, horizons_years) -> dict:
    """整合現價分類與『便宜價何時出現』的客觀量。"""
    region = classify_region(price, zones)
    cheap = zones["cheap"]
    superb = zones["super_bargain"]
    premium_over_cheap = (price - cheap) / cheap            # 現價較便宜價貴幾 %(可 >100%)
    # 從現價「需下跌」幾 % 才會到便宜價(永遠 < 100%,這才是直覺數字)
    decline_needed = max(0.0, (price - cheap) / price) if price else 0.0
    vol = annualized_vol(price_history)

    out = {
        "region": region,
        "is_buy": price <= cheap,                            # 進入便宜(含)以下 → 提醒
        "gap_to_cheap_pct": round(premium_over_cheap * 100, 1),  # 排序用:較便宜價的溢價
        "drop_to_cheap_pct": round(decline_needed * 100, 1),     # 顯示用:需下跌幾 %
        "annual_vol_pct": round(vol * 100, 1) if vol else None,
        "price_percentile": None,        # 現價在過去股價分布的百分位 (0=史上最低,100=史上最高)
        "prob_hit_cheap": {},
        "prob_hit_superbargain": {},
    }
    f = days_below_fraction(price_history, price)
    if f is not None:
        out["price_percentile"] = round(f * 100, 1)
    for y in horizons_years:
        pc = prob_hit_barrier(price, cheap, vol, y)
        ps = prob_hit_barrier(price, superb, vol, y)
        if pc is not None:
            out["prob_hit_cheap"][y] = round(pc * 100, 1)
        if ps is not None:
            out["prob_hit_superbargain"][y] = round(ps * 100, 1)
    return out

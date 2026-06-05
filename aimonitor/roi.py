"""情境式投報率試算。

關於規格第 3 點「預估投報率」:
  未來報酬無法預測。能誠實做的是「情境式目標價報酬」——
  如果未來股價回到 [便宜/合理/昂貴/瘋狂] 價(= 你的估值帶),
  在不同持有年數下,總報酬與年化報酬各是多少。
  這是 if-then 模型,不是保證。已內含交易稅費,並處理台幣/美元匯率。
"""

from __future__ import annotations

from .valuation import ZONE_KEYS, ZONE_LABEL


def _buy_cost(price: float, capital: float, market: str, fees: dict) -> tuple[float, float]:
    """回傳 (可買股數, 實際投入含手續費)。台股以「股」計(1張=1000股,這裡用股)。"""
    if market == "TW":
        br = fees["tw"]["brokerage"] * fees["tw"].get("brokerage_discount", 1.0)
        # 買進成本 = 股數×價×(1+手續費);反推可買股數
        shares = int(capital / (price * (1 + br)))
        spent = shares * price * (1 + br)
    else:
        comm = fees["us"].get("commission", 0.0)
        shares = capital / price                 # 美股可買碎股,用浮點
        spent = shares * price + comm
    return shares, spent


def _sell_proceeds(price: float, shares: float, market: str, fees: dict) -> float:
    if market == "TW":
        br = fees["tw"]["brokerage"] * fees["tw"].get("brokerage_discount", 1.0)
        tax = fees["tw"].get("tax_sell", 0.003)
        return shares * price * (1 - br - tax)
    comm = fees["us"].get("commission", 0.0)
    return shares * price - comm


def scenario_roi(stock_cfg, data, zones_info, capital: float, config: dict,
                 horizons=None, capital_currency: str = None) -> dict:
    """capital 以 capital_currency 計(預設同標的幣別)。"""
    market = stock_cfg["market"].upper()
    fees = config.get("fees", {"tw": {}, "us": {}})
    price = data.price
    stock_ccy = data.currency or ("TWD" if market == "TW" else "USD")
    cap_ccy = capital_currency or stock_ccy
    horizons = horizons or config.get("roi_horizons_years", [1, 3, 5])

    fx = config.get("fx", {}).get("USDTWD", 32.0)
    # 把投入資金換成標的幣別
    if cap_ccy == stock_ccy:
        capital_in_stock = capital
    elif cap_ccy == "TWD" and stock_ccy == "USD":
        capital_in_stock = capital / fx
    elif cap_ccy == "USD" and stock_ccy == "TWD":
        capital_in_stock = capital * fx
    else:
        capital_in_stock = capital

    shares, spent = _buy_cost(price, capital_in_stock, market, fees)
    if shares <= 0:
        return {"error": f"資金不足以買進 1 股(現價 {price} {stock_ccy})"}

    div_y = data.dividend_yield or 0.0
    target_year = zones_info.get("target_year")

    scenarios = []
    for zkey in ["cheap", "fair", "expensive", "euphoria"]:
        tprice = zones_info["zones"][zkey]
        rows = []
        for yrs in horizons:
            proceeds = _sell_proceeds(tprice, shares, market, fees)
            divs = shares * price * div_y * yrs        # 簡化:股利率持平累積
            total = proceeds + divs
            ret = (total - spent) / spent
            ann = (1 + ret) ** (1 / yrs) - 1 if (1 + ret) > 0 else -1.0
            rows.append({"years": yrs, "total_return_pct": round(ret * 100, 1),
                         "annualized_pct": round(ann * 100, 1),
                         "value_in_stock_ccy": round(total, 2)})
        scenarios.append({"zone": zkey, "label": ZONE_LABEL[zkey],
                          "target_price": tprice, "rows": rows})

    return {
        "ticker": stock_cfg["ticker"], "name": stock_cfg.get("name", ""),
        "market": market, "stock_ccy": stock_ccy, "cap_ccy": cap_ccy,
        "price": price, "shares": shares, "spent": round(spent, 2),
        "dividend_yield_pct": round(div_y * 100, 2),
        "target_year": target_year, "fx_usdtwd": fx,
        "scenarios": scenarios,
        "fx_note": (stock_ccy != cap_ccy),
    }

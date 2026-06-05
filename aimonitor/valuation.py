"""價格帶估價引擎 —— 孫慶龍《AI 護國群山》方法論的程式化。

三種估價法:
  pe_band    : forward_EPS × 本益比帶 = 五個價格帶 (適合會賺錢的成長股)
  ps_band    : (forward_營收 / 股數) × 股價營收比帶 (適合燒錢中的太空股)
  price_band : 過去 N 年股價分布的百分位 (最保守後備,不靠 EPS 假設)

五個價格帶 (由低到高): super_bargain 大特價 / cheap 便宜 / fair 合理 /
                       expensive 昂貴 / euphoria 瘋狂。
"""

from __future__ import annotations

from datetime import datetime

ZONE_KEYS = ["super_bargain", "cheap", "fair", "expensive", "euphoria"]
ZONE_LABEL = {
    "super_bargain": "大特價",
    "cheap": "便宜價",
    "fair": "合理價",
    "expensive": "昂貴價",
    "euphoria": "瘋狂價",
}


class ValuationError(Exception):
    pass


def _percentile(sorted_vals, pct):
    """線性插值百分位 (pct 為 0-100)。"""
    if not sorted_vals:
        raise ValuationError("無歷史資料可計算百分位")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _auto_bands_from_history(history_pairs, percentiles: dict) -> dict:
    """從 [(date, value)] 歷史序列,依百分位設定算出五個倍數/價位。"""
    vals = sorted(v for _, v in history_pairs if v and v > 0)
    if len(vals) < 30:
        raise ValuationError(f"歷史樣本過少({len(vals)}筆),不足以推河流圖。改用明確 bands 或 price_band。")
    return {k: round(_percentile(vals, percentiles[k]), 4) for k in ZONE_KEYS}


def _forward_eps(val_cfg: dict) -> tuple[float, int, str]:
    """回傳 (forward_eps, target_year, 說明)。"""
    if "forward_eps" in val_cfg and val_cfg["forward_eps"] is not None:
        ty = int(val_cfg.get("target_year", datetime.now().year + 2))
        return float(val_cfg["forward_eps"]), ty, "明確指定"
    d = val_cfg.get("derive")
    if d:
        # 注意:YAML 會把無正負號指數(如 2.89e12)當字串,故一律強制轉 float。
        base_rev = float(d["base_revenue"])
        cagr = float(d["revenue_cagr"])
        margin = float(d["net_margin"])
        shares = float(d["shares"])
        base_year = int(d.get("base_year", 2024))
        ty = int(d.get("target_year", base_year + 5))
        years = max(ty - base_year, 0)
        rev = base_rev * ((1 + cagr) ** years)
        eps = (rev * margin) / shares
        expl = (f"推導: {base_rev:.3g}×(1+{cagr:.0%})^{years}"
                f"×{margin:.3%}÷{shares:.3g}")
        return eps, ty, expl
    raise ValuationError("pe_band 需要 forward_eps 或 derive 區塊")


def compute_zones(stock_cfg: dict, data, config: dict) -> dict:
    """回傳 {zones, method, anchor(EPS/SPS), target_year, assumptions, warnings}。"""
    val = stock_cfg.get("valuation", {})
    method = val.get("method", "price_band")
    pctl = config.get("pe_band_percentiles", {
        "super_bargain": 10, "cheap": 25, "fair": 50, "expensive": 75, "euphoria": 90})
    warnings = []
    result = {"method": method, "target_year": None, "anchor": None,
              "anchor_kind": None, "assumptions": "", "warnings": warnings}

    if method == "pe_band":
        eps, ty, expl = _forward_eps(val)
        bands = val.get("pe_bands")
        if bands == "auto":
            if not data.per_history:
                raise ValuationError("無本益比歷史(auto),請改明確 pe_bands 或 price_band")
            bands = _auto_bands_from_history(data.per_history, pctl)
            if data.per_history_approx:
                warnings.append("美股本益比河流圖為近似(歷史股價÷現行EPS)")
            expl += " | PE帶=歷史本益比百分位(auto)"
        zones = {k: round(eps * float(bands[k]), 2) for k in ZONE_KEYS}
        implied_pe = (data.price / eps) if (data.price and eps) else None
        result.update(zones=zones, target_year=ty, anchor=round(eps, 3),
                      anchor_kind="forward_EPS", pe_bands=bands, assumptions=expl,
                      implied_multiple=round(implied_pe, 1) if implied_pe else None,
                      implied_kind="forward P/E", eps_now=data.trailing_eps)
        # 誠實護欄:現價隱含本益比遠高於瘋狂價帶 → 多半是 forward_EPS 設太低/過時
        if implied_pe and implied_pe > float(bands["euphoria"]) * 1.05:
            warnings.append(
                f"現價隱含 forward P/E≈{implied_pe:.0f},高於你瘋狂價本益比"
                f"{float(bands['euphoria']):.0f};forward_EPS({eps:.1f}) 可能過低或過時,請校正"
                + (f"(目前 trailing EPS≈{data.trailing_eps})" if data.trailing_eps else ""))

    elif method == "ps_band":
        rev = float(val["forward_revenue"])
        shares = float(val["shares"])
        sps = rev / shares                      # 每股營收
        bands = val.get("ps_bands")
        if bands == "auto":
            raise ValuationError("ps_band 目前需明確 ps_bands")
        zones = {k: round(sps * float(bands[k]), 2) for k in ZONE_KEYS}
        ty = int(val.get("target_year", datetime.now().year + 1))
        implied_ps = (data.price / sps) if (data.price and sps) else None
        result.update(zones=zones, target_year=ty, anchor=round(sps, 4),
                      anchor_kind="forward_每股營收", ps_bands=bands,
                      assumptions=f"每股營收={rev:.3g}/{shares:.3g}={sps:.3f}",
                      implied_multiple=round(implied_ps, 1) if implied_ps else None,
                      implied_kind="forward P/S")
        warnings.append("燒錢期公司:股價營收比估值波動大,僅供相對參考")
        if implied_ps and implied_ps > float(bands["euphoria"]) * 1.05:
            warnings.append(f"現價隱含 forward P/S≈{implied_ps:.0f},高於瘋狂價帶;"
                            f"forward_revenue 假設可能過低/過時,請校正")

    elif method == "price_band":
        yrs = int(val.get("lookback_years", config.get("history_years", 5)))
        if not data.price_history:
            raise ValuationError("無股價歷史可做 price_band")
        zones = _auto_bands_from_history(data.price_history, pctl)
        result.update(zones=zones, target_year=None, anchor=None,
                      anchor_kind="股價分布",
                      assumptions=f"過去約{yrs}年股價分布百分位")
        warnings.append("price_band 純看歷史股價分布,不含未來成長假設")

    else:
        raise ValuationError(f"未知估價法: {method}")

    # 確保單調遞增
    z = result["zones"]
    vals = [z[k] for k in ZONE_KEYS]
    if any(vals[i] > vals[i + 1] for i in range(len(vals) - 1)):
        warnings.append("價格帶非單調,已重新排序")
        for k, v in zip(ZONE_KEYS, sorted(vals)):
            z[k] = v
    return result

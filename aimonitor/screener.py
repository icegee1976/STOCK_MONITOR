"""篩選器 —— 在你「精選的成長股清單」中,排出目前相對便宜的標的。

誠實聲明:這不是「幫你從全市場選股」的神諭。標的池是你 watchlist.yaml
裡、依透明主題(AI 算力/記憶體/光通訊/電源散熱/ASIC/太空)精選的成長股。
篩選器只回答:「在這份清單裡,現在誰落在便宜價(含)以下、誰距離便宜最近」。
這樣可避免『追市場熱度而在瘋狂價買進』的盲點。
"""

from __future__ import annotations

from .valuation import compute_zones, ValuationError
from .classify import analyze


def screen(watchlist: list, fetch_fn, config: dict) -> list:
    """回傳每檔的分析結果,依『距便宜價缺口』由小到大排序(越前面越接近/已便宜)。"""
    horizons = config.get("roi_horizons_years", [1, 3, 5])
    rows = []
    for cfg in watchlist:
        data = fetch_fn(cfg)
        item = {"cfg": cfg, "data": data, "zones": None, "analysis": None, "error": ""}
        if not data.ok():
            item["error"] = data.error or "無資料"
            rows.append(item)
            continue
        try:
            zinfo = compute_zones(cfg, data, config)
            item["zones"] = zinfo
            item["analysis"] = analyze(data.price, zinfo["zones"], data.price_history, horizons)
        except ValuationError as e:
            item["error"] = f"估價失敗: {e}"
        rows.append(item)

    def sort_key(it):
        if it["analysis"] is None:
            return (2, 9e9)
        # 已便宜(負缺口)排最前,其次缺口小的
        return (0, it["analysis"]["gap_to_cheap_pct"])

    rows.sort(key=sort_key)
    return rows

# -*- coding: utf-8 -*-
"""用校正結果(_calibrations.json)重生 watchlist.yaml。

五價格帶由 fair_multiple × 河流圖幾何比例展開:
  standard 比例取自 PDF 台積電(12.81/16.49/23.85/27.53/31.20 ÷ 合理23.85)。
  wide 比例給高波動(太空/記憶體循環)用,瘋狂帶拉到 2.1×。
保留各檔的 name/theme/note(從現有 watchlist 取),並寫入校正來源註解。
"""
import json
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))

RATIOS = {
    "standard": {"super_bargain": 0.537, "cheap": 0.691, "fair": 1.0,
                 "expensive": 1.154, "euphoria": 1.308},
    "wide": {"super_bargain": 0.45, "cheap": 0.68, "fair": 1.0,
             "expensive": 1.45, "euphoria": 2.10},
}
ZK = ["super_bargain", "cheap", "fair", "expensive", "euphoria"]


def bands(fair, shape):
    r = RATIOS.get(shape, RATIOS["standard"])
    return {k: round(fair * r[k], 2) for k in ZK}


def fnum(x):
    """數字轉精簡字串(整數去小數;科學記號保留)。"""
    if x is None:
        return "null"
    if abs(x) >= 1e7:
        return f"{x:.3e}"
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.4g}"


def main():
    with open(os.path.join(HERE, "_calibrations.json"), encoding="utf-8") as f:
        calibs = {c["ticker"]: c for c in json.load(f)}
    with open(os.path.join(HERE, "watchlist.yaml"), encoding="utf-8") as f:
        cur = yaml.safe_load(f)
    meta = {str(s["ticker"]): s for s in cur.get("stocks", [])}

    L = []
    L.append("# ============================================================")
    L.append("#  監測清單 (watchlist) — AI + 太空 成長股  [已用最新分析師共識校正]")
    L.append("# ============================================================")
    L.append("#  forward_eps / forward_revenue 來自 2026/6 yfinance 分析師共識 + 對抗式驗證。")
    L.append("#  五價格帶 = fair_multiple × 河流圖幾何(PDF 台積電比例)。仍可自由修改。")
    L.append("#  ⚠ 便宜價建立在『未來 EPS 假設』上,非 API 事實;請隨季報持續校正。")
    L.append("# ============================================================")
    L.append("")
    L.append("stocks:")
    L.append("")

    # 依現有清單順序輸出
    for tk, m in meta.items():
        c = calibs.get(tk)
        name = m.get("name", tk)
        market = m["market"]
        theme = m.get("theme", [])
        note = m.get("note", "")
        L.append(f"  - ticker: \"{tk}\"")
        L.append(f"    market: {market}")
        L.append(f"    name: {name}")
        if theme:
            L.append(f"    theme: [{', '.join(str(t) for t in theme)}]")
        if not c:
            # 沒校正到 → 保留原 valuation 區塊
            L.append("    valuation:")
            for line in yaml.safe_dump(m.get("valuation", {}), allow_unicode=True,
                                       default_flow_style=False).splitlines():
                L.append("      " + line)
            if note:
                L.append(f"    note: \"{note}\"")
            L.append("")
            continue

        method = c["method"]
        ty = c.get("target_year", 2027)
        fair = c["fair_multiple"]
        shape = c.get("band_shape", "standard")
        bd = bands(fair, shape)
        conf = "verified" if c.get("approved") else "verifier-corrected"
        impl = c.get("implied_current_multiple")
        base_note = (note or "").replace('"', "'").replace("\n", " ").strip()

        L.append("    valuation:")
        L.append(f"      method: {method}")
        if tk == "2330":
            # 台積電保留 PDF 推導鏈(招牌範例),fair 帶用校正
            L.append("      derive:                 # PDF p.4 推導鏈(保守)")
            L.append("        base_revenue: 2.89e+12")
            L.append("        revenue_cagr: 0.24")
            L.append("        net_margin: 0.41362")
            L.append("        shares: 2.593e+10")
            L.append("        target_year: 2029")
        elif method == "pe_band":
            L.append(f"      forward_eps: {fnum(c.get('forward_eps'))}"
                     f"        # 2026/6 共識 (fair P/E {fair:g})")
            L.append(f"      target_year: {ty}")
        elif method == "ps_band":
            L.append(f"      forward_revenue: {fnum(c.get('forward_revenue'))}"
                     f"   # 下年度共識營收")
            L.append(f"      shares: {fnum(c.get('shares'))}")
            L.append(f"      target_year: {ty}")

        band_key = "pe_bands" if method == "pe_band" else "ps_bands"
        L.append(f"      {band_key}: {{ super_bargain: {bd['super_bargain']:g}, "
                 f"cheap: {bd['cheap']:g}, fair: {bd['fair']:g}, "
                 f"expensive: {bd['expensive']:g}, euphoria: {bd['euphoria']:g} }}"
                 f"  # {shape}")
        # 註解:原始理由 + 校正標籤
        impl_txt = f"現價隱含≈{impl:g}x; " if impl else ""
        L.append(f"    note: \"{base_note}  [校正 2026/6:{impl_txt}{conf}]\"")
        L.append("")

    # 備份舊檔再寫入
    import shutil
    bak = os.path.join(HERE, "watchlist.pdf_seed.yaml")
    if not os.path.exists(bak):
        shutil.copy(os.path.join(HERE, "watchlist.yaml"), bak)
    out = os.path.join(HERE, "watchlist.yaml")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"已重生 {out}（備份: {bak}）。校正 {len(calibs)} 檔。")
    # 驗證可被 yaml 載入
    with open(out, encoding="utf-8") as f:
        chk = yaml.safe_load(f)
    print("YAML 載入 OK，stocks 數:", len(chk.get("stocks", [])))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""AI 護國群山 — Streamlit 視覺化儀表板。

執行:
    pip install streamlit plotly
    streamlit run app.py

四個視圖:總覽 / 個股河流圖 / 投報率試算 / 便宜清單。
完全沿用 aimonitor 引擎,所以與 CLI 同一套估價邏輯。
"""

from __future__ import annotations

import os
import sys

import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from aimonitor import providers
from aimonitor.valuation import compute_zones, ValuationError, ZONE_KEYS, ZONE_LABEL
from aimonitor.classify import analyze
from aimonitor.roi import scenario_roi

try:
    import yaml
except ImportError:
    st.error("需要 PyYAML:pip install pyyaml")
    st.stop()

try:
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except Exception:
    HAS_PLOTLY = False

# 區域顏色 (低→高:綠→紅)
REGION_HEX = {
    "大特價區": "#1b7a3d", "便宜價區": "#2e9e57", "合理價區": "#caa21a",
    "昂貴價區": "#e07b1a", "瘋狂價區": "#d2412f", "超瘋狂價區": "#9b1c1c",
}
BAND_FILL = [  # (下界key, 上界key, 顏色, 標籤)
    (None, "super_bargain", "rgba(27,122,61,0.16)", "大特價"),
    ("super_bargain", "cheap", "rgba(46,158,87,0.16)", "便宜價"),
    ("cheap", "fair", "rgba(202,162,26,0.14)", "合理價"),
    ("fair", "expensive", "rgba(224,123,26,0.14)", "昂貴價"),
    ("expensive", "euphoria", "rgba(210,65,47,0.14)", "瘋狂價"),
]


# --------------------------------------------------------------------------- #
#  資料載入 (含快取)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def load_config():
    with open(os.path.join(HERE, "config.yaml"), "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    with open(os.path.join(HERE, "watchlist.yaml"), "r", encoding="utf-8") as f:
        wl = yaml.safe_load(f)
    stocks = wl.get("stocks", []) if isinstance(wl, dict) else wl
    return config, stocks


@st.cache_data(ttl=900, show_spinner=False)
def analyze_stock(ticker, _cfg, _config):
    """回傳可序列化的分析結果 dict。_cfg/_config 前綴底線避免被 hash。"""
    pcfg = _config.get("providers", {})
    yrs = int(_config.get("history_years", 5))
    data = providers.fetch(_cfg, pcfg, yrs, use_cache=True)
    out = {"ticker": ticker, "name": _cfg.get("name", ticker), "market": _cfg["market"],
           "error": "", "data": None, "zones": None, "analysis": None}
    if not data.ok():
        out["error"] = data.error or "無資料"
        return out
    out["data"] = {"price": data.price, "price_date": data.price_date,
                   "currency": data.currency, "source": data.source,
                   "price_history": data.price_history,
                   "dividend_yield": data.dividend_yield,
                   "trailing_eps": data.trailing_eps}
    try:
        z = compute_zones(_cfg, data, _config)
        out["zones"] = z
        out["analysis"] = analyze(data.price, z["zones"], data.price_history,
                                  _config.get("roi_horizons_years", [1, 3, 5]))
    except ValuationError as e:
        out["error"] = f"估價失敗: {e}"
    return out


def build_all(stocks, config, market_filter=None):
    items = []
    prog = st.progress(0.0, text="抓取報價中…")
    flt = [s for s in stocks if (not market_filter or s["market"] == market_filter)]
    for i, s in enumerate(flt):
        items.append(analyze_stock(str(s["ticker"]), s, config))
        prog.progress((i + 1) / len(flt), text=f"抓取 {s.get('name','')} …")
    prog.empty()
    return items


def money(x, ccy):
    if x is None:
        return "—"
    return f"{'NT$' if ccy=='TWD' else '$'}{x:,.2f}"


# --------------------------------------------------------------------------- #
#  圖表
# --------------------------------------------------------------------------- #
def river_chart(item):
    """歷史股價 + 五價格帶填色 (本益比河流圖)。"""
    data, z = item["data"], item["zones"]
    zones = z["zones"]
    hist = data["price_history"]
    if not HAS_PLOTLY:
        # 後備:用 streamlit 原生折線
        import pandas as pd
        df = pd.DataFrame(hist, columns=["date", "price"]).set_index("date")
        for k in ZONE_KEYS:
            df[ZONE_LABEL[k]] = zones[k]
        st.line_chart(df)
        return
    xs = [d for d, _ in hist]
    ys = [c for _, c in hist]
    lo = min(min(ys) * 0.95, zones["super_bargain"] * 0.9)
    hi = max(max(ys) * 1.05, zones["euphoria"] * 1.05)
    fig = go.Figure()
    for lk, uk, color, label in BAND_FILL:
        y0 = lo if lk is None else zones[lk]
        y1 = zones[uk]
        fig.add_hrect(y0=y0, y1=y1, fillcolor=color, line_width=0, layer="below")
    # euphoria 以上
    fig.add_hrect(y0=zones["euphoria"], y1=hi, fillcolor="rgba(155,28,28,0.13)",
                  line_width=0, layer="below")
    for k in ZONE_KEYS:
        fig.add_hline(y=zones[k], line_dash="dot", line_color="gray", line_width=1,
                      annotation_text=f"{ZONE_LABEL[k]} {zones[k]:,.0f}",
                      annotation_position="right",
                      annotation_font_size=11)
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="股價",
                             line=dict(color="#1f3b57", width=1.6)))
    fig.add_trace(go.Scatter(x=[xs[-1]], y=[ys[-1]], mode="markers",
                             marker=dict(color=REGION_HEX.get(item["analysis"]["region"], "#000"),
                                         size=12, line=dict(color="white", width=1.5)),
                             name="現價"))
    fig.update_layout(height=460, margin=dict(l=10, r=120, t=30, b=10),
                      yaxis_range=[lo, hi], showlegend=False,
                      title=f"{item['name']} 價格帶河流圖")
    st.plotly_chart(fig, use_container_width=True)


def roi_bar(r):
    if not HAS_PLOTLY:
        return
    horizons = [row["years"] for row in r["scenarios"][0]["rows"]]
    fig = go.Figure()
    for sc in r["scenarios"]:
        fig.add_trace(go.Bar(name=sc["label"],
                             x=[f"{y}年" for y in horizons],
                             y=[row["annualized_pct"] for row in sc["rows"]]))
    fig.update_layout(barmode="group", height=380, title="各情境年化報酬 (%)",
                      margin=dict(l=10, r=10, t=40, b=10),
                      yaxis_title="年化報酬 %")
    st.plotly_chart(fig, use_container_width=True)


# --------------------------------------------------------------------------- #
#  主程式
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="AI 護國群山 監測器", layout="wide", page_icon="📈")
config, stocks = load_config()

st.title("📈 AI 護國群山 — 美股／台股 AI＋太空 成長股監測器")
st.caption("孫慶龍《AI 護國群山投資藍圖》方法論 × Computex 產業觀察。⚠ 資訊／教育用途,非投資建議;免費數據為延遲報價。")

with st.sidebar:
    st.header("設定")
    market_filter = st.selectbox("市場", ["全部", "TW", "US"], index=0)
    mf = None if market_filter == "全部" else market_filter
    if st.button("🔄 重新抓取 (清快取)"):
        st.cache_data.clear()
        providers_cache = os.path.join(HERE, ".cache")
        import shutil
        shutil.rmtree(providers_cache, ignore_errors=True)
        st.rerun()
    st.divider()
    st.caption("價格帶:大特價→便宜→合理→昂貴→瘋狂。進入便宜價(含)以下 = 提醒買進。")

tab_overview, tab_stock, tab_roi, tab_screen = st.tabs(
    ["📊 總覽", "🔍 個股河流圖", "💰 投報率試算", "🟢 便宜清單"])

# ---------- 總覽 ----------
with tab_overview:
    items = build_all(stocks, config, mf)
    rows = []
    for it in items:
        if it["error"]:
            rows.append({"標的": it["name"], "代號": it["ticker"], "現價": "—",
                         "價位": "錯誤", "需跌%": None, "1年觸及%": None,
                         "隱含倍數": None, "錨點": it["error"][:30]})
            continue
        a, z, d = it["analysis"], it["zones"], it["data"]
        yr1 = next(iter(config.get("roi_horizons_years", [1])), 1)
        rows.append({
            "標的": it["name"], "代號": it["ticker"],
            "現價": money(d["price"], d["currency"]),
            "價位": a["region"],
            "需跌%": 0.0 if a["is_buy"] else a["drop_to_cheap_pct"],
            "1年觸及%": a.get("prob_hit_cheap", {}).get(yr1),
            "隱含倍數": z.get("implied_multiple"),
            "錨點": f"{z.get('anchor_kind','')}={z.get('anchor','')}",
        })
    import pandas as pd
    df = pd.DataFrame(rows).sort_values("需跌%", na_position="last")

    def color_region(v):
        return f"color: white; background-color: {REGION_HEX.get(v, '#888')}"
    st.dataframe(
        df.style.map(color_region, subset=["價位"]),
        use_container_width=True, height=640,
        column_config={
            "需跌%": st.column_config.NumberColumn(format="%.1f%%"),
            "1年觸及%": st.column_config.NumberColumn(format="%.0f%%"),
            "隱含倍數": st.column_config.NumberColumn(format="%.1f"),
        })
    buys = [it for it in items if it["analysis"] and it["analysis"]["is_buy"]]
    if buys:
        st.success("★ 已進入便宜價:" + "、".join(it["name"] for it in buys))
    else:
        st.info("目前清單中沒有標的進入便宜價(在 2026 狂熱行情下屬正常 —— 耐心等回檔)。")

# ---------- 個股 ----------
with tab_stock:
    names = {f"{s.get('name','')} ({s['ticker']})": str(s["ticker"]) for s in stocks
             if (not mf or s["market"] == mf)}
    pick = st.selectbox("選擇標的", list(names.keys()))
    if pick:
        s_cfg = next(s for s in stocks if str(s["ticker"]) == names[pick])
        it = analyze_stock(names[pick], s_cfg, config)
        if it["error"]:
            st.error(it["error"])
        else:
            a, z, d = it["analysis"], it["zones"], it["data"]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("現價", money(d["price"], d["currency"]), d["price_date"])
            c2.metric("價位", a["region"])
            c3.metric("距便宜價", "已便宜" if a["is_buy"] else f"需跌 {a['drop_to_cheap_pct']}%")
            if z.get("implied_multiple") is not None:
                c4.metric(f"隱含{z.get('implied_kind','倍數')}", z["implied_multiple"])
            river_chart(it)
            cc1, cc2 = st.columns(2)
            with cc1:
                st.markdown(f"**估值錨點**:{z.get('anchor_kind')} = {z.get('anchor')}"
                            + (f"(目標 {z['target_year']} 年)" if z.get("target_year") else ""))
                st.markdown(f"**假設**:{z.get('assumptions')}")
                if a.get("annual_vol_pct"):
                    st.markdown(f"**年化波動率**:{a['annual_vol_pct']}%")
                if a.get("price_percentile") is not None:
                    st.markdown(f"**現價百分位**:過去約{config.get('history_years',5)}年第 {a['price_percentile']} 百分位")
            with cc2:
                probs = a.get("prob_hit_cheap", {})
                if probs and not a["is_buy"]:
                    st.markdown("**觸及便宜價機率**(統計估計,非預測):")
                    st.markdown("　".join(f"{y}年內 **{p}%**" for y, p in probs.items()))
                for w in z.get("warnings", []):
                    st.warning(w)
            if s_cfg.get("note"):
                st.caption(s_cfg["note"])

# ---------- 投報率 ----------
with tab_roi:
    names2 = {f"{s.get('name','')} ({s['ticker']})": str(s["ticker"]) for s in stocks}
    colp, cola, colc = st.columns([2, 1, 1])
    pick2 = colp.selectbox("標的", list(names2.keys()), key="roi_pick")
    amount = cola.number_input("投入金額", min_value=1000.0, value=300000.0, step=10000.0)
    cap_ccy = colc.selectbox("資金幣別", ["TWD", "USD"], index=0)
    if pick2:
        s_cfg = next(s for s in stocks if str(s["ticker"]) == names2[pick2])
        it = analyze_stock(names2[pick2], s_cfg, config)
        if it["error"]:
            st.error(it["error"])
        else:
            data = providers.fetch(s_cfg, config.get("providers", {}),
                                   int(config.get("history_years", 5)), use_cache=True)
            stock_ccy = data.currency or ("TWD" if s_cfg["market"] == "TW" else "USD")
            if cap_ccy != stock_ccy:
                config.setdefault("fx", {})["USDTWD"] = providers.usd_twd(
                    config.get("fx", {}).get("USDTWD", 32.0))
            r = scenario_roi(s_cfg, data, it["zones"], amount, config, capital_currency=cap_ccy)
            if "error" in r:
                st.error(r["error"])
            else:
                sh = f"{int(r['shares']):,} 股" if r["market"] == "TW" else f"{r['shares']:,.3f} 股"
                st.markdown(f"投入 **{money(r['spent'], r['stock_ccy'])}** → 買進 **{sh}** @ {money(r['price'], r['stock_ccy'])}")
                if r["fx_note"]:
                    st.warning(f"跨幣別:資金 {r['cap_ccy']} ≠ 標的 {r['stock_ccy']}(USD/TWD≈{r['fx_usdtwd']:.2f}),含匯率風險")
                st.caption("情境:若未來股價回到各價格帶,持有 N 年的總報酬/年化。這是 if-then 模型,非保證。")
                roi_rows = []
                for sc in r["scenarios"]:
                    row = {"情境": sc["label"], "目標價": money(sc["target_price"], r["stock_ccy"])}
                    for rr in sc["rows"]:
                        row[f"{rr['years']}年總報酬"] = f"{rr['total_return_pct']:+.0f}%"
                        row[f"{rr['years']}年年化"] = f"{rr['annualized_pct']:+.0f}%"
                    roi_rows.append(row)
                import pandas as pd
                st.table(pd.DataFrame(roi_rows))
                roi_bar(r)

# ---------- 便宜清單 ----------
with tab_screen:
    st.markdown("依「距便宜價需跌幅」排序,越上面越接近便宜。")
    items = build_all(stocks, config, mf)
    ranked = sorted([it for it in items if it["analysis"]],
                    key=lambda it: (not it["analysis"]["is_buy"], it["analysis"]["drop_to_cheap_pct"]))
    for it in ranked:
        a, z, d = it["analysis"], it["zones"], it["data"]
        emoji = "🟢" if a["is_buy"] else ("🟡" if a["drop_to_cheap_pct"] < 15 else "🔴")
        with st.expander(f"{emoji} {it['name']} ({it['ticker']}) — {a['region']} — "
                         + ("已便宜" if a["is_buy"] else f"需跌 {a['drop_to_cheap_pct']}%"),
                         expanded=a["is_buy"]):
            cols = st.columns(5)
            for col, k in zip(cols, ZONE_KEYS):
                cur = (k == "cheap")
                col.metric(ZONE_LABEL[k] + ("⭐" if cur else ""), money(z["zones"][k], d["currency"]))
            st.caption(f"現價 {money(d['price'], d['currency'])} ｜ 錨點 {z.get('anchor_kind')}={z.get('anchor')}"
                       + (f" ｜ 隱含{z.get('implied_kind','')} {z.get('implied_multiple')}" if z.get("implied_multiple") else ""))

st.divider()
st.caption("⚠ 本工具僅作資訊／教育用途,不構成投資建議。所有估值建立在 watchlist.yaml 可修改的假設上;免費數據為延遲報價。投資請自負風險。")

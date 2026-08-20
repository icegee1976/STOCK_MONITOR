# -*- coding: utf-8 -*-
"""AI 護國群山 — Streamlit 視覺化儀表板。

執行:
    pip install streamlit plotly
    streamlit run app.py

四個視圖:總覽 / 個股河流圖 / 投報率試算 / 便宜清單。
完全沿用 aimonitor 引擎,所以與 CLI 同一套估價邏輯。
"""

from __future__ import annotations

import json
import os
import sys

import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from aimonitor import providers
from aimonitor.valuation import compute_zones, ValuationError, ZONE_KEYS, ZONE_LABEL, format_revenue_check_line
from aimonitor.classify import analyze, REGION_ORDER
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

# Plotly 預設字型常不含 CJK → 中文標題/標籤會變亂碼(如「配置權重」→「比員催重」)。
# 指定跨平台中文字型堆疊修正。
PLOTLY_FONT = dict(family='"Microsoft JhengHei","PingFang TC","Noto Sans CJK TC",'
                          '"Microsoft YaHei","Heiti TC",sans-serif')

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
def _mtimes():
    """config/watchlist 的修改時間,當快取鍵 → 改檔即自動讓快取失效(不必重啟)。"""
    try:
        return tuple(os.path.getmtime(os.path.join(HERE, f))
                     for f in ("config.yaml", "watchlist.yaml"))
    except OSError:
        return (0.0, 0.0)


@st.cache_data(show_spinner=False)
def load_config(stamp):     # stamp(=mtime tuple)參與 hash:改設定檔即重載
    with open(os.path.join(HERE, "config.yaml"), "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    with open(os.path.join(HERE, "watchlist.yaml"), "r", encoding="utf-8") as f:
        wl = yaml.safe_load(f)
    stocks = wl.get("stocks", []) if isinstance(wl, dict) else wl
    return config, stocks


@st.cache_data(ttl=900, show_spinner=False)
def analyze_stock(ticker, _cfg, _config, stamp):
    """回傳可序列化的分析結果 dict。_cfg/_config 前綴底線避免被 hash;
    stamp(檔案 mtime)參與 hash → 改 watchlist 後該股分析即重算,不會回傳舊 zones。"""
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
                   "trailing_eps": data.trailing_eps,
                   "quality_warnings": data.quality_warnings}  # 015:接線供個股分頁顯示
    try:
        z = compute_zones(_cfg, data, _config)
        out["zones"] = z
        out["analysis"] = analyze(data.price, z["zones"], data.price_history,
                                  _config.get("roi_horizons_years", [1, 3, 5]))
    except ValuationError as e:
        out["error"] = f"估價失敗: {e}"
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def fx_usd_twd(fallback):
    """即時 USD/TWD,快取 1 小時 → 避免每次 widget 互動都同步打匯率 API 卡頓。"""
    return providers.usd_twd(fallback)


def build_all(stocks, config, market_filter=None):
    items = []
    prog = st.progress(0.0, text="抓取報價中…")
    flt = [s for s in stocks if (not market_filter or s["market"] == market_filter)]
    for i, s in enumerate(flt):
        items.append(analyze_stock(str(s["ticker"]), s, config, STAMP))
        prog.progress((i + 1) / len(flt), text=f"抓取 {s.get('name','')} …")
    prog.empty()
    return items


def money(x, ccy):
    if x is None:
        return "—"
    return f"{'NT$' if ccy=='TWD' else '$'}{x:,.2f}"


def md_money(x, ccy):
    """money() 的 markdown 安全版:把 `$` 跳脫為 `\\$`。

    Streamlit 的 st.markdown/st.info/... 會用 KaTeX 解析文字,若同一段字串內
    出現兩個以上未跳脫的 `$`(例如「投入 NT$xxx → 買進 … @ NT$xxx」兩個金額),
    會被誤判成行內數學公式,導致金額亂排、`**` 粗體失效。只在會同時嵌入
    ≥2 個 money() 結果的 markdown 呼叫點使用;單一 `$` 不會觸發 KaTeX,無需改用。
    """
    return money(x, ccy).replace("$", "\\$")


# 價位欄排序修正:Streamlit 點欄位是按字串 Unicode 排,不合「便宜→昂貴」邏輯。
# 在標籤前綴隱形序號 ①②③…⑥,讓點擊排序變成 大特價→超瘋狂 的正確順序。
_CIRCLED = "①②③④⑤⑥"


def region_sortable(region):
    try:
        return f"{_CIRCLED[REGION_ORDER.index(region)]}{region}"
    except (ValueError, IndexError):
        return region


def region_color(v):
    name = v[1:] if (v and v[0] in _CIRCLED) else v   # 去前綴序號再查色
    return f"color:white;background-color:{REGION_HEX.get(name, '#888')}"


# ---- 自訂清單:使用者輸入任意代號,可存檔本機 / 下載分享 / 匯入別人的 ----
CUSTOM_FILE = os.path.join(HERE, "custom_watchlist.yaml")


def load_custom():
    if not os.path.exists(CUSTOM_FILE):
        return []
    try:
        with open(CUSTOM_FILE, "r", encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        return d.get("stocks", []) if isinstance(d, dict) else (d or [])
    except Exception:
        return []


def save_custom(entries):
    with open(CUSTOM_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump({"stocks": entries}, f, allow_unicode=True, sort_keys=False)


def custom_yaml(entries):
    return yaml.safe_dump({"stocks": entries}, allow_unicode=True, sort_keys=False)


def pe_bands_from_fair(fair):
    r = [0.54, 0.69, 1.0, 1.15, 1.31]      # PDF 台積電河流圖幾何
    return {k: round(float(fair) * r[i], 2) for i, k in enumerate(ZONE_KEYS)}


# 黃仁勳 AI 生態五層(能源底座 → 應用頂層)+ 太空 + ETF。依 theme 標籤歸層。
LAYER_ORDER = [
    "⚡ L1 能源(底座)",
    "🏗️ L2 基礎設施(晶片/記憶體/光通訊/電源散熱)",
    "☁️ L3 雲端/資料中心",
    "🧠 L4 基礎模型",
    "🤖 L5 應用/代理(頂層)",
    "🛰️ 太空",
    "📦 ETF / 基金",
]


def ai_layer(cfg):
    th = " ".join(str(x) for x in (cfg.get("theme") or []))
    if "ETF" in th:
        return "📦 ETF / 基金"
    if any(k in th for k in ("太空", "火箭", "衛星", "登月", "航太", "Starlink")):
        return "🛰️ 太空"
    if "金字塔L1" in th or "能源" in th:
        return "⚡ L1 能源(底座)"
    if "金字塔L3" in th or "雲端" in th:
        return "☁️ L3 雲端/資料中心"
    if "金字塔L4" in th or "基礎模型" in th:
        return "🧠 L4 基礎模型"
    if "金字塔L5" in th or "AI應用" in th or "代理AI" in th:
        return "🤖 L5 應用/代理(頂層)"
    return "🏗️ L2 基礎設施(晶片/記憶體/光通訊/電源散熱)"


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
                      yaxis_range=[lo, hi], showlegend=False, font=PLOTLY_FONT,
                      title=f"{item['name']} 價格帶河流圖")
    st.plotly_chart(fig, width="stretch")


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
                      margin=dict(l=10, r=10, t=40, b=10), font=PLOTLY_FONT,
                      yaxis_title="年化報酬 %")
    st.plotly_chart(fig, width="stretch")


# --------------------------------------------------------------------------- #
#  主程式
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="成長股／ETF 監測器", layout="wide", page_icon="📈")
STAMP = _mtimes()                       # 全域快取鍵:config/watchlist 改動即失效
config, stocks = load_config(STAMP)
if "custom" not in st.session_state:        # 自訂清單(開機自動讀回上次存檔)
    st.session_state.custom = load_custom()

# Streamlit Cloud Secrets → 環境變數(providers 從 env 讀金鑰)。本機無 secrets 時略過。
try:
    for _k in ("FINNHUB_API_KEY", "FINMIND_TOKEN"):
        if _k in st.secrets and st.secrets[_k]:
            os.environ.setdefault(_k, str(st.secrets[_k]))
except Exception:
    pass

st.title("📈 美股／台股 AI＋太空 成長股／ETF 監測器")
st.caption("⚠ 資訊／教育用途,非投資建議;免費數據為日收盤價(EOD,非盤中即時)。")

with st.sidebar:
    st.header("設定")
    market_filter = st.selectbox("市場", ["全部", "TW 台股", "US 美股", "INTL 全球ETF"], index=0)
    mf = None if market_filter == "全部" else market_filter.split()[0]
    with st.expander("🔑 用自己的 API 金鑰(選填)"):
        st.caption("填了就用**你自己的**額度,不碰部署者的;只存在你這個瀏覽器 session,"
                   "別人看不到。填完按下方「🔄 重新抓取」即生效。")
        st.text_input("FinMind token(台股)", type="password", key="user_finmind",
                      help="finmindtrade.com 免費申請,台股額度 300→600/hr")
        st.text_input("Finnhub API key(美股)", type="password", key="user_finnhub",
                      help="finnhub.io 免費申請,雲端美股穩定報價(60/分)")
    _own_fh = bool((st.session_state.get("user_finnhub") or "").strip())
    _fh_on = _own_fh or bool(os.environ.get("FINNHUB_API_KEY")
                             or config.get("providers", {}).get("finnhub_api_key"))
    st.caption("美股來源:" + (
        ("🟢 **Finnhub**(你的金鑰)" if _own_fh else "🟢 **Finnhub** 即時報價") if _fh_on
        else "yfinance(雲端易限流;可在上方填 Finnhub 金鑰)"))
    if st.button("🔄 重新抓取 (清快取)"):
        st.cache_data.clear()
        providers_cache = os.path.join(HERE, ".cache")
        import shutil
        shutil.rmtree(providers_cache, ignore_errors=True)
        st.rerun()
    st.divider()
    st.caption("價格帶:大特價→便宜→合理→昂貴→瘋狂。進入便宜價(含)以下 = 提醒買進。")

# 使用者自帶金鑰 → 併入「本 session 專屬」config(複本,不動到快取的共用 config)。
# providers 採 config 優先於 env,故使用者金鑰會覆蓋部署者的;沒填則回退部署者 env 金鑰。
config = {**config, "providers": {
    **config.get("providers", {}),
    "finmind_token": (st.session_state.get("user_finmind") or "").strip()
    or config.get("providers", {}).get("finmind_token", ""),
    "finnhub_api_key": (st.session_state.get("user_finnhub") or "").strip()
    or config.get("providers", {}).get("finnhub_api_key", ""),
}}

tab_overview, tab_pyramid, tab_stock, tab_roi, tab_screen, tab_alloc, tab_custom = st.tabs(
    ["📊 總覽", "🏛️ AI 金字塔", "🔍 個股河流圖", "💰 投報率試算", "🟢 便宜清單",
     "📐 資產配置試算", "➕ 自訂清單"])

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
            "價位": region_sortable(a["region"]),
            "需跌%": 0.0 if a["is_buy"] else a["drop_to_cheap_pct"],
            "1年觸及%": a.get("prob_hit_cheap", {}).get(yr1),
            "隱含倍數": z.get("implied_multiple"),
            "錨點": f"{z.get('anchor_kind','')}={z.get('anchor','')}",
        })
    import pandas as pd
    df = pd.DataFrame(rows).sort_values("需跌%", na_position="last")

    st.dataframe(
        df.style.map(region_color, subset=["價位"]),
        width="stretch", height=640,
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

# ---------- AI 金字塔分層 ----------
with tab_pyramid:
    st.caption("黃仁勳的 AI 生態五層:**能源(底座)→ 基礎設施 → 雲端/資料中心 → 基礎模型 → 應用/代理(頂層)**,"
               "外加太空與 ETF。同一份清單,按產業階層重新分組。")
    items = build_all(stocks, config, mf)
    cfgmap = {str(s["ticker"]): s for s in stocks}
    groups = {}
    for it in items:
        groups.setdefault(ai_layer(cfgmap.get(it["ticker"], {})), []).append(it)
    import pandas as pd
    for lay in LAYER_ORDER:
        g = groups.get(lay)
        if not g:
            continue
        cheap_n = sum(1 for it in g if it["analysis"] and it["analysis"]["is_buy"])
        st.subheader(lay)
        st.caption(f"{len(g)} 檔" + (f" ・ 🟢 {cheap_n} 檔已便宜" if cheap_n else ""))
        rows = []
        for it in g:
            if it["error"]:
                rows.append({"標的": it["name"], "代號": it["ticker"], "現價": "—",
                             "價位": "錯誤", "距便宜": "—"})
                continue
            a, d = it["analysis"], it["data"]
            rows.append({"標的": it["name"], "代號": it["ticker"],
                         "現價": money(d["price"], d["currency"]), "價位": a["region"],
                         "距便宜": "已便宜" if a["is_buy"] else f"需跌 {a['drop_to_cheap_pct']}%"})
        st.dataframe(
            pd.DataFrame(rows).style.map(
                lambda v: f"color:white;background-color:{REGION_HEX.get(v, '#888')}"
                if v in REGION_HEX else "", subset=["價位"]),
            hide_index=True, width="stretch")

# ---------- 個股 ----------
# 012:抽成 fragment——selectbox 互動只重跑本分頁,不觸發全 script rerun,
# tabs 前端狀態(作用中分頁)不會被總覽的元素樹重建覆蓋掉,避免切標的跳回總覽。
@st.fragment
def _stock_tab_body():
    names = {f"{s.get('name','')} ({s['ticker']})": str(s["ticker"]) for s in stocks
             if (not mf or s["market"] == mf)}
    pick = st.selectbox("選擇標的", list(names.keys()))
    if pick:
        s_cfg = next(s for s in stocks if str(s["ticker"]) == names[pick])
        it = analyze_stock(names[pick], s_cfg, config, STAMP)
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
                rc = z.get("revenue_check")  # 018:月營收假設護欄(A+B 並示)
                if rc:
                    st.markdown(f"**營收軌跡**:{format_revenue_check_line(rc)}")
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
                for w in d.get("quality_warnings", []):        # 015:資料缺口/尾端過舊
                    st.warning(f"資料品質:{w}")
            if s_cfg.get("note"):
                st.caption(s_cfg["note"])

with tab_stock:
    _stock_tab_body()

# ---------- 投報率 ----------
# 012:同上,ROI 分頁互動最重(selectbox 切標的+金額/幣別 widget),原本每次
# 互動都全 script rerun 導致 tabs 跳回總覽;fragment 化後只重跑本分頁。
@st.fragment
def _roi_tab_body():
    names2 = {f"{s.get('name','')} ({s['ticker']})": str(s["ticker"]) for s in stocks}
    colp, cola, colc = st.columns([2, 1, 1])
    pick2 = colp.selectbox("標的", list(names2.keys()), key="roi_pick")
    amount = cola.number_input("投入金額", min_value=1000.0, value=300000.0, step=10000.0)
    cap_ccy = colc.selectbox("資金幣別", ["TWD", "USD"], index=0)
    if pick2:
        s_cfg = next(s for s in stocks if str(s["ticker"]) == names2[pick2])
        it = analyze_stock(names2[pick2], s_cfg, config, STAMP)
        if it["error"]:
            st.error(it["error"])
        else:
            data = providers.fetch(s_cfg, config.get("providers", {}),
                                   int(config.get("history_years", 5)), use_cache=True)
            stock_ccy = data.currency or ("TWD" if s_cfg["market"] == "TW" else "USD")
            if cap_ccy != stock_ccy:
                # 012 註:config 是模組層共享 dict,此處在 fragment 內變異(setdefault)。
                # fragment-only rerun 沿用上次全 rerun 的 config 物件,行為與搬移前一致,可接受。
                config.setdefault("fx", {})["USDTWD"] = fx_usd_twd(
                    config.get("fx", {}).get("USDTWD", 32.0))
            r = scenario_roi(s_cfg, data, it["zones"], amount, config, capital_currency=cap_ccy)
            if "error" in r:
                st.error(r["error"])
            else:
                if r["market"] == "TW":
                    _n = int(r["shares"]); sh = f"{_n:,} 股({_n // 1000} 張+{_n % 1000} 股)"
                else:
                    sh = f"{r['shares']:,.3f} 股"
                st.markdown(f"投入 **{md_money(r['spent'], r['stock_ccy'])}** → 買進 **{sh}** @ {md_money(r['price'], r['stock_ccy'])}")
                if r["fx_note"]:
                    st.warning(f"跨幣別:資金 {r['cap_ccy']} ≠ 標的 {r['stock_ccy']}(USD/TWD≈{r['fx_usdtwd']:.2f}),含匯率風險")
                cur = r["price"]
                _div_note = ""
                if r.get("us_div_withholding") is not None:
                    _div_note = (f"美股股利已按 {r['us_div_withholding']*100:.0f}% 預扣稅計算"
                                 f"(上方殖利率如有顯示為毛值)。")
                st.info(f"📌 **目標價 ＞ 現價（{money(cur, r['stock_ccy'])}）才是獲利情境。** "
                        f"低於現價的情境(如便宜價)代表股價『跌回』該價、賣出會虧 → 負報酬。\n\n"
                        f"情境=若未來股價走到各價格帶並賣出,持有 N 年的總報酬/年化(if-then 模型,非保證;含股利與費稅)。"
                        + (f"\n\n{_div_note}" if _div_note else ""))
                roi_rows = []
                for sc in r["scenarios"]:
                    diff = (sc["target_price"] - cur) / cur * 100 if cur else 0
                    row = {"情境": sc["label"],
                           "目標價": money(sc["target_price"], r["stock_ccy"]),
                           "vs現價": f"{diff:+.0f}% {'▲需漲' if diff >= 0 else '▼需跌(會虧)'}"}
                    for rr in sc["rows"]:
                        row[f"{rr['years']}年總報酬"] = f"{rr['total_return_pct']:+.0f}%"
                        row[f"{rr['years']}年年化"] = f"{rr['annualized_pct']:+.0f}%"
                    roi_rows.append(row)
                import pandas as pd
                dfr = pd.DataFrame(roi_rows)

                def _retcolor(v):                       # 正綠負紅,一眼看出賺賠
                    s = str(v).strip()
                    if s.startswith("-"):
                        return "color:#d2412f; font-weight:600"
                    if s.startswith("+") and not s.startswith("+0%"):
                        return "color:#1b7a3d"
                    return ""
                _cols = [c for c in dfr.columns if "報酬" in c or "年化" in c or c == "vs現價"]
                st.dataframe(dfr.style.map(_retcolor, subset=_cols),
                             hide_index=True, width="stretch")
                roi_bar(r)

with tab_roi:
    _roi_tab_body()

# ---------- 便宜清單 ----------
with tab_screen:
    st.markdown("依「距便宜價需跌幅」排序,越上面越接近便宜。")
    items = build_all(stocks, config, mf)
    valid = [it for it in items if it["analysis"]]
    skipped = [it for it in items if not it["analysis"]]
    if skipped:
        st.caption(f"⚠ {len(skipped)} 檔無法估價已略過:{'、'.join(it['name'] for it in skipped)}")
    # 次級鍵 gap_to_cheap_pct(已便宜者越負越前)讓同為「已便宜」的標的依跌破深度排序
    ranked = sorted(valid, key=lambda it: (not it["analysis"]["is_buy"],
                                           it["analysis"]["drop_to_cheap_pct"],
                                           it["analysis"]["gap_to_cheap_pct"]))
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

# ---------- 資產配置試算 ----------
with tab_alloc:
    st.markdown(
        "**用途:把資金按比例分配到多檔標的,看整體投組長相。** "
        "建議挑 **2 檔以上**(例:VWRA 全球 50% ＋ 0050 台股 30% ＋ 0056 高息 20%),"
        "在下方表格調權重 → 立刻看到每檔配置金額、估算股數、目前價位,以及投組層級的加權殖利率與集中度。")
    opts = {f"{s.get('name','')} ({s['ticker']})": str(s["ticker"]) for s in stocks}
    ca, cb = st.columns([3, 2])
    picks = ca.multiselect("選擇持有標的(可多選)", list(opts.keys()), key="alloc_pick")
    total = cb.number_input("總投入資金", min_value=1000.0, value=1000000.0, step=10000.0, key="alloc_total")
    acc = cb.selectbox("資金幣別", ["TWD", "USD"], index=0, key="alloc_ccy")
    if not picks:
        st.info("請先選至少一檔。例:VWRA(全球)+ 0050(台股)+ 0056(高息)組核心配置。")
    else:
        import pandas as pd
        eqw = round(100.0 / len(picks), 2)
        st.caption("可直接編輯權重(會自動正規化為 100%):")
        edited = st.data_editor(
            pd.DataFrame({"標的": picks, "權重%": [eqw] * len(picks)}),
            hide_index=True, width="stretch", disabled=["標的"], key="alloc_w",
            column_config={"權重%": st.column_config.NumberColumn(min_value=0.0, format="%.1f")})
        raw_sum = float(edited["權重%"].sum())
        if raw_sum <= 0:
            st.warning("權重總和為 0,請至少給一檔正權重。")
            st.stop()
        st.caption(f"目前權重總和 {raw_sum:.0f}% → 自動正規化為 100%")
        wsum = raw_sum
        fx = fx_usd_twd(config.get("fx", {}).get("USDTWD", 32.0))
        rows, blended_yield, cheap_ct, maxw = [], 0.0, 0, 0.0
        for _, rr in edited.iterrows():
            nm = rr["標的"]; tk = opts[nm]; w = float(rr["權重%"]) / wsum
            maxw = max(maxw, w)
            cfg = next(s for s in stocks if str(s["ticker"]) == tk)
            it = analyze_stock(tk, cfg, config, STAMP)
            alloc = total * w
            if it["error"]:
                rows.append({"標的": nm, "權重": f"{w*100:.1f}%", "配置金額": money(alloc, acc),
                             "估算股數": "—", "價位": "錯誤", "殖利率": "—"})
                continue
            d, a = it["data"], it["analysis"]
            sccy = d["currency"]
            alloc_s = (alloc if acc == sccy else
                       alloc / fx if (acc == "TWD" and sccy == "USD") else
                       alloc * fx if (acc == "USD" and sccy == "TWD") else alloc)
            shares = (alloc_s / d["price"]) if d["price"] else 0
            shares_disp = (f"{int(shares):,} 股({int(shares)//1000}張)"
                           if cfg["market"] == "TW" else f"{shares:,.2f}")
            dy = d.get("dividend_yield") or 0
            blended_yield += w * dy
            cheap_ct += 1 if a["is_buy"] else 0
            rows.append({"標的": nm, "權重": f"{w*100:.1f}%", "配置金額": money(alloc, acc),
                         "估算股數": shares_disp, "價位": a["region"],
                         "殖利率": f"{dy*100:.1f}%" if dy else "—"})
        dfa = pd.DataFrame(rows)
        st.dataframe(
            dfa.style.map(lambda v: f"color:white;background-color:{REGION_HEX.get(v,'#888')}"
                          if v in REGION_HEX else "", subset=["價位"]),
            hide_index=True, width="stretch")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("總投入", money(total, acc))
        m2.metric("加權現金殖利率", f"{blended_yield*100:.2f}%",
                  help="各標的現金殖利率以權重加權平均(累積型 ETF 約 0%)")
        m3.metric("便宜標的數", f"{cheap_ct} / {len(picks)}",
                  help="你選的標的中,目前落在『便宜價(含)以下』的檔數")
        m4.metric("最大單一權重", f"{maxw*100:.0f}%",
                  help="權重最高的單一標的占比;越高代表越集中、個股風險越大")
        if len(picks) == 1:
            st.info("ℹ️ 只選了 1 檔 = 集中投資單一標的(權重必為 100%)。"
                    "資產配置試算在「**2 檔以上**」要看分散程度與整體狀態時最有用。")
        elif maxw > 0.4:
            st.warning(f"⚠ 集中度偏高:單一標的權重達 {maxw*100:.0f}%。適度分散可降個股風險。")
        if HAS_PLOTLY:
            fig = go.Figure(go.Pie(labels=[r["標的"] for r in rows],
                                   values=[float(r["權重"].rstrip('%')) for r in rows], hole=0.45))
            fig.update_layout(height=360, margin=dict(l=10, r=10, t=40, b=10),
                              title="配置權重(%)", font=PLOTLY_FONT)
            st.plotly_chart(fig, width="stretch")
        st.caption("配置金額＝總資金×正規化權重;估算股數＝配置金額(換匯後)÷現價之概估,未扣手續費。"
                   "加權殖利率為各檔現金殖利率按權重加權。跨幣別已用即時匯率換算。")

# ---------- 自訂清單 ----------
with tab_custom:
    st.markdown("**輸入任意台股／美股／ETF 代號**即時查詢價格帶,可**存檔本機、下載分享、匯入別人的清單**。"
                "估值預設 `price_band`(股價分布,免任何假設);會賺錢的股可改 `pe_band` 並填預估 EPS。")

    def _analyze_custom(cfg):       # 用 cfg 指紋當快取鍵,避免與主清單同代號相撞
        key = ("custom", json.dumps(cfg.get("valuation"), sort_keys=True, default=str))
        return analyze_stock(str(cfg["ticker"]), cfg, config, key)

    with st.container(border=True):
        st.markdown("**➕ 加入標的**")
        a1, a2, a3 = st.columns([2, 2, 3])
        in_tk = a1.text_input("代號", key="cu_tk", placeholder="2412 / TSLA / QQQ / VWRA.L")
        in_mkt = a2.selectbox("市場", ["TW 台股", "US 美股", "INTL 全球/美股ETF"], key="cu_mkt")
        in_nm = a3.text_input("名稱(可留空)", key="cu_nm")
        in_method = st.selectbox(
            "估價法",
            ["price_band — 股價分布(免設定,推薦)",
             "pe_band — 本益比河流圖(需填預估 EPS)",
             "yield_band — 殖利率河流圖(高股息 ETF,自動)"], key="cu_method")
        eps_v, fair_v = None, None
        if in_method.startswith("pe_band"):
            e1, e2 = st.columns(2)
            eps_v = e1.number_input("預估 forward EPS(每股盈餘)", value=0.0, step=0.1, key="cu_eps")
            fair_v = e2.number_input("合理本益比 fair P/E", value=20.0, step=1.0, key="cu_fair")
        if st.button("➕ 加入並查詢", key="cu_add"):
            tk = in_tk.strip().upper()
            if not tk:
                st.warning("請先輸入代號。")
            else:
                mkt = in_mkt.split()[0]
                meth = in_method.split()[0]
                if meth == "pe_band":
                    val = {"method": "pe_band", "forward_eps": (eps_v or None),
                           "target_year": 2027, "pe_bands": pe_bands_from_fair(fair_v or 20)}
                elif meth == "yield_band":
                    val = {"method": "yield_band", "yield_bands": "auto"}
                else:
                    val = {"method": "price_band", "lookback_years": int(config.get("history_years", 5))}
                cfg = {"ticker": tk, "market": mkt, "name": (in_nm.strip() or tk),
                       "theme": ["自訂"], "valuation": val}
                if any(str(s["ticker"]).upper() == tk and s["market"] == mkt for s in st.session_state.custom):
                    st.warning(f"{tk} 已在自訂清單中。")
                else:
                    with st.spinner(f"查詢 {tk} …"):
                        data = providers.fetch(cfg, config.get("providers", {}),
                                               int(config.get("history_years", 5)), use_cache=False)
                    if not data.ok():
                        st.error(f"加入失敗:{data.error or '抓不到資料,請確認代號與市場是否正確'}")
                    else:
                        st.session_state.custom.append(cfg)
                        st.success(f"已加入 {cfg['name']} ({tk})")
                        st.rerun()

    cust = st.session_state.custom
    if not cust:
        st.info("尚無自訂標的。用上方表單加入,或在下方匯入別人分享的清單。")
    else:
        import pandas as pd
        items = [_analyze_custom(c) for c in cust]
        rows = []
        for it in items:
            if it["error"]:
                rows.append({"標的": it["name"], "代號": it["ticker"], "市場": it["market"],
                             "現價": "—", "價位": "錯誤", "距便宜": it["error"][:24]})
                continue
            a, d = it["analysis"], it["data"]
            rows.append({"標的": it["name"], "代號": it["ticker"], "市場": it["market"],
                         "現價": money(d["price"], d["currency"]),
                         "價位": region_sortable(a["region"]),
                         "距便宜": "已便宜" if a["is_buy"] else f"需跌 {a['drop_to_cheap_pct']}%"})
        st.dataframe(pd.DataFrame(rows).style.map(region_color, subset=["價位"]),
                     hide_index=True, width="stretch")
        labels = [f"{c.get('name', '')} ({c['ticker']})" for c in cust]
        rm = st.multiselect("移除標的", labels, key="cu_rm")
        if rm and st.button("🗑 移除選取", key="cu_rmbtn"):
            st.session_state.custom = [c for c, l in zip(cust, labels) if l not in set(rm)]
            st.rerun()
        with st.expander("🔍 深入看某一檔(河流圖 + 投報率)"):
            pick = st.selectbox("選擇", labels, key="cu_pick")
            pc = next((c for c, l in zip(cust, labels) if l == pick), None)
            if pc:
                it = _analyze_custom(pc)
                if it["error"]:
                    st.error(it["error"])
                else:
                    river_chart(it)
                    g1, g2 = st.columns(2)
                    amt = g1.number_input("投入金額試算", min_value=1000.0, value=300000.0, step=10000.0, key="cu_amt")
                    ccy = g2.selectbox("資金幣別", ["TWD", "USD"], key="cu_ccy")
                    data = providers.fetch(pc, config.get("providers", {}), int(config.get("history_years", 5)))
                    sccy = data.currency or ("TWD" if pc["market"] == "TW" else "USD")
                    if ccy != sccy:
                        config.setdefault("fx", {})["USDTWD"] = fx_usd_twd(config.get("fx", {}).get("USDTWD", 32.0))
                    r = scenario_roi(pc, data, it["zones"], amt, config, capital_currency=ccy)
                    if "error" not in r and HAS_PLOTLY:
                        roi_bar(r)

    st.divider()
    s1, s2, s3 = st.columns(3)
    if s1.button("💾 儲存到本機", key="cu_save", help="存到 custom_watchlist.yaml,下次開啟自動帶回"):
        save_custom(st.session_state.custom)
        s1.success("已儲存")
    s2.download_button("📤 下載分享檔", data=custom_yaml(st.session_state.custom),
                       file_name="my_watchlist.yaml", mime="text/yaml", key="cu_dl",
                       help="把這個檔傳給別人,他在此匯入就能看到一樣的清單")
    up = s3.file_uploader("📥 匯入分享檔 (.yaml)", type=["yaml", "yml"], key="cu_up")
    if up is not None:
        try:
            d = yaml.safe_load(up.read().decode("utf-8")) or {}
            imported = d.get("stocks", []) if isinstance(d, dict) else d
            have = {(str(c["ticker"]).upper(), c["market"]) for c in st.session_state.custom}
            n = 0
            for c in imported:
                if isinstance(c, dict) and c.get("ticker") and c.get("market") \
                        and (str(c["ticker"]).upper(), c["market"]) not in have:
                    st.session_state.custom.append(c)
                    have.add((str(c["ticker"]).upper(), c["market"]))
                    n += 1
            if n:
                st.success(f"已匯入 {n} 檔(自動略過重複)。記得按「💾 儲存到本機」保留。")
                st.rerun()
            else:
                st.info("沒有新標的可匯入(可能都已存在)。")
        except Exception as e:
            st.error(f"匯入失敗:{e}")

st.divider()
st.caption("⚠ 本工具僅作資訊／教育用途,不構成投資建議。所有估值建立在 watchlist.yaml 可修改的假設上;免費數據為日收盤價(EOD,非盤中即時)。投資請自負風險。")

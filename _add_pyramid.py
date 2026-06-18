# -*- coding: utf-8 -*-
"""把『AI 金字塔 + 太空』新標的附加到 watchlist.yaml(用 yfinance forward 共識設定)。
pe_band 的五價格帶 = fair × 河流圖幾何;ps/price_band 直接寫。可重複執行(會先去重)。"""
import os, yaml
HERE = os.path.dirname(os.path.abspath(__file__))
R = {"standard": [0.54, 0.69, 1.0, 1.15, 1.31], "wide": [0.45, 0.68, 1.0, 1.45, 2.1]}
ZK = ["super_bargain", "cheap", "fair", "expensive", "euphoria"]

def pe(fair, shape):
    r = R[shape]
    return {k: round(fair * r[i], 2) for i, k in enumerate(ZK)}

# (ticker, market, name, theme, method, params, note)
PE = [  # ticker, name, theme, fwdEps, fair, shape, note
 ("NOC","Northrop Grumman",["太空","國防","航太"],30.14,18,"standard","航太國防+太空探測長約。yfinance fwdEps,請校正。"),
 ("LMT","Lockheed Martin",["太空","國防","航太"],32.09,17,"standard","軍工龍頭,政府太空合約。"),
 ("VST","Vistra",["能源","AI電力","金字塔L1"],10.99,17,"wide","AI 金字塔底座:電力。資料中心電力需求受惠。"),
 ("CEG","Constellation Energy",["能源","核電","AI電力","金字塔L1"],13.55,20,"wide","全美最大核電,簽 AI 資料中心供電長約。"),
 ("GEV","GE Vernova",["能源","電網","燃氣","金字塔L1"],24.51,38,"wide","電網+燃氣+核能設備,AI 用電擴張直接受惠。高成長。"),
 ("MSFT","Microsoft",["雲端","Azure","OpenAI","金字塔L3","金字塔L4"],19.35,28,"standard","Azure 雲算力 + OpenAI 最大股東(L3+L4)。"),
 ("GOOGL","Alphabet",["雲端","Gemini","金字塔L3","金字塔L4"],14.48,24,"standard","GCP 雲 + Gemini 基礎模型(L3+L4)。"),
 ("AMZN","Amazon",["雲端","AWS","金字塔L3"],9.86,30,"wide","AWS 雲算力龍頭(L3)。"),
 ("META","Meta Platforms",["基礎模型","Llama","金字塔L4"],36.25,24,"standard","Llama 開源模型 + 自建 AI 算力(L4)。"),
 ("PLTR","Palantir",["AI應用","代理AI","金字塔L5"],2.08,55,"wide","代理式 AI 應用龍頭(L5)。極高本益比,注意瘋狂價。"),
]
PS = [  # ticker, name, theme, fwd_rev, shares, ps_bands, note
 ("SPCX","SpaceX",["太空","火箭","Starlink","龍頭"],2.2e10,1.311e10,
  {"super_bargain":18,"cheap":27,"fair":40,"expensive":58,"euphoria":84},
  "⚠2026/6/12 IPO(估值1.77兆,史上最大)。營收/股數為估計(Starlink+發射~$22B、~131億股),請校正。現價隱含P/S極高=定價完美。"),
]
PB = [  # ticker, market, name, theme, lookback, note
 ("OKLO","US","Oklo (核能SMR)",["能源","核能SMR","金字塔L1"],3,"小型模組化核電,Altman 投資。尚未量產營收→用股價分布。極投機。"),
 ("CRWV","US","CoreWeave",["雲端","GPU算力","金字塔L3"],3,"GPU as a Service 純算力出租(L3)。2025 IPO,燒錢高成長→股價分布。"),
 ("NBIS","US","Nebius",["雲端","GPU算力","金字塔L3"],3,"歐洲 GPU 雲(L3)。獲利不穩→股價分布。"),
 ("UFO","US","Procure Space ETF",["ETF","太空","純太空"],5,"純度較高的全球太空 ETF。"),
 ("XOVR","US","ERShares (含SpaceX)",["ETF","太空","SpaceX曝險"],5,"少數可直接曝險 SpaceX 的 ETF。"),
 ("6285","TW","啟碁",["太空","衛星地面設備","網通"],5,"衛星地面接收+網通設備。"),
 ("2313","TW","華通",["太空","PCB","星鏈板"],5,"星鏈衛星板主要 PCB 供應商。"),
 ("6271","TW","同欣電",["太空","衛星零組件"],5,"衛星零組件、影像感測封裝。"),
 ("2314","TW","台揚",["太空","衛星通訊"],5,"衛星通訊設備。獲利不穩→股價分布。"),
 ("3491","TW","昇達科",["太空","衛星通訊元件"],5,"衛星通訊元件佔比高。本益比極高→用股價分布。"),
 ("00910","TW","第一金太空衛星",["ETF","太空","衛星"],4,"聚焦全球衛星設備與太空技術 ETF。"),
 ("00965","TW","元大航太防衛科技",["ETF","太空","航太國防"],3,"涵蓋航太、國防與衛星 ETF。"),
]

def theme_str(t): return "[" + ", ".join(t) + "]"

def block_pe(tk, name, theme, eps, fair, shape, note):
    b = pe(fair, shape)
    L = [f'  - ticker: "{tk}"', "    market: US", f"    name: {name}",
         f"    theme: {theme_str(theme)}", "    valuation:", "      method: pe_band",
         f"      forward_eps: {eps}        # yfinance 共識 (fair P/E {fair})",
         "      target_year: 2027",
         f"      pe_bands: {{ super_bargain: {b['super_bargain']:g}, cheap: {b['cheap']:g}, "
         f"fair: {b['fair']:g}, expensive: {b['expensive']:g}, euphoria: {b['euphoria']:g} }}  # {shape}",
         f'    note: "{note}"', ""]
    return "\n".join(L)

def block_ps(tk, name, theme, rev, shares, psb, note):
    L = [f'  - ticker: "{tk}"', "    market: US", f"    name: {name}",
         f"    theme: {theme_str(theme)}", "    valuation:", "      method: ps_band",
         f"      forward_revenue: {rev:.3e}", f"      shares: {shares:.3e}", "      target_year: 2027",
         f"      ps_bands: {{ super_bargain: {psb['super_bargain']:g}, cheap: {psb['cheap']:g}, "
         f"fair: {psb['fair']:g}, expensive: {psb['expensive']:g}, euphoria: {psb['euphoria']:g} }}  # wide",
         f'    note: "{note}"', ""]
    return "\n".join(L)

def block_pb(tk, mkt, name, theme, lb, note):
    L = [f'  - ticker: "{tk}"', f"    market: {mkt}", f"    name: {name}",
         f"    theme: {theme_str(theme)}", "    valuation:", "      method: price_band",
         f"      lookback_years: {lb}", f'    note: "{note}"', ""]
    return "\n".join(L)

wl = yaml.safe_load(open(os.path.join(HERE, "watchlist.yaml"), encoding="utf-8"))
existing = {str(s["ticker"]).upper() for s in wl["stocks"]}

parts = ["", "# ============================================================",
         "#  AI 金字塔(黃仁勳五層)擴充 + 太空(含 SpaceX IPO)",
         "#  L1能源 / L3雲端資料中心 / L4基礎模型 / L5應用代理;太空個股+供應鏈+ETF",
         "#  pe_band: yfinance forward 共識;ps/price_band 見各檔。皆可改。",
         "# ============================================================", ""]
added = []
for tk, name, theme, eps, fair, shape, note in PE:
    if tk.upper() in existing: continue
    parts.append(block_pe(tk, name, theme, eps, fair, shape, note)); added.append(tk)
for tk, name, theme, rev, shares, psb, note in PS:
    if tk.upper() in existing: continue
    parts.append(block_ps(tk, name, theme, rev, shares, psb, note)); added.append(tk)
for tk, mkt, name, theme, lb, note in PB:
    if tk.upper() in existing: continue
    parts.append(block_pb(tk, mkt, name, theme, lb, note)); added.append(tk)

with open(os.path.join(HERE, "watchlist.yaml"), "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
chk = yaml.safe_load(open(os.path.join(HERE, "watchlist.yaml"), encoding="utf-8"))
print("已新增:", added)
print("watchlist 總檔數:", len(chk["stocks"]))
PY = None

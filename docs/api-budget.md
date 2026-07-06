# API 呼叫與快取總帳(工單 004 唯讀審計,2026-07-06)

對照額度:**FinMind 300 次/hr**(免 token;`FINMIND_TOKEN` → 600)、**Finnhub 60 次/min**、
yfinance 無公開額度(雲端機房 IP 易 429)、exchangerate-api(匯率,免金鑰)。
審計基準:watchlist@HEAD = **57 檔**(TW 25:pe_band 6/price_band 14/yield_band 5;
US 27:pe_band 16/ps_band 6/price_band 固定帶 5;INTL 5:price_band)。

## 1. 每檔一次抓取的成本(檔案快取 miss 時)

| 市場/方法 | FinMind | Finnhub(有金鑰) | Yahoo(yfinance) | 備註 |
|---|---|---|---|---|
| TW pe_band | 2(Price+PER) | — | — | PER 供河流圖/隱含PE |
| TW price_band | 1(Price) | — | — | |
| TW yield_band | 2(Price+Dividend) | — | — | |
| US(有 FINNHUB_API_KEY) | — | 2(/quote+/metric) | ≤2(history+info,best-effort,失敗略過) | Finnhub 失敗→整檔退回 yfinance |
| US(無金鑰) | — | — | ~2(history 含 retry×3 + info) | 雲端易 429 |
| INTL(.L) | — | — | ~2 | Finnhub 免費不支援 LSE |
| 匯率 usd_twd | — | — | — | exchangerate-api 1 次 |

## 2. 每指令/場景的呼叫數上限(全部 cache miss 的最壞情況)

| 場景 | FinMind | Finnhub | Yahoo | 佔額度 |
|---|---|---|---|---|
| `report --ticker 2330`(單檔 pe_band) | 2 | 0 | 0 | 0.7% of 300/hr |
| `report` / `screen` 全清單(57 檔) | **36** | 54 | ~64 | FinMind 12%/hr;**Finnhub 54 接近 60/min**(序列執行有自然間隔,實務約 30–60s 內發出,邊緣) |
| Dashboard 冷載入(總覽全清單) | 36 | 54 | ~64 | 同上;之後 15 分鐘內 rerun 幾乎 0(見 §3) |
| `roi <ticker> <amt>`(跨幣別) | 1–2 | 0–2 | 0–2 | +1 次匯率 |
| **`watch --interval 300`(預設,已修工單 008)** | **≤36/15min ≈ 144/hr**(use_cache=True,同快取週期內多輪共用資料) | 54/首輪,之後多輪共用 | ~64/首輪,之後多輪共用 | ✅ 已修;15 分鐘快取吸收輪詢,不再 402 |

## 3. 快取層(現狀確認)

1. **檔案快取 `.cache/`**(providers 層):TTL = `config.providers.cache_minutes` = **15 分鐘**;
   只在 `data.ok()` 時寫入;抓取失敗時退回**過期快取保命**(`_load_cache(max_age=None)`,
   標示「(過期快取)」)。CLI 與 dashboard 共用。
2. **`@st.cache_data`(app.py 層)**:`analyze_stock` ttl=900(15 分),鍵含 ticker +
   config/watchlist 的 mtime stamp(改檔即失效);`load_config` 以 mtime 為鍵;
   `fx_usd_twd` ttl=3600。側邊欄「手動重新整理」呼叫 `st.cache_data.clear()`
   後,下一次 build_all 仍會先命中檔案快取(<15 分),**不會**直接引爆 36 次 FinMind——雙層設計正確。
3. **watch 例外**:`cmd_watch` 用 `make_fetch_fn(config, use_cache=False)` **繞過檔案快取讀取**
   (仍會寫入),所以每輪都是全清單 live 抓取。

## 4. 發現(只記錄,修復需另開工單)

- **F-1(高)`watch` 預設參數自我打爆額度 —— 已修(工單 008)**:原本
  `--interval 300`(預設值)× 36 FinMind/輪 = 432/hr > 300/hr 免費額度,**用預設值跑
  約 40 分鐘就會 402**,之後整個 IP 的 FinMind 當小時額度歸零(連 dashboard 也一起死)。
  已改為 (b):`cmd_watch` 的 `make_fetch_fn(config, use_cache=True)`,讓 15 分鐘檔案
  快取吸收輪詢(interval<900 時多輪共用同一批資料),額度上限降到 ≤144/hr,
  `--interval 300` 預設值即安全。
- **F-2(中)Finnhub 突發接近上限**:全清單 54 次(27 檔×2)理論上可在 1 分鐘內發出,
  貼著 60/min。序列 HTTP 延遲目前是唯一緩衝;若未來並行化抓取,會直接超限。
- **F-3(低)`watch` 對 INTL/US 的 429 無輪間退避**:單次抓取內有 `_retry` 指數退避,
  但輪與輪之間無 backoff;yfinance 被 429 時每輪照打(有過期快取保命,不致崩,浪費呼叫)。
- **金鑰確認(乾淨)**:FinMind/Finnhub 金鑰只經 `config dict(側邊欄 session_state)>
  環境變數/Streamlit secrets` 讀取(providers.py L358–366、app.py L275–279、L318–319),
  無落地寫檔、無 commit;config.yaml 兩欄位皆空字串。

## 5. `watch` 最小安全 interval 建議

| 條件 | FinMind 預算 | 最大輪/hr | 最小 interval | 建議值 |
|---|---|---|---|---|
| 免 token(300/hr),舊行為 use_cache=False(已修) | 36/輪 | 8.3 | ≥434s | ~~≥600s~~(已不適用) |
| 有 token(600/hr),舊行為(已修) | 36/輪 | 16.6 | ≥217s | ~~≥450s~~(已不適用) |
| **use_cache=True(已修,工單 008,現行行為)** | ≤36/15min≈144/hr | — | 任意 | 300s(預設值)即安全 |

> 本審計 0 次 API 呼叫(純讀 code + 本地 watchlist 統計)。

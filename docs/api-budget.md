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
| `report` / `screen` 全清單(57 檔) | **36**(cache miss 當次;§1 的每檔成本表,worst case) | 54 | ~64 | FinMind 12%/hr;**Finnhub 54 接近 60/min**(序列執行有自然間隔,實務約 30–60s 內發出,邊緣) |
| Dashboard 冷載入(總覽全清單) | 36(cache miss 當次) | 54 | ~64 | 同上;**happy path**(抓取成功、快取正常寫入)下 TW 之後在同一交易日內幾乎 0(EOD-aware,工單 013,見 §3),US/INTL 仍是 15 分鐘內幾乎 0。**失敗模式**:抓取失敗時 `_save_cache` 不寫入(見 providers.py `if data.ok(): _save_cache(...)`),下一輪仍是 cache miss、仍要打滿 36/54/~64——EOD-aware 只降低「成功後的重複讀取」,不改善「持續失敗時每輪都重抓」的情況 |
| `roi <ticker> <amt>`(跨幣別) | 1–2 | 0–2 | 0–2 | +1 次匯率 |
| **`watch --interval 300`(預設,已修工單 008;TW 已加碼工單 013)** | **happy path:TW ≈36/天**(EOD-aware,一天內跨過交易日邊界才重抓,與 interval 無關,工單 013)。**worst case(FinMind 402/斷線等持續失敗期間)**:失敗不寫快取 → 每輪仍是 36 FinMind(全清單),回到工單 008 修復前的量級,`--interval 300` 下仍是 **432/hr**,需仰賴 `_retry` 指數退避與過期快取保命撐過去,不是「怎樣都安全」。US/INTL 抓取量不算在此欄(FinMind 專用) | 54/首輪,之後多輪共用(15分 TTL 不變) | ~64/首輪,之後多輪共用(15分 TTL 不變) | happy path 下 TW 額度需求遠低於工單 008 時代;持續失敗時退化回等量 |

## 3. 快取層(現狀確認)

1. **檔案快取 `.cache/`**(providers 層):TTL = `config.providers.cache_minutes` = **15 分鐘**
   (US/INTL 不變);**TW 例外(工單 013,EOD-aware)**:改用 `_tw_cache_fresh` 判斷——
   台股是日收盤(EOD)資料,一天只變一次,只要「上次抓取之後」沒有跨過任何一個
   週一~週五台北 `providers.tw_eod_hour`:00(預設 18:00,對應 FinMind 傍晚更新,
   可由 `providers_cfg` 覆寫、不動 `config.yaml`)的資料更新邊界,快取就視為新鮮
   (週末沒有邊界、國定假日不查行事曆一律視為交易日,寧可多抓一次)。仍保留
   15 分鐘**安全地板**(`now - fetched_at < floor_minutes` 一律新鮮,防時鐘/時區
   誤差造成重抓風暴)。只在 `data.ok()` 時寫入;抓取失敗時退回**過期快取保命**
   (`_load_cache(max_age=None)`,標示「(過期快取)」,TW/US 皆同、行為未變)。
   CLI 與 dashboard 共用。
2. **`@st.cache_data`(app.py 層)**:`analyze_stock` ttl=900(15 分),鍵含 ticker +
   config/watchlist 的 mtime stamp(改檔即失效);`load_config` 以 mtime 為鍵;
   `fx_usd_twd` ttl=3600。側邊欄「🔄 重新抓取(清快取)」按鈕(app.py:314–319)
   **不是**只清 `st.cache_data`——它同時 `shutil.rmtree(.cache/)` 把 providers 層的
   檔案快取整個刪掉再 `st.rerun()`,所以按下去就是**全清單冷載入**:57 檔全部
   cache miss,一次打滿 §1/§2 worst-case 的量(36 FinMind + 54 Finnhub + ~64
   Yahoo),不是「先命中檔案快取」的溫和路徑。這是使用者主動要求強制刷新的
   預期行為,只是文件先前的描述(僅清 `st.cache_data`、仍會命中檔案快取)與
   實際程式碼不符,此處更正。
3. **watch 例外**:`cmd_watch` 用 `make_fetch_fn(config, use_cache=True)`(工單 008
   已修;monitor.py:160)**讀取仍會命中檔案快取**,不像本節先前版本描述的
   `use_cache=False`——15 分鐘 TTL(US/INTL)/EOD-aware(TW,工單 013)吸收
   輪詢,同快取週期內多輪共用同一批資料,不是每輪都全清單 live 抓取。
4. **快取鍵的已知限制(未解,留待另開工單)**:檔案快取鍵是 `market_ticker`
   (`_cache_path`),**不含** `history_years` 或 `valuation.method`。若使用者只是
   改了 `watchlist.yaml` 裡某檔的估價假設(例如 pe_band 门檻、method 從
   `price_band` 換成 `pe_band`)而沒有換 ticker,舊快取仍會被視為「新鮮」沿用,
   直到自然過期(US/INTL 最長 15 分;TW EOD-aware 最長約 72 小時,週五收盤後
   改假設要等到週一收盤才會反映)。要立即看到新假設生效,請按側邊欄
   「🔄 重新抓取(清快取)」或手動刪除 `.cache/`。真正的解法(快取鍵納入
   假設指紋,或另建本地歷史資料庫)留待未來工單,不在本工單範圍。

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
| use_cache=True,固定 15 分 TTL(工單 008,~~已被 013 取代~~) | ~~≤36/15min≈144/hr~~ | — | 任意 | ~~300s(預設值)即安全~~ |
| **TW EOD-aware(工單 013,現行行為,happy path)** | **≈36/天**(只在跨過交易日 18:00 更新邊界時重抓,與 interval 幾乎無關;若持續抓取失敗則退化回 §2 所述的「每輪 36」worst case,不受 EOD-aware 保護) | — | 任意 | 300s(預設值)在 happy path 下遠低於風險線;確切降幅比例需視實際使用時數與輪詢頻率而定,未量化 |

> 本審計 0 次 API 呼叫(純讀 code + 本地 watchlist 統計)。工單 013(2026-08)在既有審計基礎上
> 補充 TW EOD-aware 快取變更;US/INTL 15 分固定 TTL 段落未動。

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
| **TW 官方備援**(僅 FinMind price 抓取失敗時觸發,工單 017;call 數模型於 reviewer 修正包 G3 更新) | 0(FinMind 健康時不觸發) | — | — | 觸發時:**成功時各最多 1 次/EOD 邊界**(皆免金鑰,不佔 FinMind/Finnhub 額度)——兩者都是「一次呼叫回全市場當日收盤」,process 內 memo 共用,同一輪(screen/watch 跑多檔)全部台股標的共用這次成功結果,不隨檔數增加,跨過工單 013 的 EOD 邊界才重打;找到代號才算「觸發成功」,兩端點都沒有該代號時仍算成功 memo(不重打)。**失敗時**(端點本身也掛、逾時、回應非預期格式)有 **15 分鐘負向 memo 冷卻**(G3b):同一輪內同一端點第一次失敗後,15 分鐘內其他檔位直接短路不重打,15 分鐘後才會再嘗試 1 次;每次真正探測都含 `_retry` 三次重試(`timeout=10`,G3c),把「全端點都掛、逐檔重打」的最壞情境從單輪可疊加到近一小時,壓到單輪只有第一檔付出真正探測成本 |

## 2. 每指令/場景的呼叫數上限(全部 cache miss 的最壞情況)

| 場景 | FinMind | Finnhub | Yahoo | 佔額度 |
|---|---|---|---|---|
| `report --ticker 2330`(單檔 pe_band) | 2 | 0 | 0 | 0.7% of 300/hr |
| `report` / `screen` 全清單(57 檔) | **36**(cache miss 當次;§1 的每檔成本表,worst case) | 54 | ~64 | FinMind 12%/hr;**Finnhub 54 接近 60/min**(序列執行有自然間隔,實務約 30–60s 內發出,邊緣) |
| Dashboard 冷載入(總覽全清單) | 36(cache miss 當次) | 54 | ~64 | 同上;**happy path**(抓取成功、快取正常寫入)下 TW 之後在同一交易日內幾乎 0(EOD-aware,工單 013,見 §3),US/INTL 仍是 15 分鐘內幾乎 0。**失敗模式**:抓取失敗時 `_save_cache` 不寫入(見 providers.py `if data.ok(): _save_cache(...)`),下一輪仍是 cache miss、仍要打滿 36/54/~64——EOD-aware 只降低「成功後的重複讀取」,不改善「持續失敗時每輪都重抓」的情況 |
| `roi <ticker> <amt>`(跨幣別) | 1–2 | 0–2 | 0–2 | +1 次匯率 |
| **`watch --interval 300`(預設,已修工單 008;TW 已加碼工單 013)** | **happy path:TW ≈36/天**(EOD-aware,一天內跨過交易日邊界才重抓,與 interval 無關,工單 013)。**worst case(FinMind 402/斷線等持續失敗期間)**:失敗不寫快取 → 每輪仍是 36 FinMind(全清單),回到工單 008 修復前的量級,`--interval 300` 下仍是 **432/hr**,需仰賴 `_retry` 指數退避與過期快取保命撐過去,不是「怎樣都安全」。US/INTL 抓取量不算在此欄(FinMind 專用)。**工單 017 更新(reviewer 修正包 G1b 之後的真實模型)**:台股 TWSE/TPEx 備援結果**不寫入 blob**(G1b)——這代表 blob 的新鮮度只由「上一次 FinMind 真正成功」的時間戳決定,備援再怎麼成功也不會讓 blob 看起來新鮮。換言之:一旦既有 blob 跨過 EOD 邊界過期,**備援期間每一輪都會照常先打 FinMind 探測**(每檔 1 次,額度壓力與工單 008 之前描述的 worst case 相同,不會因為有備援就下降),FinMind 失敗才觸發備援;但**只要 FinMind 有一次恢復成功,那一輪就立刻寫回正常 blob、回到 happy path**,不會像「若備援結果寫 blob」那樣被 EOD 新鮮度誤判凍結在降級快照上長達 22~71 小時(這正是 G1a/G1b 修正的 P1-1 真實重現)。**每輪額外只加 TWSE+TPEx 最多各 1 次**(免金鑰、process memo 共用全清單,不隨檔數增加、也不隨輪數線性增加——只在跨過 EOD 邊界或負向 memo 冷卻期滿時才各重打 1 次,見上方 §1),換到大多數台股標的能顯示官方 EOD 現價與明確帶分類,而不是整輪只剩過期快取;**不改變 FinMind 額度本身的壓力**,是「多一條命 + 更快恢復正常路徑」,不是「省 FinMind 額度」 | 54/首輪,之後多輪共用(15分 TTL 不變) | ~64/首輪,之後多輪共用(15分 TTL 不變) | happy path 下 TW 額度需求遠低於工單 008 時代;持續失敗時退化回等量(FinMind 側),另加 TWSE/TPEx 極小額外呼叫(見上方 §1 備援列) |

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
4. **快取鍵的已知限制(blob 層新鮮度視窗本身仍未解,留待另開工單)**:檔案快取鍵
   是 `market_ticker`(`_cache_path`),**不含** `history_years` 或 `valuation.method`。
   若使用者只是改了 `watchlist.yaml` 裡某檔的估價假設(例如 pe_band 门檻、method
   從 `price_band` 換成 `pe_band`)而沒有換 ticker,舊快取仍會被視為「新鮮」沿用,
   直到自然過期(US/INTL 最長 15 分;TW EOD-aware 最長約 72 小時,週五收盤後
   改假設要等到週一收盤才會反映)。要立即看到新假設生效,請按側邊欄
   「🔄 重新抓取(清快取)」或手動刪除 `.cache/`。真正的解法(快取鍵納入
   假設指紋)仍留待未來工單,不在本點範圍內。
   **工單 014 更新(根治的是另一個獨立面向,不是這個 blob 新鮮度視窗)**:上述
   「要等 TTL/EOD 邊界或手動清快取才看到新假設生效」這件事本身**沒有改變**——
   014 沒有動 `_cache_path`/blob 讀寫邏輯。014 解的是:**台股在 blob 快取過期後
   重新打 FinMind 時的 payload 大小**。013 之前(含 013),只要 blob 快取一過期,
   `fetch_tw` 無論是不是真的有假設變動,每次都會把 `history_years` 整段(預設
   5 年)重新下載一次。014 之後,同樣的呼叫次數(仍是每 dataset 1 次
   `_finmind`,S8 契約不變),只在**首次**抓取、`history_years` 加深、或
   `valuation.method` 切到從未抓過的 dataset 時才整段全量;其餘情況(單純
   blob 快取自然過期後的例行重抓)只下載「上次成功之後的增量」。詳見下方第 5 點。
5. **本地歷史庫 `.cache/history.sqlite3`(工單 014,台股限定;含 reviewer 修正包
   F1–F4 後現狀)**:blob 快取(第 1 點)之下再加一層 stdlib `sqlite3` 耐久儲存
   (`aimonitor/history_store.py`,WAL 模式、每次操作獨立連線、`timeout=2`),
   疊加、不取代 blob 快取/EOD 新鮮度判斷/stale-rescue(三者行為與字串一律
   未動)。運作方式(`fetch_tw` 接線):
   - **價格/PER**(`_sync_and_assemble`):先讀 `series_meta` 判斷全量(無記錄、
     或這次要求的窗起點比記錄的還早)vs 增量(`start_date` = store 內該序列
     目前的 `MAX(date)`,含當天,重疊 1 筆防尾筆修訂)。呼叫 FinMind 後
     best-effort upsert 進 store,**接著無條件在記憶體把這次剛抓到的原始
     資料與 store 讀回的既有資料依日期合併**(reviewer 修正包 F1 根修)——
     store 寫入是否真的成功,不影響這次組裝結果的正確性:store 全好時合併
     是冪等無感;store「可讀不可寫」(真實檔案鎖、唯讀檔案系統等)時靠這次
     記憶體裡的資料補位,fetch 仍然成功、資料仍然新鮮,只是這次沒能被
     「下一次呼叫的增量」撿到而已(F1 修正前的舊設計會直接信任 store 讀回
     結果,寫入失敗時讀到「寫入前」的舊資料而不自知,嚴重時整批新資料
     消失、`d.ok()` 判 False 卻沒有清楚錯誤訊息,讓 blob 快取不更新、額度
     保護失效——已用真實檔案鎖重現過並修復,見工單 014 REPORT)。**這時候
     才對合併完的完整序列做一次拆股/反分割還原**(`_back_adjust_tw` 本體
     零改動,只是搬到組裝之後才呼叫)——刻意不對每次增量各自局部還原,
     否則拆股跨兩次增量會漏掉回頭修正較早那批資料(工單 014 REPORT 有
     mutation 重演證據)。
   - **增量回應異常偵測**(reviewer 修正包 F2):增量的 `start_date` 是 store
     既有序列的 `MAX(date)`(含當天),正常情況下 FinMind 一定至少會回傳
     這筆重疊列。若回應是空列表,視為異常(暫時性資料源問題等,不是「這段
     期間真的沒有交易」),`fetch_tw` 判定失敗、設 `d.error`,交給 `fetch()`
     既有的 stale-rescue 接手(標「(過期快取)」,恢復 014 之前「抓取失敗
     就整檔 error」的語意)。全量抓取回空維持現狀不受影響(新上市無資料
     等合法情況)。
   - **PER 派生新鮮度護欄**(reviewer 修正包 F3):PER 增量之後,`d.per`/
     `d.trailing_eps`/`d.dividend_yield` 只在 PER 最新一筆與 `d.price_date`
     相差 <=10 天時才派生,超過門檻三者維持 `None`(避免拿過期的 PER 配上
     今天的股價算出誤導的隱含估值)。`d.per_history`(河流圖用的歷史序列)
     不受此限制,仍是完整歷史序列。014 之前 PER 跟價格永遠同一次 API 呼叫
     抓回,天然不會有時間落差;增量之後兩個 dataset 各自步調可能不同步,
     才需要這個護欄。
   - **配息不做增量**(reviewer 修正包 F4):`yield_band` 的配息完全恢復
     014 之前的原始碼路徑——每次都全量抓(`start_date` = 窗起點,仍是 1 次
     `_finmind` 呼叫),`d.div_history` 直接用這次抓到的 raw 資料排序組裝,
     不經 store 讀取組裝。原因:(1) FinMind `TaiwanStockDividend` 的
     `start_date` 篩的是「公告日」欄位,不是 `CashExDividendTradingDate`
     (除息日),兩者可能相差數月甚至跨年,拿「上次看到的除息日」當增量
     游標語意對不上,可能漏抓「公告在游標之前、但除息日還沒發生」的紀錄;
     (2) store 的 dividend 表 PK 是 `(market,ticker,ex_date)`,若同一
     ex_date 曾有多筆紀錄(真實資料可能發生),upsert 會把現狀的 SUM 語意
     破壞成 last-wins。對配息做增量是「零收益、有風險」。
     `history_store.upsert_dividend` 仍保留呼叫,純粹當 best-effort 耐久
     備份(供未來工單使用),**不參與**這次組裝。
   - **呼叫次數不變**(每 dataset 仍 1 次 `_finmind`,S8 契約鎖住,見上方
     §1/§2 的成本表原封不動);改變的只有**同樣次數下,單次呼叫的 payload
     大小**(價格/PER 在 happy path 下從整段 N 年降到增量;配息本來就沒有
     這個優化空間,payload 一直很小,不做也無妨)。
   - **美股/INTL 不做增量,殘留全量重抓(正面陳述,非缺陷)**:
     `fetch_us`/`fetch_us_finnhub` 每次成功後,把 `daily_price` 該 ticker
     整檔 DELETE+INSERT 覆蓋(不是 upsert)。原因:yfinance `auto_adjust=True`
     會隨後續拆股/配息事件回溯改寫已發生日期的收盤價,增量拼接會讓序列
     前後尺度不一致;INTL 的 ROI total-return 語意(股利視為 0、報酬全
     反映在股價)也依賴單一連續 auto_adjust 序列。**這代表美股/INTL 在
     blob 快取過期後,每次都還是整段 `history_years` 年全量向 yfinance
     要資料**——這不是本工單漏做優化,是刻意的正確性優先設計:美股沒有
     FinMind 那種嚴格的每小時額度上限(yfinance 無公開額度、只有雲端機房
     IP 才容易被限流),增量化帶來的節流收益本來就遠低於台股,不值得為了
     省一點 payload 冒「auto_adjust 尺度不一致」的資料正確性風險。store
     對 US/INTL 純粹是耐久備份用(供未來工單,例如 015 缺日偵測),讀路徑
     完全不依賴它、逐位輸出與改動前一致。**快照寫入成本**:每次成功抓取
     約 1250 列(5 年交易日)DELETE+INSERT,純本機 sqlite 操作,對整體
     fetch 耗時無感。
   - **永不炸**:sqlite 檔案損毀/鎖定/唯讀等任何例外一律在 `history_store.py`
     內部吞掉(讀函數回傳 `None`、寫函數回傳 `False`,不外拋),`fetch_tw`
     遇到 store 完全不可用(讀寫皆炸)或只是可讀不可寫,都靠記憶體合併
     (見上方 F1)保證這次 fetch 仍然成功(離線測試已驗證兩種情境:垃圾
     bytes 覆蓋 `history.sqlite3` 後讀寫全部安全降級;真實 patch 模擬
     upsert 恆失敗、讀取正常時,merge 正確補位新舊資料)。
   - **增量的固有代價(誠實揭露)**:一旦某天的價格/PER 值被寫進 store,
     之後除非那天剛好又落在某次增量的重疊窗內,否則不會再被重新抓取——
     若 FinMind 事後更正了某天的歷史數值,增量路徑不會自動吸收這個更正,
     除非那天剛好是下一次增量的 `MAX(date)` 重疊列。**自癒手段**:
     (a) 側邊欄「🔄 重新抓取(清快取)」或手動刪除 `.cache/`(含 sqlite 檔)
     強制整批重來;(b) 把 `config.yaml` 的 `history_years` 調大又調回來,
     會觸發一次全量回填。**定期全量重同步**(例如排程整批重建 sqlite)目前
     不在本工單範圍,列入 backlog,留給未來工單視實際使用經驗決定是否需要。
   - **`use_cache=False` 的範圍**:只繞過 blob 快取的新鮮度判斷(強制重新
     打 FinMind),**不繞過**本地歷史庫——`fetch_tw` 一律照 SPEC D2 的全量/
     增量規則走(store 是耐久資料層,不是「新不新鮮」的快取;現價本身的
     新鮮度由「這次真的打了 live API」保證,跟要不要利用 store 裡的歷史
     資料是兩件事)。換言之:`use_cache=False` 只影響「要不要打 API」,
     不影響「打了 API 之後怎麼組裝」。

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
> 補充 TW EOD-aware 快取變更;US/INTL 15 分固定 TTL 段落未動。工單 014(2026-08)新增
> §3 第 5 點(本地歷史庫)、更新 §3 第 4 點的 P2-4 根治範圍;呼叫次數(§1/§2 表格)
> 全部不變,0 新增 API 呼叫,黃金值交叉驗收 2 次 FinMind(2330 為 pe_band,Price+PER)。
> 014 reviewer 修正包(2026-08,F1–F6)在合併前再次更新 §3 第 5 點:P1-1 根修(sqlite
> 可讀不可寫時記憶體合併保底)、P1-2a(增量回空視為失敗)、P1-2b(PER 派生 10 天新鮮度
> 護欄)、配息完全撤出增量(恢復 014 之前原始碼路徑)。呼叫次數與 API 額度結論不變,
> 黃金值再次交叉驗收 2 次 FinMind,結果一致。
> 工單 015(2026-08)為純本地缺口/尾端過舊偵測(`StockData.quality_warnings`,fetch_tw/
> fetch_us 組裝完成後附掛,不影響估價/分類/ROI 數值),blob 快取多一個欄位但已在
> `_load_cache_raw` 過濾未知鍵確保新舊版互讀不炸,§1/§2 呼叫數表與快取新鮮度規則全部
> 不變,0 新增 API 呼叫。
> 工單 017(2026-08)新增 TWSE(上市)/TPEx(上櫃)官方 OpenAPI 備援,**只在 FinMind
> price 抓取失敗時觸發**(見上方新增的 §1 備援列與 §2 `watch` 列更新),兩端點皆免金鑰、
> 一次呼叫回全市場當日收盤,process 內 memo(重用工單 013 `_tw_cache_fresh` 的 EOD
> 邊界規則判斷失效)讓同一輪最多各打 1 次,不隨檔數增加。FinMind 健康時的呼叫數
> (§1/§2 既有表格數字)**逐位不變、0 新增呼叫**(regression 測試鎖住,見
> `tests/test_twse_fallback.py` 的「FinMind 正常時 → TWSE/TPEx 呼叫數 == 0」)。
> 一次性 schema 確認呼叫:各打 1 次 TWSE `exchangeReport/STOCK_DAY_ALL`、TPEx
> `tpex_mainboard_daily_close_quotes`(SPEC 明文允許,非額度紅線;之後測試全離線)。
> 黃金值交叉驗收 2 次 FinMind(2330 為 pe_band,Price+PER,FinMind 健康,備援未觸發),
> 結果與改動前一致(便宜 2228.57 / 大特 1731.23)。
> **工單 017 reviewer 修正包(2026-08,G1–G8)**:三條 P1 真實重現後仲裁修正,呼叫數模型
> 有實質更新(上方 §1 備援列、§2 `watch` 列已同步改寫,不是本段重複)——重點:
> (a) G1b 台股備援結果**不寫入 blob**,所以「FinMind 呼叫數不變」不再只是巧合式描述,
> 而是 G1b 直接導致的必然結果(blob 新鮮度只認 FinMind 真正成功的時間戳,備援期間
> 每輪都會照常先探測 FinMind,一旦恢復立刻回正常路徑,不會被 EOD 新鮮度多釘住);
> (b) G3 幫 TWSE/TPEx 備援呼叫本身加上 15 分鐘負向 memo + `timeout=10`,把「全端點
> 故障時逐檔重打兩端點,單輪最壞可疊加到近一小時」壓到「單輪只有第一檔付出真正探測
> 成本」;G1a/G2(歷史來源 store 優先退回舊 blob + 尺度脫鉤護欄)則是資料正確性修正,
> 不影響呼叫數,但避免了 auto 類估價假 ValuationError 與尺度脫鉤產生的假 is_buy 訊號。
> FinMind 健康路徑呼叫數(§1/§2 既有表格數字)**仍然逐位不變、0 新增呼叫**(210 題
> regression 全綠,新增 48 題於 `tests/test_twse_fallback.py`);黃金值再次交叉驗收
> 2 次 FinMind(cache-miss 強制重抓),結果一致(便宜 2228.57 / 大特 1731.23 /
> forward_EPS 135.147)。

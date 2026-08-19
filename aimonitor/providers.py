"""資料來源層。

設計原則:
* stdlib 優先 (urllib) — 台股 FinMind 不需任何第三方套件。
* 美股用 yfinance(若已安裝);抓不到時優雅降級、給清楚訊息,而不是整支崩掉。
* 所有抓回來的資料放進輕量 JSON 快取,避免短時間重複打 API。

回傳統一格式 StockData,讓上層估價/分類模組不用管資料從哪來。
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, fields, asdict
from datetime import datetime, timedelta, timezone

from . import history_store  # 工單 014:本地 sqlite 歷史庫(台股增量、美股全量快照)

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")


@dataclass
class StockData:
    ticker: str
    market: str                       # "TW" | "US"
    name: str = ""
    currency: str = ""
    price: float | None = None        # 最新日收盤價(EOD;盤中:台股=昨收、美股≈延遲15分)
    price_date: str = ""
    trailing_eps: float | None = None # 近四季 EPS
    per: float | None = None          # 目前本益比
    shares: float | None = None
    revenue_ttm: float | None = None
    dividend_yield: float | None = None   # 以「比例」表示, 0.0093 = 0.93%
    annual_dividend: float | None = None  # 近12月現金配息(每單位),供 ETF 殖利率法
    div_history: list = field(default_factory=list)     # [(除息日, 現金配息), ...]
    price_history: list = field(default_factory=list)   # [(date, close), ...] 舊→新
    per_history: list = field(default_factory=list)     # [(date, per), ...]
    per_history_approx: bool = False  # 美股的 PER 河流圖是近似(price/現EPS)
    quality_warnings: list = field(default_factory=list)  # 015:缺口/尾端過舊偵測告知
    source: str = ""
    error: str = ""

    def ok(self) -> bool:
        return self.price is not None and self.error == ""


# blob 相容:_load_cache_raw 用這個集合過濾快取 JSON 裡的未知鍵,避免「新版寫入的
# blob 裡有現在 dataclass 沒有的欄位」(例如新增/移除欄位期間的新舊版互讀,或未來
# 欄位)時 `StockData(**blob)` 因為多餘的 keyword argument 直接 TypeError——已用
# python 實測驗證缺鍵會被 default_factory 正常補上、但多鍵會炸,細節見工單 015 PLAN。
_STOCKDATA_FIELD_NAMES = {f.name for f in fields(StockData)}


# --------------------------------------------------------------------------- #
#  快取
# --------------------------------------------------------------------------- #
def _cache_path(market: str, ticker: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = ticker.replace("/", "_")
    return os.path.join(CACHE_DIR, f"{market}_{safe}.json")


# 台北時區:固定 UTC+8 偏移(台灣自 1979 年起無 DST)。刻意不用 zoneinfo/tzdata——
# Windows 沒有系統 tz db,拉這個依賴不值得,固定偏移對台股交易時區已經足夠精確。
TPE = timezone(timedelta(hours=8))


def _now_tpe() -> datetime:
    """取得「現在」的台北 aware datetime。獨立成一個函數(而不是散落各處的
    `datetime.now(tz=TPE)`)是為了讓測試能用 `patch.object(providers, "_now_tpe",
    return_value=...)` 釘死假時鐘,寫出不依賴系統當前時間、可重現的整合測試
    (工單 013 reviewer 修正包 P2-3/P3-5)。"""
    return datetime.now(tz=TPE)


def _parse_fetched_at(raw: str) -> datetime:
    """把快取檔的 `_fetched_at` 字串正規化成台北時區(UTC+8)aware datetime。

    兩種輸入都要吃:
    * legacy naive isoformat(舊版 `_save_cache` 用 `datetime.now()` 寫入,無 tz)
      —— 一律當作「已經是台北時間」直接貼標籤,不做時區換算。系統時鐘會決定
      這個假設的準確度與偏差方向(工單 013 reviewer 修正包 P2-2,誠實記錄,
      不宣稱「唯一風險是多抓」):
        - 本機在台灣執行(系統時鐘 = UTC+8):精確,無偏差。
        - 雲端機器系統時鐘 = UTC(常見預設,例如多數 Docker 基底映像):naive
          戳記會被多貼 8 小時的「偏舊」台北標籤 → 算出的 age 比實際久 →
          偏向「多抓一次」,方向保守。雲端容器通常不持久化 `.cache/`(重新
          部署即全新),實務影響頂多是重啟後那一輪把受影響的快取當過期重抓
          一次,之後全部覆寫成新版 aware UTC 格式,不會持續累積。
        - 系統時鐘位於 UTC+8 **以東**(例如 UTC+9~+14):方向會反過來——
          naive 戳記被貼的台北標籤反而「超前」真實時間,可能算出**負年齡**,
          讓真的已經過期的資料被誤判成新鮮,直到該筆快取被下一次成功抓取
          覆寫為止。同樣是一次性、有界(不會無限期卡在錯誤狀態),但方向
          不再保守,不能宣稱恆安全。
    * aware isoformat(新版寫入,UTC)—— 用 astimezone 轉換成台北時間,不受
      系統時鐘時區影響,上述偏差只影響 legacy naive 這一種輸入。
    """
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TPE)
    return dt.astimezone(TPE)


def _cache_age_minutes(fetched_at: datetime, now: datetime | None = None) -> float:
    """兩者正規化到台北 aware datetime 後才相減,避免 naive/aware 混用 TypeError。"""
    now = now if now is not None else _now_tpe()
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=TPE)
    return (now - fetched_at).total_seconds() / 60.0


def _tw_cache_fresh(fetched_at: datetime, now: datetime | None = None,
                     eod_hour: int = 18, floor_minutes: float = 15.0) -> bool:
    """台股 EOD-aware 快取新鮮度(工單 013)。FinMind 是日收盤資料,收盤後約
    傍晚才更新,一天只變一次——比固定 15 分 TTL 更貼近實際更新頻率。

    規則(orchestrator 已核准,不得偏離):
    1. 時區:兩個輸入一律正規化成台北(UTC+8)aware datetime 再比較;naive 輸入
       視為「已經是台北時間」(呼叫端已用 `_parse_fetched_at`/系統時間產生時通常
       已是這個形狀,這裡再做一次防禦性正規化)。
    2. 安全地板:`now - fetched_at < floor_minutes` 一律新鮮,不管是否跨邊界
       (防時鐘/時區錯誤或抓取後極短時間內重複呼叫造成重抓風暴)。
    3. 邊界:每個週一~週五台北 `eod_hour`:00(預設 18:00,對應 FinMind 收盤後
       更新,留餘裕)。規則是 `(fetched_at, now]` 區間內**不含任何邊界**才新鮮;
       只要跨過一個邊界就視為過期。週末沒有邊界(週五收盤後抓的快取整個週末
       新鮮,直到週一 18:00)。國定假日不查行事曆、視同交易日——每逢假日多
       抓一次,可接受,換取不依賴行事曆資料。
    """
    def _norm(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TPE)
        return dt.astimezone(TPE)

    fetched_at = _norm(fetched_at)
    now = _norm(now) if now is not None else _now_tpe()

    if now <= fetched_at:
        return True  # 時鐘異常或同一瞬間:不因此誤判過期

    if (now - fetched_at) < timedelta(minutes=floor_minutes):
        return True  # 安全地板:無條件新鮮,凌駕邊界判斷

    # 掃描 fetched_at 當天到 now 當天之間的每一天;只要某天是平日且它的
    # eod_hour:00 邊界落在 (fetched_at, now] 區間內,就代表跨過一次資料更新邊界。
    day = fetched_at.date()
    last_day = now.date()
    while day <= last_day:
        if day.weekday() < 5:  # 週一=0 ... 週五=4;週六/日沒有邊界
            boundary = datetime(day.year, day.month, day.day, eod_hour, 0, tzinfo=TPE)
            if fetched_at < boundary <= now:
                return False
        day += timedelta(days=1)
    return True


def _load_cache_raw(market: str, ticker: str):
    """讀快取檔,回傳 `(StockData, fetched_at)`(fetched_at 已正規化成台北 aware
    datetime);檔案不存在或內容損毀 → `None`。**不做任何新鮮度判斷**——新鮮度
    交給呼叫端(`_load_cache` 的固定 TTL,或 TW 的 `_tw_cache_fresh`)決定,
    讓兩種新鮮度規則共用同一份「讀檔 + 解析」邏輯,不重複。"""
    p = _cache_path(market, ticker)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            blob = json.load(f)
        fetched_at = _parse_fetched_at(blob["_fetched_at"])
        blob.pop("_fetched_at", None)
        # 過濾掉 dataclass 沒有的鍵(向前相容,見上方 _STOCKDATA_FIELD_NAMES 註解)。
        blob = {k: v for k, v in blob.items() if k in _STOCKDATA_FIELD_NAMES}
        return StockData(**blob), fetched_at
    except Exception:
        return None


def _load_cache(market: str, ticker: str, max_age_min: float):
    """固定 TTL 新鮮度判斷(US/INTL 用;行為與改動前一致,僅底層改用 aware
    台北時間相減,避免新版 `_save_cache` 寫入 aware UTC 戳記後 naive-vs-aware
    相減炸 TypeError)。"""
    loaded = _load_cache_raw(market, ticker)
    if loaded is None:
        return None
    data, fetched_at = loaded
    if max_age_min is not None and _cache_age_minutes(fetched_at) > max_age_min:
        return None  # max_age_min=None 表示「無視 TTL」(過期快取保命用)
    return data


def _save_cache(data: StockData):
    try:
        blob = asdict(data)
        blob["_fetched_at"] = datetime.now(timezone.utc).isoformat()
        with open(_cache_path(data.market, data.ticker), "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  資料品質:缺口/尾端過舊偵測(工單 015)
#
#  百分位/波動率/殖利率河流圖都假設歷史序列完整;FinMind/yfinance 若有資料缺口
#  目前是靜默偏差。這裡只做「偵測 + 告知」,刻意**不自動補抓**(避免對缺口反覆
#  重試造成呼叫風暴;真的要補資料屬人工判斷)。純函數、市場中立(TW/US/INTL
#  共用同一份邏輯與同一個時鐘 _now_tpe——SPEC 明言美股用台北時鐘誤差一天無妨),
#  不改變任何估價/分類/ROI 數值,只附掛在 StockData.quality_warnings 供顯示層
#  (report.py/app.py)提醒使用者。
# --------------------------------------------------------------------------- #
GAP_WARNING_DAYS = 12          # 相鄰兩筆缺口 > 此門檻才警告(台股春節連假含前後
                                # 週末最長約 11 天,12 為安全門檻,避免誤報正常連假)
STALE_TAIL_WARNING_DAYS = 10   # 序列最後一筆距 now > 此門檻(抓取仍回報成功)才警告


def _series_quality_warnings(price_history, now=None) -> list:
    """偵測 `price_history`([(date_str, close), ...],不假設已排序)裡的資料缺口
    與尾端過舊,回傳警告文字列表(可能為空)。序列 < 2 筆不檢查(既有空資料路徑
    自有錯誤處理,這裡不重複判斷)。`now` 可注入(測試釘假時鐘),預設 `_now_tpe()`。
    """
    if len(price_history) < 2:
        return []
    now = now if now is not None else _now_tpe()
    dates = sorted(d for d, _ in price_history)
    warnings = []
    for prev, cur in zip(dates, dates[1:]):
        gap = _days_between(prev, cur)
        if gap > GAP_WARNING_DAYS:
            warnings.append(
                f"資料缺口:{prev} ~ {cur}(相差 {gap} 天),期間百分位/波動率可能因此偏差。"
            )
    last_date = dates[-1]
    tail_age = (now.date() - datetime.strptime(last_date, "%Y-%m-%d").date()).days
    if tail_age > STALE_TAIL_WARNING_DAYS:
        warnings.append(
            f"最新資料為 {last_date},距今已 {tail_age} 天,百分位/波動率可能因此偏差。"
        )
    return warnings


# --------------------------------------------------------------------------- #
#  HTTP 小工具
# --------------------------------------------------------------------------- #
def _retry(fn, attempts: int = 3, base_delay: float = 0.8):
    """重試包裝:對抗暫時性網路/限流(雲端機房 IP 的 yfinance 常被 Yahoo 429)。指數退避。"""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep(base_delay * (2 ** i))
    raise last


def _http_get_json(url: str, timeout: int = 25) -> dict:
    def _do():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (aimonitor)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    return _retry(_do, attempts=3)


# --------------------------------------------------------------------------- #
#  台股: FinMind
# --------------------------------------------------------------------------- #
FINMIND = "https://api.finmindtrade.com/api/v4/data"


def _finmind(dataset: str, data_id: str, start_date: str, token: str = "") -> list:
    params = {"dataset": dataset, "data_id": data_id, "start_date": start_date}
    if token:
        params["token"] = token
    url = FINMIND + "?" + urllib.parse.urlencode(params)
    j = _http_get_json(url)
    if j.get("status") != 200 and "data" not in j:
        raise RuntimeError(f"FinMind {dataset} 回傳異常: {j.get('msg', j)}")
    return j.get("data", [])


def _back_adjust_tw(price_history, div_history):
    """還原分割/反分割。真正的拆股是整數倍(1:2→r≈0.5、1:4→r≈0.25、反分割→r≥2);
    除息/除權當日也跳空但幅度小很多。為避免把高股息 ETF/個股的大額除息誤判成分割,
      (1) 門檻收緊到 r<0.6 或 r>1.7,避開 6~40% 的除權息雜訊;
      (2) 額外排除「現金除息日」(該日跳空是配息,不是分割)。
    把斷層之前的價與配息還原到現尺度,修正 price_band 百分位/波動率/殖利率河流圖。"""
    price_history = sorted(price_history)          # 確保升冪(f_for 的查找依賴此前提)
    n = len(price_history)
    if n < 2:
        return price_history, div_history
    ex_dates = {ex for ex, _ in div_history}       # 現金除息日:跳空屬配息,非分割
    factor = [1.0] * n
    cum = 1.0
    for i in range(n - 1, 0, -1):
        prev = price_history[i - 1][1]
        cur = price_history[i][1]
        if prev and cur and price_history[i][0] not in ex_dates:
            r = cur / prev
            if r < 0.6 or r > 1.7:                 # 只認真正的整數倍拆/反拆
                cum *= r
        factor[i - 1] = cum
    adj_px = [(d, (c * factor[i]) if c else c) for i, (d, c) in enumerate(price_history)]
    if not div_history:
        return adj_px, div_history
    import bisect
    dates = [d for d, _ in price_history]
    def f_for(date):                                # 該除息日所屬的 factor(二分查找)
        j = bisect.bisect_right(dates, date) - 1
        return factor[j] if j >= 0 else factor[0]
    adj_div = [(ex, cash * f_for(ex)) for ex, cash in div_history]
    return adj_px, adj_div


def _days_between(a: str, b: str) -> int:
    """兩個 "YYYY-MM-DD" 字串日期相差的天數(絕對值)。工單 014 reviewer 修正包
    F3 用來判斷 PER 的「最新一筆」是否與現價「同一批」(見 fetch_tw pe_band 段落)。"""
    return abs((datetime.strptime(a, "%Y-%m-%d") - datetime.strptime(b, "%Y-%m-%d")).days)


def _merge_rows_by_key(base_rows, fresh_rows):
    """依每筆 tuple 的第一個欄位(日期字串)為鍵合併——`fresh_rows`(這次剛抓到的
    原始資料)覆蓋 `base_rows`(store 讀到的既有資料)同鍵的值,回傳依鍵升冪排序
    的完整列表。price("date",close)/per("date",per,dividend_yield)兩種 tuple
    形狀皆通用(只依賴「每筆第一個元素是可排序的日期字串」這個共同點)。

    工單 014 reviewer 修正包 F1(P1-1 根修):sqlite「可讀不可寫」(例如另一支
    process 持有寫鎖、或檔案系統唯讀)時,舊設計「upsert 後直接信任 get_fn 的
    讀回結果」會讀到**寫入前的舊資料**而不自知(讀本身沒有例外、`get_fn` 回傳
    非 None 的合法結果,只是內容還沒反映這次剛抓到的新資料)——導致
    `d.price_history` 缺了這次的新資料、`d.price` 停在舊值,嚴重時全空的
    StockData 還會讓 `d.ok()` 判 False、卻沒有清楚的 `d.error`,使 `fetch()`
    不寫回 blob 快取、下一輪又整批重抓,額度保護整個失效。改成**無條件在記憶體
    合併**:不管 store 寫入這次成功與否,一律拿「store 讀到的既有資料」與
    「這次剛抓到的 raw_rows」合併,`raw_rows` 必定覆蓋對應日期——store 全部正常
    時合併是冪等無感(讀回的本來就已經包含這次寫入的資料,合併結果不變);
    store 寫入失敗時,靠這次記憶體裡的 raw_rows 補位,確保這次呼叫依然成功、
    資料依然新鮮,只是這次的 upsert 沒有被「下一次呼叫的增量」撿到而已
    (下次呼叫仍會照 SPEC D2 判斷全量/增量,不會因為這次沒寫成功而整個卡死)。"""
    merged = {row[0]: row for row in base_rows}
    merged.update({row[0]: row for row in fresh_rows})
    return [merged[k] for k in sorted(merged)]


def _sync_and_assemble(dataset: str, cache_dir: str, ticker: str, requested_start: str,
                        fetch_fn, upsert_fn, get_fn):
    """工單 014 D2 的全量/增量共用骨架。對單一 dataset("price"/"per",market 恆為
    "TW" —— 此函數只服務 `fetch_tw`;配息自工單 014 reviewer 修正包 F4 起撤出,
    改回每次全量、不經此函數,見 `fetch_tw` yield_band 段落)執行:

    1. 讀 `series_meta.requested_start` 判斷全量 vs 增量(SPEC D2 point 1–2):
       無記錄,或這次要求的窗起點比記錄的還早(history_years 加深/新 dataset,
       含 method 切換)→ 全量,`fetch_fn` 的 start 參數 = 這次要求的窗起點;
       否則增量,start = store 內該序列目前的 MAX(date)(含當天,重疊 1 筆靠
       合併吸收,防尾筆修訂——SPEC D2 point 2)。
    2. 呼叫 `fetch_fn(start)` 真正打 FinMind——**例外原樣往外拋**,不在這裡吞,
       由呼叫端(`fetch_tw`)的 try/except 決定要不要致命(價格:致命,現狀
       行為不變;PER:非致命,現狀行為不變)。
    3. 呼叫成功則 upsert 進 store、更新 meta(皆 best-effort——store 的公開
       函數已經「永不炸」,這裡不需要也不應該再包一層 try/except;寫入是否
       真的成功不影響這次呼叫的正確性,見下方第 4 點與 `_merge_rows_by_key`)。
    4. 從 store 取 `date >= requested_start` 的既有資料(不可用/無資料 → `[]`),
       與這次剛抓到的 `raw_rows` **在記憶體合併**(F1 根修,見
       `_merge_rows_by_key` docstring)後回傳——不論 store 讀寫是否正常,這次
       結果都保證新鮮、正確。

    回傳 `(merged_rows, incremental_empty)`:
    - `merged_rows`:如上,**刻意不在這裡做 `_back_adjust_tw`**——那必須在
      price/div 兩個組裝結果都到齊之後,由 `fetch_tw` 對「完整組裝序列」統一
      呼叫一次(D2 point 4,本工單命脈:分割還原若對「單次增量」各自局部執行,
      跨增量的分割會被漏掉一段,見工單 REPORT 的 mutation 重演)。
    - `incremental_empty`:工單 014 reviewer 修正包 F2——這次走的是「增量」
      分支、且 FinMind 回應是空列表時為 `True`(增量起點是 store 既有序列的
      MAX(date),正常情況下這筆重疊列一定會被回傳,回空是異常訊號,交給
      `fetch_tw` 決定要不要視為失敗);全量分支永遠是 `False`(全量抓到空是
      現狀就允許的正常情況,例如新上市無資料)。
    """
    meta = history_store.get_meta(cache_dir, "TW", ticker, dataset)
    is_full = meta is None or not meta.get("requested_start") or requested_start < meta["requested_start"]
    if is_full:
        fetch_start = requested_start          # 全量:抓「這次要求的窗起點」
        keep_start = requested_start
    else:
        fetch_start = history_store.max_date(cache_dir, "TW", ticker, dataset) or requested_start
        keep_start = meta["requested_start"]   # 增量:窗深度不變,requested_start 維持原記錄

    raw_rows = fetch_fn(fetch_start)           # 例外原樣往外拋,呼叫端接
    incremental_empty = (not is_full) and (not raw_rows)

    upsert_fn(cache_dir, "TW", ticker, raw_rows)   # best-effort;失敗不影響下面的組裝正確性
    history_store.set_meta(cache_dir, "TW", ticker, dataset, keep_start,
                            datetime.now(timezone.utc).isoformat())

    stored = get_fn(cache_dir, "TW", ticker, start_date=requested_start) or []
    return _merge_rows_by_key(stored, raw_rows), incremental_empty


def fetch_tw(ticker: str, name: str, years: int, token: str = "", method: str = "") -> StockData:
    start = (datetime.now() - timedelta(days=int(years * 365.25) + 10)).strftime("%Y-%m-%d")
    d = StockData(ticker=ticker, market="TW", name=name, currency="TWD", source="FinMind")
    try:
        def _fetch_price(s):
            prices = _finmind("TaiwanStockPrice", ticker, s, token)
            return [(r["date"], float(r["close"])) for r in prices if r.get("close")]

        price_rows, price_incremental_empty = _sync_and_assemble(
            "price", CACHE_DIR, ticker, start, _fetch_price,
            history_store.upsert_price, history_store.get_price,
        )
        if price_incremental_empty:
            # 工單 014 reviewer 修正包 F2(P1-2a):增量起點 = store 內既有序列的
            # MAX(date)(含當天),正常情況下 FinMind 一定至少會回傳這筆重疊列
            # ——回空代表異常(暫時性資料源問題等),不是「這段期間真的沒有
            # 交易」。視為失敗,讓 fetch() 的 stale-rescue(過期快取保命)接手,
            # 不要把「查無資料」誤當成功、寫回一個縮水的快取蓋掉可能還新鮮的
            # 資料(恢復 014 之前「抓取失敗就整檔 error」的語意)。全量抓取回空
            # 則維持現狀不變(新上市無資料等合法情況,不受此檢查影響)。
            d.error = "FinMind 增量回應為空(無新資料),沿用快取。"
            return d
        d.price_history = list(price_rows)
        if d.price_history:
            d.price = d.price_history[-1][1]
            d.price_date = d.price_history[-1][0]
    except Exception as e:
        if "402" in str(e):     # FinMind 免費額度用罄(每小時上限)
            d.error = "FinMind 免費額度用罄(402)。約 1 小時後自動重置;或到 finmindtrade.com 申請免費 token 設為 FINMIND_TOKEN 提高額度。"
        else:
            d.error = f"FinMind 價格抓取失敗: {e}"
        return d
    # 為省 FinMind 額度:PER 只給 pe_band(含 auto 河流圖)、配息只給 yield_band。
    if method == "pe_band":
        try:
            def _fetch_per(s):
                per = _finmind("TaiwanStockPER", ticker, s, token)
                rows = []
                for r in per:
                    raw_per = r.get("PER")
                    p_val = float(raw_per) if raw_per not in (None, 0) else None
                    dy = r.get("dividend_yield")
                    dy_val = float(dy) if dy else None   # 存 FinMind 原始百分數,/100 留到組裝後
                    rows.append((r["date"], p_val, dy_val))
                return rows

            per_rows, _ = _sync_and_assemble(
                "per", CACHE_DIR, ticker, start, _fetch_per,
                history_store.upsert_per, history_store.get_per,
            )
            per_rows = list(per_rows)
            d.per_history = [(dt, p) for dt, p, dy in per_rows if p not in (None, 0)]
            if per_rows:
                last_dt, last_p, last_dy = per_rows[-1]
                # 工單 014 reviewer 修正包 F3(P1-2b 護欄):PER 的「最新一筆」現在
                # 來自 store 組裝(可能是先前某次增量留下的),不再保證跟這次的
                # d.price_date 出自同一批 API 回應——兩者可能有時間落差(例如 PER
                # 增量暫時落後價格增量,或剛切換 method 時 PER 還沒同步到位)。
                # 014 之前 PER 跟價格永遠同一次呼叫抓回,天然不會有這個落差。
                # 這裡用 10 天門檻模擬「同一批」的寬鬆容忍——超過就不派生
                # d.per/d.trailing_eps/d.dividend_yield(三者維持 None),避免拿
                # 過期的 PER 配上今天的股價算出誤導的隱含估值;`d.per_history`
                # (河流圖用的歷史序列)不受此限制,仍是完整組裝後的歷史序列。
                if d.price_date and _days_between(last_dt, d.price_date) <= 10:
                    d.per = float(last_p) if last_p else None
                    d.dividend_yield = float(last_dy) / 100.0 if last_dy else None
                    if d.per and d.price:
                        d.trailing_eps = round(d.price / d.per, 4)  # EPS = 股價 / 本益比
        except Exception:
            pass  # PER 拿不到不致命
    if method == "yield_band":
        try:
            # 工單 014 reviewer 修正包 F4(P2-1/P2-2):配息刻意撤出增量路徑
            # (不再呼叫 `_sync_and_assemble`),每次都全量抓(start=窗起點,
            # 仍是 1 次 `_finmind` 呼叫,配息資料本來就稀疏、payload 很小,
            # 增量在這裡沒有節流收益)。恢復成 014 之前的原始碼路徑——直接用
            # 這次全量抓到的 raw 資料組裝 `d.div_history`,不經 store 讀取。
            # 理由:
            # (1) FinMind `TaiwanStockDividend` 的 `start_date` 篩的是「公告日」
            #     欄位,不是 `CashExDividendTradingDate`(除息日)——兩者可能
            #     相差數月甚至跨年。拿「上次看到的除息日」當增量游標送出去,
            #     游標語意跟真正的篩選欄位對不上,可能漏抓「公告在游標之前、
            #     但除息日還沒發生」的紀錄,或被未來 ex_date 卡住游標。
            # (2) history_store 的 dividend 表 PK 是 (market,ticker,ex_date)——
            #     若同一 ex_date 曾有多筆紀錄(真實資料可能發生),upsert 會讓
            #     後寫入的覆蓋前面,把現狀的 SUM 語意破壞成 last-wins。
            # 兩者相加,對配息做增量是「零收益、有風險」。
            div = _finmind("TaiwanStockDividend", ticker, start, token)
            hist = []
            for r in div:
                ex = r.get("CashExDividendTradingDate") or ""
                cash = (r.get("CashEarningsDistribution") or 0) + (r.get("CashStatutorySurplus") or 0)
                if ex and cash:
                    hist.append((ex, float(cash)))
            d.div_history = sorted(hist)
            # `history_store.upsert_dividend` 仍保留呼叫,純粹當 best-effort 耐久
            # 備份(供未來工單使用,例如 015 缺日偵測);**不參與**這次的組裝,
            # 寫入失敗也不影響 `d.div_history`/`d.annual_dividend`。
            history_store.upsert_dividend(CACHE_DIR, "TW", ticker, hist)
        except Exception:
            pass
    # 還原分割(總是執行,即使無配息也要修正分割股的價格序列)。工單 014:
    # price_history 是「store 組裝 + 記憶體合併後的完整序列」(可能橫跨多次
    # 增量抓取拼接而成,F1 保證即使 store 寫入失敗也不影響這裡拿到的內容),
    # div_history 是這次全量抓到的原始序列(F4,配息不做增量)。還原必須在
    # 兩者都到齊的這一步對完整序列做一次,不能拆到各次增量各自局部處理——
    # 見 `_sync_and_assemble` docstring 與工單 014 SPEC D2 point 4。
    d.price_history, d.div_history = _back_adjust_tw(d.price_history, d.div_history)
    if d.price_history:
        d.price = d.price_history[-1][1]
    cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    if d.div_history:
        d.annual_dividend = round(sum(c for ex, c in d.div_history if ex > cutoff), 4)
        if d.annual_dividend and d.price and not d.dividend_yield:
            d.dividend_yield = d.annual_dividend / d.price
    # 015:序列組裝(含拆股還原)完成後才偵測缺口/尾端過舊,純附加告知,不影響上面
    # 任何已算完的數值。
    d.quality_warnings = _series_quality_warnings(d.price_history)
    return d


# --------------------------------------------------------------------------- #
#  美股備援: Finnhub — 即時報價在雲端機房 IP 也拿得到(解決 yfinance 429)。
#  免費版有 /quote(報價)與 /stock/metric(基本面),但無 /stock/candle(歷史→付費)。
# --------------------------------------------------------------------------- #
FINNHUB = "https://finnhub.io/api/v1"


def _finnhub_us(ticker: str, key: str) -> dict:
    out = {}
    sym = urllib.parse.quote(ticker)
    q = _http_get_json(f"{FINNHUB}/quote?symbol={sym}&token={key}")
    price = _f(q.get("c"))
    if not price:                      # c=0 → 無此美股或無資料
        return out
    out["price"] = price
    ts = q.get("t")
    out["date"] = (datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                   if ts else datetime.now().strftime("%Y-%m-%d"))
    try:                               # 基本面(trailing EPS / PE / 殖利率),best-effort
        m = _http_get_json(f"{FINNHUB}/stock/metric?symbol={sym}&metric=all&token={key}").get("metric", {})
        out["eps"] = _f(m.get("epsTTM") or m.get("epsBasicExclExtraItemsTTM"))
        out["per"] = _f(m.get("peTTM") or m.get("peBasicExclExtraTTM"))
        dy = _f(m.get("dividendYieldIndicatedAnnual") or m.get("currentDividendYieldTTM"))
        out["div"] = (dy / 100.0) if dy is not None else None   # Finnhub 殖利率為百分數
    except Exception:
        pass
    return out


def fetch_us_finnhub(ticker: str, name: str, years: int, key: str) -> StockData:
    """Finnhub 取即時報價/基本面;歷史(免費版無)盡力用 yfinance 補,雲端被限流就留空。
    Finnhub 失敗(或非美股代號)→ 整個退回 yfinance。"""
    d = StockData(ticker=ticker, market="US", name=name, currency="USD", source="Finnhub")
    try:
        fh = _finnhub_us(ticker, key)
    except Exception:
        fh = {}
    if not fh.get("price"):
        return fetch_us(ticker, name, years)        # 退回 yfinance
    d.price, d.price_date = fh["price"], fh["date"]
    d.trailing_eps, d.per, d.dividend_yield = fh.get("eps"), fh.get("per"), fh.get("div")
    try:                                            # 盡力補歷史與年配息(雲端失敗就略過)
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period=f"{max(years,1)}y", auto_adjust=True)
        if hist is not None and len(hist):
            d.price_history = [(i.strftime("%Y-%m-%d"), float(c)) for i, c in zip(hist.index, hist["Close"])]
            # 工單 014 D3:美股全量快照(DELETE+INSERT,best-effort,不影響回傳值)。
            # 禁止用增量——理由見 history_store.replace_us_snapshot docstring。
            history_store.replace_us_snapshot(CACHE_DIR, d.market, ticker, d.price_history)
            # 015:Finnhub 報價成功 + best-effort 歷史也到手的路徑,同樣附掛資料品質
            # 偵測(雲端帶 Finnhub 金鑰的使用者主要走這條;歷史缺失時序列 <2 筆自然
            # 回空清單,不誤報)。
            d.quality_warnings = _series_quality_warnings(d.price_history)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        d.trailing_eps = d.trailing_eps if d.trailing_eps is not None else _f(info.get("trailingEps"))
        d.annual_dividend = _f(info.get("trailingAnnualDividendRate"))
        if not d.dividend_yield:
            dy = _f(info.get("trailingAnnualDividendYield"))
            if dy is not None:
                d.dividend_yield = dy if dy < 1 else dy / 100.0
    except Exception:
        pass
    if d.per is None and d.price and d.trailing_eps and d.trailing_eps > 0:
        d.per = round(d.price / d.trailing_eps, 2)
    if d.trailing_eps and d.trailing_eps > 0 and d.price_history:
        d.per_history = [(dt, round(c / d.trailing_eps, 2)) for dt, c in d.price_history]
        d.per_history_approx = True
    return d


# --------------------------------------------------------------------------- #
#  美股: yfinance (主) — 失敗時給清楚降級訊息
# --------------------------------------------------------------------------- #
def fetch_us(ticker: str, name: str, years: int) -> StockData:
    d = StockData(ticker=ticker, market="US", name=name, currency="USD", source="yfinance")
    try:
        import yfinance as yf
    except ImportError:
        d.error = "未安裝 yfinance。請 `pip install yfinance` 以啟用美股報價。"
        return d
    try:
        t = yf.Ticker(ticker)
        hist = _retry(lambda: t.history(period=f"{max(years,1)}y", auto_adjust=True), attempts=3)
        if hist is not None and len(hist):
            d.price_history = [(idx.strftime("%Y-%m-%d"), float(c))
                               for idx, c in zip(hist.index, hist["Close"])]
            d.price = d.price_history[-1][1]
            d.price_date = d.price_history[-1][0]
            # 工單 014 D3:美股/INTL 全量快照(DELETE+INSERT 同一交易,best-effort,
            # 不影響回傳值;讀路徑完全不改)。**禁止在此改用增量**——yfinance
            # auto_adjust=True 會隨後續拆股/配息事件回溯改寫已發生日期的收盤價,
            # 增量拼接會讓序列前後尺度不一致;INTL 的 ROI total-return 語意(股利
            # 視為 0、報酬全反映在股價)也依賴單一連續 auto_adjust 序列。詳見
            # history_store.replace_us_snapshot docstring。
            history_store.replace_us_snapshot(CACHE_DIR, d.market, ticker, d.price_history)
            # 015:price_history 在這裡是這次呼叫「組裝完成」的最終序列(yfinance
            # auto_adjust=True 已內含拆股/配息還原,US/INTL 之後不再變動)。
            d.quality_warnings = _series_quality_warnings(d.price_history)
        # 基本面 (info 可能慢/不穩,包起來)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        d.trailing_eps = _f(info.get("trailingEps"))
        d.shares = _f(info.get("sharesOutstanding"))
        d.revenue_ttm = _f(info.get("totalRevenue"))
        # yfinance 的 dividendYield 欄位格式不穩(有時 0.46 代表 0.46%)。
        # 改用 trailingAnnualDividendYield(乾淨的小數,如 0.0002),較可靠;
        # 退而求其次用 年化股利金額 / 股價。
        dy = _f(info.get("trailingAnnualDividendYield"))
        if dy is None:
            rate = _f(info.get("dividendRate"))
            if rate and d.price:
                dy = rate / d.price
        if dy is not None:
            d.dividend_yield = dy if dy < 1 else dy / 100.0  # 保險:>1 必是百分數誤填
        d.annual_dividend = _f(info.get("trailingAnnualDividendRate")) or _f(info.get("dividendRate"))
        cur = info.get("currency")
        if cur:
            d.currency = cur
        if d.per is None and d.price and d.trailing_eps and d.trailing_eps > 0:
            d.per = round(d.price / d.trailing_eps, 2)
        # 近似本益比河流圖: 用歷史股價 / 現行 EPS (假設 EPS 不變,標示為近似)
        if d.trailing_eps and d.trailing_eps > 0:
            d.per_history = [(dt, round(c / d.trailing_eps, 2)) for dt, c in d.price_history]
            d.per_history_approx = True
        if not d.price:
            d.error = "yfinance 取得不到價格(可能被限流或代號錯誤)。"
    except Exception as e:
        d.error = f"yfinance 抓取失敗: {type(e).__name__}: {str(e)[:140]}"
    return d


def _f(x):
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _tw_eod_hour(providers_cfg: dict) -> int:
    """從 `providers_cfg` 讀 `tw_eod_hour`(可選覆寫,預設 18,寫死在 code、不動
    config.yaml)。防呆(工單 013 reviewer 修正包 P2-1):快取讀取路徑必須「永不
    往外炸」——型別不對(None、"18:00" 這種非數字字串)或數值超出合理的
    0–23 時範圍,一律靜默回退預設 18,不讓一個設定失誤變成整個 fetch() 掛掉。"""
    try:
        h = int(providers_cfg.get("tw_eod_hour", 18))
    except (TypeError, ValueError):
        return 18
    if not (0 <= h <= 23):
        return 18
    return h


# --------------------------------------------------------------------------- #
#  對外統一入口
# --------------------------------------------------------------------------- #
def fetch(stock_cfg: dict, providers_cfg: dict, history_years: int, use_cache: bool = True) -> StockData:
    market = stock_cfg["market"].upper()
    ticker = str(stock_cfg["ticker"])
    name = stock_cfg.get("name", ticker)
    cache_min = float(providers_cfg.get("cache_minutes", 15))

    if use_cache:
        if market == "TW":
            # 台股是日收盤(EOD)資料,一天只變一次:用「有沒有跨過收盤更新
            # 邊界」判斷新鮮度,取代固定 15 分 TTL(工單 013),把活躍使用時
            # 整天重抓不會變的資料降下來。tw_eod_hour 可由 providers_cfg 覆寫
            # (預設 18,不動 config.yaml)。
            loaded = _load_cache_raw(market, ticker)
            if loaded is not None:
                cached, fetched_at = loaded
                tw_eod_hour = _tw_eod_hour(providers_cfg)
                if _tw_cache_fresh(fetched_at, eod_hour=tw_eod_hour, floor_minutes=cache_min):
                    return cached
        else:
            cached = _load_cache(market, ticker, cache_min)
            if cached is not None:
                return cached

    if market == "TW":
        # 金鑰優先序:config(含使用者自帶金鑰)> 環境變數/Secret(部署者)。
        # 這樣使用者在側邊欄填的金鑰會覆蓋部署者的,沒填則用部署者 env 金鑰。
        token = providers_cfg.get("finmind_token") or os.environ.get("FINMIND_TOKEN", "")
        method = (stock_cfg.get("valuation") or {}).get("method", "")  # 決定要不要打 PER/配息
        data = fetch_tw(ticker, name, history_years, token, method)
    elif market == "US":
        # 有 Finnhub 金鑰 → 用 Finnhub 取即時報價(雲端機房 IP 也行);否則 yfinance。
        # 金鑰優先序:config(含使用者自帶)> 環境變數/Secret(部署者)。
        fh_key = providers_cfg.get("finnhub_api_key") or os.environ.get("FINNHUB_API_KEY", "")
        data = (fetch_us_finnhub(ticker, name, history_years, fh_key)
                if fh_key else fetch_us(ticker, name, history_years))
    elif market == "INTL":             # 全球型 ETF(倫敦 .L):Finnhub 免費不支援 → 走 yfinance
        data = fetch_us(ticker, name, history_years)
    else:
        data = StockData(ticker=ticker, market=market, name=name, error=f"未知市場 {market}")

    if data.ok():
        _save_cache(data)
        return data
    # 線上抓取失敗(雲端常因 yfinance 被限流)→ 退回任何「過期」快取保命:
    # 顯示上次成功抓到的資料(標記為過期快取),好過整檔變「錯誤」消失。
    # 註:此救援刻意「不看」上面的 use_cache 旗標——use_cache 只決定要不要讀
    # 「新鮮」快取,線上全源失敗時的過期快取保命是可用性保底,一律生效。
    # 未來若要做「強制刷新、失敗就直接報錯」的語意,請另開參數,不要在這裡動手腳。
    stale = _load_cache(market, ticker, None)   # None = 無視 TTL
    if stale is not None and stale.ok():
        if "快取" not in (stale.source or ""):
            stale.source = f"{stale.source}(過期快取)"
        return stale
    return data


# --------------------------------------------------------------------------- #
#  匯率
# --------------------------------------------------------------------------- #
def usd_twd(fallback: float = 32.0) -> float:
    """嘗試線上取 USD/TWD,失敗回退設定值。"""
    try:
        j = _http_get_json("https://api.exchangerate-api.com/v4/latest/USD", timeout=10)
        rate = j.get("rates", {}).get("TWD")
        if rate:
            return float(rate)
    except Exception:
        pass
    return float(fallback)

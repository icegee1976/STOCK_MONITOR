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
    month_revenue: list = field(default_factory=list)  # 018:[(YYYY-MM, 營收), ...],僅 TW+derive 標的抓取
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


# --------------------------------------------------------------------------- #
#  台股第二資料源備援: TWSE(上市)/ TPEx(上櫃)官方 OpenAPI(工單 017)
#
#  只在 fetch_tw 的 FinMind 例外分支(價格抓取失敗,見下方)被呼叫——FinMind
#  成功路徑完全不觸碰這裡,呼叫數見 docs/api-budget.md。兩者皆免金鑰、一次呼叫
#  回「全市場當日收盤行情」,用來覆蓋 watchlist 內所有台股標的,不對個股逐檔
#  查詢(省呼叫數)。全市場回應在 process 記憶體內 memoize(重用工單 013
#  `_tw_cache_fresh` 的 EOD 邊界規則判斷這份 memo 還新不新鮮,不發明新規則),
#  同一輪(screen/watch 跑多檔)最多各打一次;抓取失敗時保留舊 memo(可能是
#  None)原樣回傳,不因一次暫時性失敗清空稍早才抓到的資料。不落地寫新檔案
#  ——memo 純粹是模組層 dict,blob/history_store 兩層沿用既有設計。
#
#  實測欄位(2026-08-19,見工單 017 REPORT,mock 以此為準):
#    TWSE STOCK_DAY_ALL → list[dict](非包一層 status/data,直接是陣列),鍵
#      Date(民國年 "1150818" 無分隔)/Code/Name/ClosingPrice(乾淨數字字串,
#      無千分位逗號,實測 1378 檔無一例外)/OpeningPrice/HighestPrice/
#      LowestPrice/TradeVolume/TradeValue/Change/Transaction。
#    TPEx tpex_mainboard_daily_close_quotes → list[dict](同樣直接是陣列,
#      10000+ 列,含個股/ETF/債券),鍵 Date(格式同上)/SecuritiesCompanyCode/
#      CompanyName/Close(同樣無逗號;當日無成交會是 " ---" 這種帶前導空白的
#      佔位字串,需防呆跳過)/Open/High/Low/Average/TradingShares/...。
#    兩端點數值字串仍統一經 `_tw_official_num` 防呆(去逗號/空白,"---" 等
#    佔位符 → None)——實測雖無逗號案例,但官方資料源在不同資料集/未來格式
#    調整下仍可能出現千分位逗號,保留這層防呆並在測試涵蓋(工單 017 SPEC
#    明文要求的案例)。
# --------------------------------------------------------------------------- #
TWSE_STOCK_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_MAINBOARD_DAILY_CLOSE_QUOTES = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"

_TWSE_SNAPSHOT_MEMO: dict = {"data": None, "fetched_at": None, "failed_at": None}
_TPEX_SNAPSHOT_MEMO: dict = {"data": None, "fetched_at": None, "failed_at": None}

# 全故障時的「負向 memo」冷卻窗口(工單 017 reviewer 修正包 G3b)。沿用 013 的
# floor 概念(短時間內不重打)——不是真的判斷 EOD 邊界,單純冷卻,見 `_market_snapshot`。
_FALLBACK_NEGATIVE_MEMO_MINUTES = 15.0


def _reset_snapshot_memos() -> None:
    """測試輔助(工單 017 reviewer 修正包 G7):把 `_TWSE_SNAPSHOT_MEMO`/
    `_TPEX_SNAPSHOT_MEMO` 兩個 process 記憶體 memo(含 `failed_at`)重置回初始
    狀態。供測試在 setUp/tearDown 呼叫,避免同一 process 內的測試互相汙染。
    **注意**:任何測試檔只要會驅動 `fetch_tw` 的 FinMind 失敗分支(進而觸發這裡
    的模組層 memo,即使目標端點本身也被 mock 成失敗),就有可能寫入這兩個
    module-level dict;若該測試檔沒有呼叫這個函數重置,理論上會被其他測試檔
    (例如本檔 `tests/test_twse_fallback.py`)的殘留狀態影響,或反過來汙染其他
    測試檔。目前只有本檔會斷言 memo 相關行為,其餘既有測試檔對此免疫(不檢查
    memo 狀態),但未來新增這類測試檔時請務必自行呼叫這個函數重置。"""
    for memo in (_TWSE_SNAPSHOT_MEMO, _TPEX_SNAPSHOT_MEMO):
        memo["data"] = None
        memo["fetched_at"] = None
        memo["failed_at"] = None


def _tw_official_num(s) -> float | None:
    """TWSE/TPEx 官方數值字串 → float。去除千分位逗號與前後空白;無成交的佔位
    字串(如 "---"、" ---"、"")或非正值一律 None(呼叫端跳過該列,不誤當
    0 元現價)。工單 017 reviewer 修正包 G8:額外擋 inf/-inf/nan(`float("inf")`/
    `float("nan")` 這種字串本身可以被 `float()` 成功解析,不會走進 ValueError
    分支,但拿來當股價會讓下游比較/運算出現無法預期的結果,必須明確排除)。"""
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if not s or s.strip("-") == "":
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if not math.isfinite(v):
        return None
    return v if v > 0 else None


def _roc_date_to_iso(s) -> str | None:
    """民國年日期字串(實測 "1150818" 無分隔格式;寬鬆相容 "115/08/18" 等含
    分隔符變體,解析時先濾掉非數字字元)→ 西元 "YYYY-MM-DD"。格式無法辨識
    (非 6~7 位數字,或組出不合法日期)一律 None,呼叫端跳過該列。

    工單 017 reviewer 修正包 G8:
    * 只接受 **ASCII** 數字字元(`"0"`~`"9"`,用字面集合比對,不用
      `str.isdigit()`)——`str.isdigit()` 對全形數字(如 `"１１５"`)、其他
      Unicode 數字字元也會回傳 True,`int()` 恰好也吃得下這些字元,兩者疊加
      會讓全形輸入被「意外正確」解析,而非明確拒絕;改成只接受 ASCII 後,
      全形輸入會被濾成空字串或錯誤長度而回傳 None,行為更可預期。
    * 已知取捨(不修正,僅記錄):本函式用「先濾掉所有非數字字元、剩下的
      digits 依固定寬度(6 或 7)切 day/month/roc_year」的策略解析——這對
      「月/日未補零」的分隔符變體(例如 `"115/8/18"`,濾掉分隔符後剩
      `"115818"` 只有 6 碼)**不支援**,會被誤判成 6 碼格式(2 位民國年)
      而算出離譜的西元年份;不過後面的 `datetime(year, month, day)` 驗證
      多半會因為 month/day 超出合法範圍而擋下、回傳 None(安全失敗,不會
      靜默給出錯誤但看似合理的日期)。真正實測到的兩種格式(`"1150818"`
      無分隔、`"115/08/18"` 有補零分隔)都不受此限制影響。
    """
    if not s:
        return None
    digits = "".join(ch for ch in str(s) if ch in "0123456789")
    if len(digits) not in (6, 7):
        return None
    day, month, roc_year = digits[-2:], digits[-4:-2], digits[:-4]
    try:
        year = int(roc_year) + 1911
        datetime(year, int(month), int(day))   # 驗證是合法日期,否則視為格式不明
    except ValueError:
        return None
    return f"{year:04d}-{month}-{day}"


def _parse_twse_snapshot(raw) -> dict:
    """TWSE STOCK_DAY_ALL 原始回應(list[dict])→ {ticker: (price, iso_date)}。
    單列解析失敗(缺鍵/格式異常)不拖垮整批,直接跳過該列。"""
    out = {}
    if not isinstance(raw, list):
        return out
    for row in raw:
        try:
            code = row.get("Code")
            price = _tw_official_num(row.get("ClosingPrice"))
            date = _roc_date_to_iso(row.get("Date"))
            if code and price and date:
                out[str(code)] = (price, date)
        except Exception:
            continue
    return out


def _parse_tpex_snapshot(raw) -> dict:
    """TPEx 主板日行情原始回應(list[dict])→ {ticker: (price, iso_date)}。"""
    out = {}
    if not isinstance(raw, list):
        return out
    for row in raw:
        try:
            code = row.get("SecuritiesCompanyCode")
            price = _tw_official_num(row.get("Close"))
            date = _roc_date_to_iso(row.get("Date"))
            if code and price and date:
                out[str(code)] = (price, date)
        except Exception:
            continue
    return out


def _market_snapshot(memo: dict, url: str, parse_fn, now=None):
    """`memo`(模組層 dict,原地更新:`_TWSE_SNAPSHOT_MEMO` 或
    `_TPEX_SNAPSHOT_MEMO`)的 get-or-fetch。成功新鮮度沿用工單 013
    `_tw_cache_fresh` 的 EOD 邊界規則(不發明新規則)——同一交易日內重複呼叫
    共用同一份 memo,跨過收盤更新邊界才重打。`now` 可注入(測試釘假時鐘),
    預設 `_now_tpe()`。

    工單 017 reviewer 修正包 G3(P1-3 根修 + P2-1):
    * (a) 抓到的資料若不是 dict 或是空 dict(`_parse_twse_snapshot`/
      `_parse_tpex_snapshot` 在輸入不是預期的 list、或全部列都解析失敗時會
      回傳 `{}`),視為「這次抓取沒有可用結果」,跟例外一樣走失敗分支——
      不能被記成「成功」memo(空 dict 一旦被當成功快取,`_tw_cache_fresh`
      的 EOD 邊界會讓後面整個交易日都直接回傳這個空結果,不會再嘗試)。
    * (b) 失敗(例外、或 (a) 判定的空/非 dict 結果)時記
      `memo["failed_at"]=now`;若 `failed_at` 存在且距 `now` 未滿
      `_FALLBACK_NEGATIVE_MEMO_MINUTES`(15 分,沿用 013 的「安全地板」概念,
      不是 EOD 邊界判斷),直接回傳現有的 `memo["data"]`(可能是 None)而
      **不重打**——全市場故障時,同一輪的第一檔付出真正的探測成本(含
      `_retry` 三次重試),之後 15 分鐘內的其他檔位直接短路,不會每檔各自
      重打兩個端點(這是 P1-3 對應的真實重現:全網路故障時每檔都各自重試
      兩端點,單輪最壞可以疊加到近一小時)。成功一次後會清掉 `failed_at`。
    * (c) 呼叫 `_http_get_json` 明確帶 `timeout=10`(而非預設 25 秒)——同樣
      是為了壓低「全故障但連線是掛著、不是立刻 refuse」這種最壞情境下,
      單次探測的耗時上限(10 秒 × `_retry` 三次嘗試,遠低於 25 秒 × 3 次)。

    已知限制(P2-4,記錄不修):這裡的 `_tw_cache_fresh` 呼叫用預設
    `eod_hour=18`/`floor_minutes=15.0`,**不吃** `providers_cfg` 的
    `tw_eod_hour`/`cache_minutes` 覆寫(那兩個設定只影響 `fetch()` 開頭的
    blob 層新鮮度判斷)。也就是說,即使使用者把 `tw_eod_hour` 設成別的時間,
    這裡的 TWSE/TPEx 全市場 memo 仍然用 18:00 當邊界。影響範圍很小(memo 只是
    「同一輪內不要重打」的效能優化,不是資料正確性的一部分——邊界設定不同
    頂多讓 memo 早一點或晚一點重打,不影響備援本身查到的官方 EOD 價格是否
    正確),故不在本工單修正,留給未來若真的有需求再處理。
    """
    now = now if now is not None else _now_tpe()
    if memo["data"] is not None and memo["fetched_at"] is not None \
            and _tw_cache_fresh(memo["fetched_at"], now):
        return memo["data"]
    failed_at = memo.get("failed_at")
    if failed_at is not None and _cache_age_minutes(failed_at, now) < _FALLBACK_NEGATIVE_MEMO_MINUTES:
        return memo["data"]
    try:
        data = parse_fn(_http_get_json(url, timeout=10))
        if not isinstance(data, dict) or not data:
            raise ValueError("empty or non-dict market snapshot")
    except Exception:
        memo["failed_at"] = now
        return memo["data"]
    memo["data"] = data
    memo["fetched_at"] = now
    memo["failed_at"] = None
    return data


def _twse_fallback_quote(ticker: str, now=None):
    """TWSE STOCK_DAY_ALL 備援查價。回傳 `(price, price_date) | None`。"""
    snap = _market_snapshot(_TWSE_SNAPSHOT_MEMO, TWSE_STOCK_DAY_ALL, _parse_twse_snapshot, now)
    return snap.get(str(ticker)) if snap else None


def _tpex_fallback_quote(ticker: str, now=None):
    """TPEx 主板日行情備援查價。回傳 `(price, price_date) | None`。"""
    snap = _market_snapshot(_TPEX_SNAPSHOT_MEMO, TPEX_MAINBOARD_DAILY_CLOSE_QUOTES,
                             _parse_tpex_snapshot, now)
    return snap.get(str(ticker)) if snap else None


def _assemble_tw_fallback(ticker: str, name: str, price: float, price_date: str,
                           source_label: str, requested_start: str) -> StockData:
    """FinMind 失敗、TWSE/TPEx 備援查到現價後組裝 StockData。`price`/`price_date`
    一律用官方 EOD(最新、最權威),不被下面組裝出來的歷史序列覆蓋。不呼叫
    FinMind(已經失敗),因此不走 `_sync_and_assemble` 的全量/增量骨架。

    歷史序列來源(工單 017 reviewer 修正包 G1a,P1-1 根修之一):**store 優先,
    store 空/不可用時退回上一份 blob 快取**——
    * store(`history_store`)有價格資料 → 用 store(原始 FinMind 值),這裡
      才需要對它跑 `_back_adjust_tw` 做拆股/反分割還原。
    * store 沒有(從未成功抓過 / sqlite 不可用)→ 改用 `_load_cache_raw`
      (無視新鮮度)讀「上一份成功寫入的 blob」。blob 裡的 price_history/
      div_history/per_history 是 `fetch_tw` 存進 blob 前**已經**跑過
      `_back_adjust_tw` 的最終序列——這裡**絕對不能再對它跑一次**
      `_back_adjust_tw`,否則等於對已經還原過的價格再套一次還原因子(二次
      還原,數值會被弄壞)。store/blob 兩條路徑刻意分開處理,不共用同一段
      還原程式碼。
    目的:備援結果嚴格 ≥ 舊有的 stale-rescue(官方新價 + 最佳可得歷史),
    yield_band/price_band(auto)在「store 空但有舊 blob」時不再變成
    `ValuationError`;真的兩者都沒有時,才會自然走到既有 `ValuationError`
    路徑(不是這裡的職責)。

    尺度護欄(G2,P1-2):組裝完成後若官方現價與序列尾端的比值跳動異常(疑似
    拆股或資料不一致),整組捨棄三個歷史序列,避免用「錯尺度」的歷史算出
    誤導的分類/機率(寧可誠實 ValuationError,不要假訊號)。
    """
    d = StockData(ticker=ticker, market="TW", name=name, currency="TWD",
                  source=source_label, price=price, price_date=price_date)

    store_price_rows = history_store.get_price(CACHE_DIR, "TW", ticker, start_date=requested_start)
    per_rows = None            # 只有 store 分支才有 (date, per, dividend_yield) 三元組
    blob_data = None           # 只有 blob 分支才會設值(供下面 PER 護欄回退用)
    if store_price_rows:
        div_rows = history_store.get_dividend(CACHE_DIR, "TW", ticker, start_date=requested_start) or []
        d.price_history, d.div_history = _back_adjust_tw(list(store_price_rows), list(div_rows))
        per_rows = history_store.get_per(CACHE_DIR, "TW", ticker, start_date=requested_start) or []
        if per_rows:
            d.per_history = [(dt, p) for dt, p, dy in per_rows if p not in (None, 0)]
    else:
        blob_loaded = _load_cache_raw("TW", ticker)
        blob_data = blob_loaded[0] if blob_loaded is not None else None
        if blob_data is not None:
            d.price_history = list(blob_data.price_history or [])
            d.div_history = list(blob_data.div_history or [])
            d.per_history = list(blob_data.per_history or [])

    # G2:官方現價與本地歷史尾端「尺度脫鉤」護欄。門檻 0.6/1.7 刻意複製自
    # `_back_adjust_tw` 判斷「真正整數倍拆股/反分割」的同一組數字(該函數內
    # `if r < 0.6 or r > 1.7:` 那行)——沒有讓 `_back_adjust_tw` 改成引用共用
    # 常數,是為了不去動 FinMind 成功路徑的既有程式碼(byte-untouched 紅線);
    # 這裡刻意複製而非重構共用,兩處數字之後如需調整必須同步修改。
    if d.price_history:
        last_close = d.price_history[-1][1]
        if last_close and d.price:
            r = d.price / last_close
            if r < 0.6 or r > 1.7:
                d.price_history, d.div_history, d.per_history = [], [], []
                d.quality_warnings.append(
                    f"官方現價({d.price})與本地歷史尾端({last_close})跳動異常"
                    f"(比值≈{r:.2f}),疑似拆股或資料不一致,已捨棄歷史序列以避免誤判。"
                )

    cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    if d.div_history:
        d.annual_dividend = round(sum(c for ex, c in d.div_history if ex > cutoff), 4)
        if d.annual_dividend and d.price:
            d.dividend_yield = d.annual_dividend / d.price

    # PER/trailing_eps(可能覆蓋上面算出的 dividend_yield,較新/官方來源優先)
    # ——同工單 014 reviewer 修正包 F3 護欄:PER 最新一筆與現價日相差 >10 天
    # 就不派生,避免拿過期 PER 配上今天的官方 EOD 現價算出誤導的隱含估值。
    if per_rows:  # store 分支:有原始三元組,沿用既有 F3 邏輯
        last_dt, last_p, last_dy = per_rows[-1]
        if d.per_history and _days_between(last_dt, d.price_date) <= 10:
            d.per = float(last_p) if last_p else None
            if last_dy:
                d.dividend_yield = float(last_dy) / 100.0
            if d.per and d.price:
                d.trailing_eps = round(d.price / d.per, 4)
    elif blob_data is not None and d.per_history:
        # blob 分支:blob 沒有保留 FinMind PER 表原始的三元組,只有 (date, per)
        # 河流圖點位;沿用 blob 當時已經算好的 per/trailing_eps/dividend_yield,
        # 用 blob per_history 最後一筆的日期(而不是 blob 當時的舊 price_date)
        # 對「現在」的官方 EOD price_date 做同一套 10 天新鮮度護欄。
        last_dt = d.per_history[-1][0]
        if _days_between(last_dt, d.price_date) <= 10:
            d.per = blob_data.per
            d.trailing_eps = blob_data.trailing_eps
            if blob_data.dividend_yield:
                d.dividend_yield = blob_data.dividend_yield

    d.quality_warnings = d.quality_warnings + _series_quality_warnings(d.price_history)
    d.quality_warnings.append(
        f"FinMind 抓取失敗,現價已切換至{source_label}官方 EOD;"
        "歷史序列(price_history/per_history/div_history)組裝自本地快取,可能不完整。"
    )
    return d


# --------------------------------------------------------------------------- #
#  台股月營收護欄(工單 018):FinMind TaiwanStockMonthRevenue,獨立輕量 JSON
#  快取(TTL 7 天,月頻資料——不沿用工單 013 的 TW EOD-aware/天級規則),只給
#  `fetch()` 判斷為 market==TW 且 `valuation.derive` 存在的標的抓取(現況約
#  1-6 檔,見 docs/api-budget.md 呼叫評估);不動 history_store schema(migration
#  政策屬工單 016,未定前不加表)。失敗一律靜默回空列表——估價鏈完全不受
#  影響,只是少了這個護欄檢查(aimonitor/valuation.py compute_zones 對空/
#  不足 12 個月一律回傳 revenue_check=None,不產生 error,不影響 zones)。
#
#  實測欄位(2026-08-20 真實 FinMind 呼叫,2330,見工單 018 REPORT):
#    TaiwanStockMonthRevenue → list[dict],鍵 date("YYYY-MM-01",**注意**:這是
#    「發布月」= 營收所屬月的次月,不是營收所屬月本身,不可直接當月份用)/
#    stock_id/country/revenue(新台幣「元」原始數值,與 watchlist.yaml 的
#    `derive.base_revenue` 同單位,可直接比較,不是千元)/revenue_month
#    (1-12,營收「所屬」月)/revenue_year(營收「所屬」年)/create_time(發布
#    時間,較早期資料可能是空字串)。真正的營收所屬月份必須用
#    revenue_year+revenue_month 組回 "YYYY-MM",不能用 date 欄位——本節與
#    離線測試皆以此為準。
# --------------------------------------------------------------------------- #
MONTHREV_CACHE_TTL_MINUTES = 7 * 24 * 60  # 7 天(月頻資料,約每月10日更新)
MONTHREV_WINDOW_MONTHS = 30               # B 法 YoY 需 24 個月,30 留緩衝


def _monthrev_cache_path(ticker: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = str(ticker).replace("/", "_")
    return os.path.join(CACHE_DIR, f"TW_{safe}_monthrev.json")


def _monthrev_start_date(now=None) -> str:
    """近 `MONTHREV_WINDOW_MONTHS` 個月的窗起點("YYYY-MM-01")。用整數月運算
    (而非天數估算),避免大小月造成窗口忽多忽少。"""
    now = now if now is not None else _now_tpe()
    total = now.year * 12 + (now.month - 1) - MONTHREV_WINDOW_MONTHS
    y, m0 = divmod(total, 12)
    return f"{y:04d}-{m0 + 1:02d}-01"


def _load_monthrev_cache(ticker: str, now=None):
    """讀月營收快取(TTL 7 天),回傳 `[(YYYY-MM, revenue), ...]` 或 `None`
    (不存在/損毀/過期)。刻意獨立於 `_load_cache_raw`(主 blob 快取)——月營收
    是月頻資料,不該跟著台股 EOD-aware(工單 013,天級)新鮮度規則走,用同一套
    `_parse_fetched_at`/`_cache_age_minutes` 時鐘慣式,但門檻是自己的 7 天。"""
    p = _monthrev_cache_path(ticker)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            blob = json.load(f)
        fetched_at = _parse_fetched_at(blob["_fetched_at"])
        if _cache_age_minutes(fetched_at, now) > MONTHREV_CACHE_TTL_MINUTES:
            return None
        return [(row[0], row[1]) for row in blob.get("rows", [])]
    except Exception:
        return None


def _save_monthrev_cache(ticker: str, rows: list) -> None:
    try:
        blob = {"rows": [[m, r] for m, r in rows],
                "_fetched_at": datetime.now(timezone.utc).isoformat()}
        with open(_monthrev_cache_path(ticker), "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)
    except Exception:
        pass


def _fetch_month_revenue_tw(ticker: str, token: str, now=None) -> list:
    """真正打 FinMind TaiwanStockMonthRevenue。回傳 `[(YYYY-MM, revenue), ...]`
    依月份升冪、已去重(同月多筆取後者)。例外原樣往外拋,由呼叫端
    `fetch_month_revenue` 決定要不要吞(統一的靜默失敗策略在那裡,不在這裡)。"""
    start = _monthrev_start_date(now)
    raw = _finmind("TaiwanStockMonthRevenue", ticker, start, token)
    out = {}
    for r in raw:
        y, m, rev = r.get("revenue_year"), r.get("revenue_month"), r.get("revenue")
        if y is None or m is None or rev in (None, ""):
            continue
        try:
            y, m, rev = int(y), int(m), float(rev)
        except (TypeError, ValueError):
            continue
        if not (1 <= m <= 12) or rev <= 0:
            continue
        out[f"{y:04d}-{m:02d}"] = rev
    return [(k, out[k]) for k in sorted(out)]


def fetch_month_revenue(ticker: str, token: str = "", now=None) -> list:
    """月營收抓取入口(工單 018):`_load_monthrev_cache` 命中就不打 API;否則
    呼叫 FinMind,成功才寫快取(失敗不寫,下次呼叫仍會重試,與主 blob 快取
    `if data.ok(): _save_cache(...)` 同一套「只有成功才快取」哲學)。任何失敗
    (網路/解析/FinMind 額度用罄等)一律靜默回空列表——只有 `fetch_tw` 在
    `need_monthrev=True`(即 `fetch()` 判斷 TW+derive)時才會呼叫這個函數,
    不影響非 derive 標的的呼叫數(見下方 `fetch_tw`/`fetch()` 接線)。"""
    now = now if now is not None else _now_tpe()
    cached = _load_monthrev_cache(ticker, now)
    if cached is not None:
        return cached
    try:
        rows = _fetch_month_revenue_tw(ticker, token, now)
    except Exception:
        return []
    if rows:
        _save_monthrev_cache(ticker, rows)
    return rows


def _needs_month_revenue(stock_cfg: dict) -> bool:
    """工單 018(修正包 H3):僅 TW 市場、`method=="pe_band"`、且含
    `valuation.derive` 區塊的標的才需要月營收護欄(現況約 1-6 檔)——SPEC 的
    計算範圍本就限定 pe_band+derive(見 valuation.compute_zones 只在 pe_band
    分支才用得到 revenue_check),補上 method 條件避免未來 derive 出現在非
    pe_band 假設區塊時白白多打一次不會被用到的 FinMind 呼叫。與其他市場/
    無 derive/非 pe_band 標的的呼叫數完全無關(0 新增)。"""
    if str(stock_cfg.get("market", "")).upper() != "TW":
        return False
    val = stock_cfg.get("valuation") or {}
    if val.get("method") != "pe_band":
        return False
    return bool(val.get("derive"))


def fetch_tw(ticker: str, name: str, years: int, token: str = "", method: str = "",
             need_monthrev: bool = False) -> StockData:
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
        # 工單 017:FinMind 價格抓取失敗 → 退而求其次試 TWSE(上市)/TPEx(上櫃)
        # 官方備援(免金鑰、全市場單次呼叫,process 內 memoize,見上方章節)。
        # 找到 → 備援成功,回傳全新組裝的 StockData(ok()==True,不沿用上面
        # 剛設的 d.error,讓 fetch() 的 `if data.ok(): _save_cache(...)` 正常
        # 接手、blob/EOD 新鮮度自然生效,但 G1b 之後備援結果本身不會被存進
        # blob,見 fetch() 的說明)。兩端點都沒有這個代號(或都失敗)→ 維持
        # 現狀:原樣回傳帶 error 的 d,交給 fetch() 既有的過期快取保命
        # (stale-rescue)。
        # 工單 017 reviewer 修正包 G6(P3-1):`_twse_fallback_quote`/
        # `_tpex_fallback_quote` 本身已經在 `_market_snapshot` 內部把 HTTP/
        # 解析例外都吞掉,理論上不會再往外拋;這裡額外包一層 try/except 是
        # 防禦性寫法(例如 `snap.get(str(ticker))` 這類查找本身若因為極端邊角
        # 情況拋出未預期例外),確保任何例外都不逸出、一律維持原本的 error
        # 路徑 `return d`,不讓 fetch_tw 整個崩掉。備援組裝
        # (`_assemble_tw_fallback`)本身若意外拋例外一樣吞掉退回現狀路徑。
        fb = None
        source_label = ""
        try:
            fb = _twse_fallback_quote(ticker)
            source_label = "TWSE(備援)"
            if fb is None:
                fb = _tpex_fallback_quote(ticker)
                source_label = "TPEx(備援)"
        except Exception:
            fb = None
        if fb is not None:
            price, price_date = fb
            try:
                return _assemble_tw_fallback(ticker, name, price, price_date, source_label, start)
            except Exception:
                pass
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
    # 018:月營收護欄資料。只有 `need_monthrev=True`(fetch() 判斷 TW+derive)才
    # 呼叫;獨立快取層見上方 fetch_month_revenue,失敗一律靜默,d.month_revenue
    # 維持預設空列表(估價照常,只是少了這個護欄檢查)。放在函式最尾端,
    # 只在價格抓取成功的主路徑執行(上面 FinMind 例外分支各自 return,不會走到
    # 這裡;TWSE/TPEx 備援路徑同理不觸發,現況低優先級,見工單 018 REPORT)。
    if need_monthrev:
        try:
            d.month_revenue = fetch_month_revenue(ticker, token)
        except Exception:
            pass
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
        data = fetch_tw(ticker, name, history_years, token, method,
                         need_monthrev=_needs_month_revenue(stock_cfg))  # 018:僅 TW+derive
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
        # 工單 017 reviewer 修正包 G1b(P1-1 根修之二):台股 TWSE/TPEx 備援結果
        # **不寫入 blob**。約定:`_assemble_tw_fallback` 一律把 `source` 標成含
        # 「(備援)」字樣(見 `_twse_fallback_quote`/`_tpex_fallback_quote` 呼叫端
        # 設的 `source_label`,目前是 "TWSE(備援)"/"TPEx(備援)");這裡用字串
        # 內容判斷是否跳過寫入,不是新增欄位——好處是零風險延伸既有的
        # StockData(不必改 dataclass 欄位、不必動 blob 序列化格式),代價是
        # 「約定」而非型別保證,未來任何新的資料來源如果也想要「成功但不快取」
        # 的語意,必須遵守同一個「source 含 (備援)」的字串慣例,或請另開工單
        # 把這裡改成更嚴謹的機制(例如 StockData 新增明確欄位)。
        #
        # 原因(對應 reviewer 發現的 P1-1 真實重現:0056 的 blob 被降級快照
        # 覆蓋,之後 auto 類估價變 ValuationError 且救不回來):備援結果的
        # 歷史序列品質通常不如完整 FinMind 序列(即使有 G1a 的 store/blob 退援,
        # 也可能還是比不上「FinMind 這一輪真的成功時」的完整度)。如果照舊寫進
        # blob,台股 EOD-aware 新鮮度(工單 013)會把這份「降級快照」當成新鮮
        # 資料釘住,直到下一個平日 18:00 邊界(最長可達 22~71 小時)才會重新
        # 嘗試 FinMind——這段期間即使 FinMind 早就恢復正常,使用者也只能看到
        # 這份降級結果,完整的舊 blob(可能還留有更好的歷史)也已經被覆蓋掉、
        # 回不去了。不寫入 blob 之後:(1) 舊的完整 blob(如果有)永遠不會被
        # 降級快照蓋掉,下次 store 也空時 G1a 還能繼續退回這份好資料;
        # (2) 因為 blob 的 `_fetched_at` 沒有被更新,下一輪 `fetch()` 的
        # TW 分支新鮮度判斷會看到「舊」時間戳,FinMind 一恢復正常就會在下一輪
        # 立刻被重新嘗試並覆蓋回正常路徑,不會被 EOD 新鮮度多釘住一整個交易日
        # 甚至更久。stale-rescue 區塊(下面)完全不受影響,原樣沿用。
        if "(備援)" not in (data.source or ""):
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

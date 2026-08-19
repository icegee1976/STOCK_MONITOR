"""本地歷史資料耐久儲存(工單 014,候選 B 核心)。

目的:台股在 blob 快取(providers.py 的 `.cache/TW_xxx.json`,含 013 的
EOD-aware 新鮮度)過期後,不必整段重抓 `history_years` 年,只抓「上次之後的
增量」——這一層負責把每次成功抓到的 FinMind 原始資料**耐久**存起來(跨進程、
跨 blob 快取過期都不會消失),讓 `providers.fetch_tw` 可以用更小的 payload
補齊完整序列。美股/INTL 不做增量(見 `replace_us_snapshot` docstring 的原因),
只當成全量快照的耐久備份用(讀路徑完全不依賴這裡)。

設計原則(對照工單 014 SPEC D1):
* 純儲存層,**不 import `aimonitor.providers`**——避免循環 import(providers.py
  要 import 這個模組來呼叫)。所有公開函數的第一個參數都是顯式 `cache_dir: str`,
  由呼叫端(`providers.py`)傳自己模組全域的 `CACHE_DIR`。這個「顯式傳入」的
  late-binding 特性剛好讓測試沿用既有慣例 `patch.object(providers, "CACHE_DIR",
  tmpdir)` 時,providers.py 內對 `CACHE_DIR` 的參照自然跟著變、间接讓這裡的
  sqlite 檔案位置自然隔離,不需要另外對這個模組做任何 patch。
* stdlib `sqlite3` 唯一依賴。單檔 `<cache_dir>/history.sqlite3`。WAL 模式、
  每次公開函數呼叫都開一條「用完即關」的獨立連線(with 包裹)——Streamlit
  多執行緒環境下安全,不共用長駐連線物件。
* **全部存 FinMind 原始值(未還原分割/未做任何單位轉換之外的加工)**。拆股/
  反分割還原(`providers._back_adjust_tw`)在讀端「組裝完整序列之後」才執行,
  這裡只負責忠實保存與依日期範圍取回。
* **永不炸原則**:每一個公開函數自己包 try/except,任何 sqlite 例外(檔案
  損毀、鎖定、唯讀、磁碟滿…)一律在函數內部吞掉,讀函數回傳 `None`(不可用,
  和「查得到但真的没资料」的 `[]` 明確區分開)、寫函數回傳 `False`。呼叫端
  (`providers.fetch_tw`)看到 `None`/`False` 就退回「當作沒有這份記錄」處理
  (通常等同全量抓取路徑),絕對不會因為這個模組往外拋例外而讓 fetch 整個崩掉。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

DB_FILENAME = "history.sqlite3"

# dataset 名稱("price"/"per"/"dividend")→ (table, date欄位, value欄位們)。
# 只給模組內部用(max_date 的 SQL 組裝);值皆為寫死常數,不吃外部輸入,無注入疑慮。
_DATASET_TABLES = {
    "price": ("daily_price", "date"),
    "per": ("daily_per", "date"),
    "dividend": ("dividend", "ex_date"),
}


def _db_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, DB_FILENAME)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    # 工單 014 reviewer 修正包 F5(a):蓋 schema 版本章,純粹為未來 migration 鋪路
    # (目前只有一套 schema,不做任何版本判斷/分支邏輯——加這行本身沒有其他作用)。
    conn.execute("PRAGMA user_version=1")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS daily_price ("
        " market TEXT NOT NULL, ticker TEXT NOT NULL, date TEXT NOT NULL,"
        " close REAL, PRIMARY KEY (market, ticker, date))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS daily_per ("
        " market TEXT NOT NULL, ticker TEXT NOT NULL, date TEXT NOT NULL,"
        " per REAL, dividend_yield REAL, PRIMARY KEY (market, ticker, date))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dividend ("
        " market TEXT NOT NULL, ticker TEXT NOT NULL, ex_date TEXT NOT NULL,"
        " cash REAL, PRIMARY KEY (market, ticker, ex_date))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS series_meta ("
        " market TEXT NOT NULL, ticker TEXT NOT NULL, dataset TEXT NOT NULL,"
        " requested_start TEXT, last_success TEXT,"
        " PRIMARY KEY (market, ticker, dataset))"
    )


@contextmanager
def _connect(cache_dir: str):
    """開一條獨立連線,建表(冪等),成功時在 with 區塊結束後 commit,例外時讓
    例外原樣往外拋(不在這裡吞——由每個公開函數自己的 try/except 決定「不可用」
    語意),finally 保證連線一定關閉。呼叫端永遠不會漏關連線或漏 commit。"""
    os.makedirs(cache_dir, exist_ok=True)
    # timeout=2(工單 014 reviewer 修正包 F1):reviewer 用真實檔案鎖重現最差情況
    # 單一 dataset 呼叫可阻塞到 10.1s(sqlite3 預設 timeout=5 疊加重試/佇列效應);
    # 降到 2s 讓「store 暫時不可用」更快 fail fast、交給呼叫端的合併保護(見
    # `fetch_tw`/`_sync_and_assemble`)接手,不讓一次 fetch 被本地鎖卡住太久。
    conn = sqlite3.connect(_db_path(cache_dir), timeout=2)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        _ensure_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  寫入(upsert;INSERT OR REPLACE by PK,天生冪等——同一筆 (market,ticker,date)
#  重複寫入只會覆蓋成同一個值,不會產生重複列)
# --------------------------------------------------------------------------- #
def upsert_price(cache_dir: str, market: str, ticker: str, rows) -> bool:
    """rows: [(date:str, close:float|None), ...]。成功 True,任何例外 → False。"""
    try:
        with _connect(cache_dir) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_price(market,ticker,date,close) "
                "VALUES (?,?,?,?)",
                [(market, ticker, d, c) for d, c in rows],
            )
        return True
    except Exception:
        return False


def upsert_per(cache_dir: str, market: str, ticker: str, rows) -> bool:
    """rows: [(date:str, per:float|None, dividend_yield:float|None), ...]
    (dividend_yield 存 FinMind 原始百分數,不在這裡做 /100 轉換——轉換屬於
    「衍生計算」,留在 providers.fetch_tw 組裝後執行,這裡只管忠實保存)。"""
    try:
        with _connect(cache_dir) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_per(market,ticker,date,per,dividend_yield) "
                "VALUES (?,?,?,?,?)",
                [(market, ticker, d, p, dy) for d, p, dy in rows],
            )
        return True
    except Exception:
        return False


def upsert_dividend(cache_dir: str, market: str, ticker: str, rows) -> bool:
    """rows: [(ex_date:str, cash:float), ...]。"""
    try:
        with _connect(cache_dir) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO dividend(market,ticker,ex_date,cash) "
                "VALUES (?,?,?,?)",
                [(market, ticker, ex, c) for ex, c in rows],
            )
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  讀取(依 date/ex_date 升冪;start_date 給定時只回傳 >= start_date 的列)
#  回傳 None = store 不可用(例外);回傳 [] = store 可用但確實查無資料——
#  呼叫端必須能區分這兩種情況(None 通常代表「當作沒有這份記錄」退回全量抓取)。
# --------------------------------------------------------------------------- #
def get_price(cache_dir: str, market: str, ticker: str, start_date: str | None = None):
    try:
        with _connect(cache_dir) as conn:
            if start_date:
                cur = conn.execute(
                    "SELECT date, close FROM daily_price "
                    "WHERE market=? AND ticker=? AND date>=? ORDER BY date ASC",
                    (market, ticker, start_date),
                )
            else:
                cur = conn.execute(
                    "SELECT date, close FROM daily_price "
                    "WHERE market=? AND ticker=? ORDER BY date ASC",
                    (market, ticker),
                )
            return [(r[0], r[1]) for r in cur.fetchall()]
    except Exception:
        return None


def get_per(cache_dir: str, market: str, ticker: str, start_date: str | None = None):
    try:
        with _connect(cache_dir) as conn:
            if start_date:
                cur = conn.execute(
                    "SELECT date, per, dividend_yield FROM daily_per "
                    "WHERE market=? AND ticker=? AND date>=? ORDER BY date ASC",
                    (market, ticker, start_date),
                )
            else:
                cur = conn.execute(
                    "SELECT date, per, dividend_yield FROM daily_per "
                    "WHERE market=? AND ticker=? ORDER BY date ASC",
                    (market, ticker),
                )
            return [(r[0], r[1], r[2]) for r in cur.fetchall()]
    except Exception:
        return None


def get_dividend(cache_dir: str, market: str, ticker: str, start_date: str | None = None):
    try:
        with _connect(cache_dir) as conn:
            if start_date:
                cur = conn.execute(
                    "SELECT ex_date, cash FROM dividend "
                    "WHERE market=? AND ticker=? AND ex_date>=? ORDER BY ex_date ASC",
                    (market, ticker, start_date),
                )
            else:
                cur = conn.execute(
                    "SELECT ex_date, cash FROM dividend "
                    "WHERE market=? AND ticker=? ORDER BY ex_date ASC",
                    (market, ticker),
                )
            return [(r[0], r[1]) for r in cur.fetchall()]
    except Exception:
        return None


def max_date(cache_dir: str, market: str, ticker: str, dataset: str) -> str | None:
    """該序列目前 store 內最新的 date/ex_date 字串。dataset 不在
    `{"price","per","dividend"}`、查無資料、或 store 不可用 → 一律 None
    (呼叫端統一當作「沒有可用的增量起點」,通常導向全量抓取)。"""
    table_col = _DATASET_TABLES.get(dataset)
    if table_col is None:
        return None
    table, col = table_col
    try:
        with _connect(cache_dir) as conn:
            cur = conn.execute(
                f"SELECT MAX({col}) FROM {table} WHERE market=? AND ticker=?",
                (market, ticker),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  meta:每個 (market,ticker,dataset) 記錄「這次要求的窗起點」與「最後成功時間」,
#  供 providers.fetch_tw 判斷全量 vs 增量(工單 014 D2)。
# --------------------------------------------------------------------------- #
def get_meta(cache_dir: str, market: str, ticker: str, dataset: str):
    """回傳 `{"requested_start": str|None, "last_success": str|None}`;
    無記錄或 store 不可用 → `None`(呼叫端統一視為「首次/新 dataset」→ 全量抓取,
    這正是「永不炸」的價值所在:損毀時自然退化成安全的全量路徑,不需要呼叫端
    另外判斷「是損毀還是真的沒記錄」)。"""
    try:
        with _connect(cache_dir) as conn:
            cur = conn.execute(
                "SELECT requested_start, last_success FROM series_meta "
                "WHERE market=? AND ticker=? AND dataset=?",
                (market, ticker, dataset),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {"requested_start": row[0], "last_success": row[1]}
    except Exception:
        return None


def set_meta(cache_dir: str, market: str, ticker: str, dataset: str,
             requested_start: str, last_success: str) -> bool:
    try:
        with _connect(cache_dir) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO series_meta"
                "(market,ticker,dataset,requested_start,last_success) VALUES (?,?,?,?,?)",
                (market, ticker, dataset, requested_start, last_success),
            )
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  美股/INTL 專用:全量替換快照(工單 014 D3)
# --------------------------------------------------------------------------- #
def replace_us_snapshot(cache_dir: str, market: str, ticker: str, rows) -> bool:
    """單一 ticker 的 `daily_price` 整檔 DELETE+INSERT(同一個 sqlite 交易內——
    `_connect` 只在 with 區塊順利跑完後才 commit,中途任何例外都不會 commit,
    不會出現「刪了但插入失敗」的半殘狀態)。

    **禁止把這個函數用在台股增量路徑**(那是 `upsert_price` 的職責),原因:
    1. `fetch_us`/`fetch_us_finnhub` 呼叫 yfinance 時用 `auto_adjust=True`——
       這個模式下同一天的收盤價會隨「之後」發生的拆股/配息事件被回溯改寫
       (例如某檔股票在 T+30 天拆股,重新抓一次 T 日的資料,auto_adjust 後的
       T 日數值會跟著變小)。如果對美股序列做「增量 upsert」,舊、新兩次抓取
       對同一天可能給出兩種不同尺度的數字,靠 PK 覆蓋只能覆蓋掉「重疊」的那幾天,
       覆蓋不到的更早期資料仍停留在舊尺度——序列會左右尺度不一致,比不存還糟。
       全量 DELETE+INSERT 每次都用「這次抓到的完整序列」整個換掉,保證單一尺度。
    2. INTL(全球型 ETF)的 ROI 試算假設「股利=0、報酬全部反映在股價」的
       total-return 語意,前提正是 yfinance auto_adjust 後的單一連續序列——
       增量拼接一旦混入不同時間點的 auto_adjust 尺度,會破壞這個假設。

    讀路徑完全不受影響(fetch_us/fetch_us_finnhub 的回傳值只由這次的 yfinance
    結果決定,不讀這裡存的東西)——這裡純粹是把讀到的資料多存一份到本地庫,
    供未來(工單 015 缺日偵測等)使用。成功 True,任何例外(含 rows 為空、
    store 損毀)→ False,呼叫端不因此改變回傳給使用者的結果。
    """
    try:
        with _connect(cache_dir) as conn:
            conn.execute(
                "DELETE FROM daily_price WHERE market=? AND ticker=?", (market, ticker)
            )
            if rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO daily_price(market,ticker,date,close) "
                    "VALUES (?,?,?,?)",
                    [(market, ticker, d, c) for d, c in rows],
                )
        return True
    except Exception:
        return False

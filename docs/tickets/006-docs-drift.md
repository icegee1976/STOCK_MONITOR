# 006 — 文件 drift 修正(收尾)

狀態:**CLOSED**(2026-07-06,commit 見 REPORT)
母單:000-baseline.md 工單 006

## SPEC

### 目標
把 001–005/007–008 落地後的現況同步回文件,**不改方法論與免責文字**(紅線 4):
1. `.claude/CLAUDE.md`「測試現況」節:「目前沒有測試」已過時 → 改為描述
   tests/ 離線套件(71 題)與新的收斂 gate(黃金值已自動化);行數 2,260→2,270。
2. `README.md` §7 檔案結構:補 `app.py`、`_gen_watchlist.py`、`_add_pyramid.py`、
   `watchlist.pdf_seed.yaml`、校正產物 json、`run_dashboard.bat`、`docs/`、`tests/`。
3. `docs/tickets/README.md`:黃金值 regression 建議已完成 → 改為指路現況。

### 範圍修正(相對 000-baseline 原計畫)
- `.gitignore`「亂碼」經 Read 工具確認是 PowerShell ANSI 誤讀的顯示假象,檔案本身
  正常 UTF-8 → **不修**(改前先看,避免誤改)。
- README §5.5 的「美股股利預扣 30%」宣稱在 007 落地後已為真 → **不需改**。

### 允許檔案
`.claude/CLAUDE.md`、`README.md`(僅 §7 結構)、`docs/tickets/README.md`、本工單檔。

### 驗收
- README §4/§5 方法論與免責文字 git diff 零變更;py_compile 不適用(純文件);
  71 題測試不受影響(不碰 code)。

### API 呼叫評估
0 次。

## REPORT(orchestrator 填)

- `.claude/CLAUDE.md`:「測試現況」由「目前沒有測試」改為 71 題離線套件描述,
  收斂 gate 加入 unittest 為第一關(黃金值自動化);技術棧行數 2,260→2,270
  (實測:引擎 11 檔 2,270 行、tests/ 五檔 1,311 行);指路 docs/api-budget.md。
- `README.md`:§7 檔案結構補 app.py、run_dashboard.bat、watchlist.pdf_seed.yaml、
  _gen_watchlist.py、_add_pyramid.py、校正產物 json、tests/、docs/;
  providers 註解更新為「Finnhub→yfinance」、roi 註解加「美股股利預扣 30%」。
  **git diff 確認只有 §7 一個 hunk,§4/§5 方法論與免責文字零變更**(紅線 4)。
- `docs/tickets/README.md`:黃金值 regression 建議改為「已由 001 落地」+ 全套 gate 指令。
- 範圍修正:.gitignore 亂碼為 PowerShell ANSI 誤讀假象(Read 工具驗證檔案正常),不修;
  README §5.5 預扣宣稱經 007 已為真,不需改。
- Gate:71 題照綠(未碰 code)、README 免責文字 diff 零。免 reviewer
  (純文件、orchestrator 逐行自查)。→ CLOSE。

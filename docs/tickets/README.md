# 工單(Tickets)

每張工單一個檔案,一次只做一件窄任務。格式:

- **SPEC** — 目標、驗收條件、**允許檔案清單**、**禁區**、**新增 API 呼叫評估**(若涉及)
- **PLAN** — executor 的做法
- **REPORT** — diff 摘要、`python -m py_compile` 結果、**黃金值比對**(改估價時,台積電 便宜≈2,226 / 大特≈1,729)、冒煙、剩餘風險
- **收斂 gate** — 語法綠 + 黃金值一致(如適用)+ 冒煙不崩 + orchestrator 已讀 diff + 一個乾淨 commit → close

`000-baseline.md` 由 orchestrator 在第一輪盤點時產生(現況 + 待改進清單)。
提醒(見 `.claude/CLAUDE.md` 紅線):顧 API 額度、數值正確第一、不改使用者假設、保留免責聲明。
黃金值 regression 已由工單 001 落地(`tests/test_golden_valuation.py`,離線 0 API);
全套離線 gate:`python -m unittest discover -s tests`(001–005/007 共 71 題)。

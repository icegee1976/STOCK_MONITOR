# 016 — 歷史庫硬化(backlog;014 reviewer P2-3/P3 群)

狀態:OPEN(backlog,未排程;014 收斂時仲裁「記錄延後」的項目集中於此)

## 待辦候選(開工時再切窄)
1. **定期全量重同步**(014 P2-3):增量使歷史值寫入即凍結,FinMind 事後更正
   不再自動吸收。方案候選:meta 記 last_full,超過 N 天(如 30)自動改全量一次;
   或 CLI 加 `--resync` 指令。呼叫數不變(仍 1 次/dataset),只是 payload периodic 放大。
2. **schema migration 政策**:`user_version=1` 已於 014 蓋章;本單定義升版流程
   (讀到舊版→就地 ALTER 或重建;損毀 DB 偵測後刪除重建,而非永久靜默退化)。
3. **store 修剪**:窗外舊列無限累積(~50KB/檔/年,量小);決定要不要修剪或明文不修。
4. **連線成本**:每 dataset 4–5 條短連線、每條重跑 PRAGMA+CREATE;本機無感,
   若未來支援網路碟/同步資料夾再合併連線。
5. **import 防護**:缺 `_sqlite3` 的罕見 Python 建置會在 import 期死;可包
   try/except 降級為「無 store 模式」。
6. Windows 清快取殘留:app.py rmtree(ignore_errors)在 sqlite 連線開啟時會留檔;
   評估清理前主動 close 或提示。

## API 呼叫評估
除 1.(定期全量,次數不變 payload 放大)外皆 0。

## 出處
014 reviewer findings(見 docs/tickets/014-local-history-store.md 收斂紀錄)。

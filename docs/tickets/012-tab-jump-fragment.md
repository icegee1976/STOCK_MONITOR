# 012 — 修 BUG:投報率分頁切換標的跳回總覽(st.tabs rerun 重置)

狀態:**CLOSED**(2026-07-06,commit 見文末收斂紀錄)
(使用者回報 2026-07-06)

## 現象與根因
投報率試算分頁把 selectbox 從預設台積電切到其他標的 → 畫面跳回「📊 總覽」。
根因:`st.tabs` 的作用中分頁是**前端狀態**,widget 互動觸發**全 script rerun**、
元素樹重建(總覽的 build_all/progress churn 加劇時序),分頁選取被重置回第一頁。
Streamlit 已知行為類 issue;本機快速合成事件測不必然重現(時序相依),實際使用會中。

## 修法(Streamlit 1.59,`@st.fragment`)
把互動最重的兩個分頁內容抽成 fragment 函數:
- `tab_roi`(L447–508)→ `@st.fragment def _roi_tab_body(): ...`,`with tab_roi: _roi_tab_body()`
- `tab_stock`(L409–444,同 class:selectbox 驅動)→ 同型處理
fragment 內 widget 互動只重跑 fragment、不重跑全 script → tabs 不重繪,**分頁不再跳**;
副作用紅利:切標的不再重跑 build_all(省 rerun 時間,API 不變因有快取)。

### 閉包/契約注意(executor 必讀)
- fragment 函數放模組層,引用的 `stocks`/`config`/`STAMP`/`mf`/helpers 皆全域,
  fragment rerun 時取值正常;`mf` 在 sidebar 變更會觸發全 rerun,fragment 隨之重建,無 stale 問題。
  ⚠ `mf`/`STAMP` 若是在 sidebar 區塊定義的區域變數,fragment 函數要嘛收參數、
  要嘛確認其為模組層名稱——**先讀 L295–335 確認**,不要猜。
- `config.setdefault("fx", ...)` 在 fragment 內變異共享 dict:fragment-only rerun 沿用
  上次全 rerun 的 config 物件,行為不變;可接受,註解說明即可。
- fragment 內不得寫入分頁容器以外的元素(sidebar 除外)——兩個分頁現有程式碼
  皆只寫自身區塊,合規;搬移時勿改寫任何顯示字串(009/011 的文案有測試鎖)。
- 其餘分頁(總覽/金字塔/便宜清單/資產配置/自訂清單)**不動**;自訂清單有
  存檔/上傳副作用,fragment 化另案評估(留 backlog 註記)。

### 允許檔案
- `app.py`(兩個分頁內容搬進 fragment 函數 + 呼叫點;**純搬移,顯示字串零改動**)

### 驗收
- py_compile 綠;76 題離線套件全綠(不 import app.py,理論不受影響,仍須跑)。
- **preview 實機驗收(orchestrator)**:ROI 分頁切換標的後,ROI tab 的
  `aria-selected` 仍為 true、投入行更新為新標的、總覽不重新出現 progress;
  個股河流圖分頁同樣驗證。
- `git diff` 中不得出現顯示字串變更(搬移縮排除外)。

### API 呼叫評估
0 新增(fragment 只影響 rerun 範圍;快取層不變)。驗收 preview 冷載入一次 ~36 FinMind。

## PLAN(executor 填)

`STAMP`(L281)與 `mf`(L300)皆定義在模組層(app.py 以腳本執行,`with st.sidebar:` 不
建立新作用域,`mf` 因此也是模組全域名稱),`stocks`/`config` 同理。故 `_stock_tab_body`/
`_roi_tab_body` 兩個 fragment 函數直接引用全域名稱即可,不需收參數——函數體內的名稱
在呼叫時(late binding)才查找,定義順序不影響。做法:在原 `with tab_stock:`/`with
tab_roi:` 位置各自替換成 `@st.fragment def _xxx_tab_body(): <原內容,縮排不變>`,原本
body 已是 4-space 縮排,升格為函數體後縮排剛好吻合,免重新縮排;`with tab_x:` 只留一行
呼叫 `_xxx_tab_body()`。`config.setdefault("fx", ...)` 保留原邏輯,依工單要求加一行註解
說明 fragment 內變異共享 dict 仍沿用上次全 rerun 物件、行為不變。

## REPORT(executor 填)

**DIFF 摘要**(`git diff app.py`,共 16 行新增 / 2 行刪除,`git diff -U0` 逐行核對):
- tab_stock:`with tab_stock:` → `@st.fragment` + `def _stock_tab_body():`(+2 行說明註解);
  body 原封不動;新增 `with tab_stock:\n    _stock_tab_body()` 呼叫點。
- tab_roi:同型處理,`with tab_roi:` → `@st.fragment` + `def _roi_tab_body():`(+2 行說明
  註解);body 原封不動;新增 `with tab_roi:\n    _roi_tab_body()` 呼叫點;`config.setdefault
  ("fx", ...)` 前依工單要求加 2 行註解說明 fragment 內變異共享 dict 的行為(工單明確允許
  的唯一非純搬移異動)。

**「純搬移」驗證方式**:`git diff -U0 app.py | grep -E '^[+-]'` 逐行核對,結果僅有:
(a) 2 行 `-with tab_x:` 被替換為 decorator+def(4 行);(b) 2 個 4 行的說明註解區塊;
(c) 2 個 2 行的 `with tab_x:\n    _x_tab_body()` 呼叫點;(d) 1 個 2 行的 setdefault 註解
(工單允許)。除此之外**沒有任何一行 body 內容(顯示字串 / widget key / 邏輯)出現在
diff 裡**——因為原 body 縮排本就是 4-space,升格為函數體後縮排數字不變,diff 引擎完全
沒偵測到那些行有變動。`roi_pick` 等 widget key 逐一確認未被觸碰(diff 中無 `key=` 相關行)。

**Gate 結果**:
- `python -m py_compile app.py` → 通過(PYCOMPILE_OK)。
- `python -m unittest discover -s tests` → `Ran 76 tests in 0.077s / OK`,全綠。
- 依工單指示未跑 live dashboard,留給 orchestrator 用 preview 做 ROI/個股分頁切換
  標的的實機驗收(`aria-selected` 維持、總覽不重繪)。

**API 呼叫評估**:0 新增,未動任何 provider 呼叫邏輯,快取層(`@st.cache_data`/
`analyze_stock`/`providers.fetch`)沿用原樣。

**剩餘風險**:fragment 化後的 rerun 行為（tabs 不跳頁、切標的不重跑 build_all）僅能在
瀏覽器 preview 實測,本輪未跑 `streamlit run app.py`(工單指示不跑,交由 orchestrator
preview 驗收);其餘五個分頁(總覽/金字塔/便宜清單/資產配置/自訂清單)未觸碰,`app.py`
其餘程式碼零改動。

## 收斂紀錄(orchestrator)

- **Preview 實機驗收(重現使用者操作路徑)**:
  - 投報率分頁:台積電 → 鴻海(2317),切換後 `aria-selected` 仍在
    「💰 投報率試算」、投入行更新為 NT$237.50/1,262 股、**全程無 progress bar**
    (總覽 build_all 未被重跑,證實 fragment-only rerun)。
  - 個股河流圖分頁:切 NVIDIA,分頁不跳、河流圖正常渲染、零 progress bar。
  - server log 零錯誤。
- **免派獨立 reviewer(orchestrator 裁決)**:diff 為純搬移(executor 以 `git diff -U0`
  逐行證明 body 零變動,orchestrator 複讀 diff 確認),行為由 preview 雙分頁實測 +
  76 題離線套件(含 009/011 顯示字串鎖)背書。
- 收斂 gate:76/76 綠、py_compile 綠、diff 僅 app.py、0 新增 API。→ CLOSE。
- 備註:自訂清單/資產配置等其餘分頁的互動仍走全 script rerun,理論上同 class
  跳頁可能存在;因涉存檔/上傳副作用,fragment 化風險較高,待實際回報再開單。

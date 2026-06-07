"""終端機報表輸出。rich 有就用彩色表格,沒有則純文字,兩者皆可讀。"""

from __future__ import annotations

from .valuation import ZONE_KEYS, ZONE_LABEL

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    _C = Console()
    HAS_RICH = True
except Exception:
    _C = None
    HAS_RICH = False

REGION_COLOR = {
    "大特價區": "bright_green", "便宜價區": "green", "合理價區": "yellow",
    "昂貴價區": "orange1", "瘋狂價區": "red", "超瘋狂價區": "bright_red",
}


def _p(s=""):
    if HAS_RICH:
        _C.print(s)
    else:
        # 去掉 rich 標記
        import re
        print(re.sub(r"\[/?[a-z0-9_ ]+\]", "", str(s)))


def money(x, ccy):
    if x is None:
        return "—"
    sym = "NT$" if ccy == "TWD" else "$"
    return f"{sym}{x:,.2f}"


def price_ladder(price, zones, region, ccy) -> str:
    """由高到低印出五個價格帶,並在現價位置標記。"""
    lines = []
    order = list(reversed(ZONE_KEYS))           # euphoria → super_bargain
    placed = False
    # 若現價高於瘋狂價,先標在最上面
    if price > zones["euphoria"]:
        lines.append(f"    ◀ 現價 {money(price, ccy)}  ({region})")
        placed = True
    for k in order:
        lines.append(f"  {ZONE_LABEL[k]:<4} {money(zones[k], ccy):>12}  ┤")
        # 現價落在此帶與下一帶之間?
        idx = ZONE_KEYS.index(k)
        lower = zones[ZONE_KEYS[idx - 1]] if idx > 0 else -1
        if not placed and lower < price <= zones[k]:
            lines.append(f"    ◀ 現價 {money(price, ccy)}  ({region})")
            placed = True
    if not placed:
        lines.append(f"    ◀ 現價 {money(price, ccy)}  ({region})")
    return "\n".join(lines)


def render_stock_card(item, config):
    cfg, data = item["cfg"], item["data"]
    name = cfg.get("name", cfg["ticker"])
    head = f"{name}  ({cfg['ticker']}.{cfg['market']})"
    _p()
    _p(f"[bold]══ {head} ══[/bold]")
    if item["error"]:
        _p(f"  [red]⚠ {item['error']}[/red]")
        return
    z = item["zones"]
    a = item["analysis"]
    ccy = data.currency
    color = REGION_COLOR.get(a["region"], "white")
    _p(f"  現價 [bold]{money(data.price, ccy)}[/bold] ({data.price_date})  →  "
       f"[{color}]{a['region']}[/{color}]   來源:{data.source}")
    _p()
    _p(price_ladder(data.price, z["zones"], a["region"], ccy))
    _p()
    # 估值錨點
    if z.get("anchor") is not None:
        _p(f"  估值錨點: {z['anchor_kind']} = {z['anchor']}"
           + (f" (目標 {z['target_year']} 年)" if z.get("target_year") else ""))
    _p(f"  假設: {z['assumptions']}")
    # 現價隱含的倍數 — 讓你一眼判斷「假設是否合理」
    if z.get("implied_multiple") is not None:
        yb = z.get("yield_bands")
        bands = z.get("pe_bands") or z.get("ps_bands")
        extra = ""
        if isinstance(yb, dict):                       # 殖利率法:顯示便宜/瘋狂所需殖利率
            extra = (f"  (便宜需殖利率≈{float(yb['cheap'])*100:.1f}% / "
                     f"瘋狂≈{float(yb['euphoria'])*100:.1f}%)")
        elif isinstance(bands, dict):
            extra = (f"  (你的便宜帶≈{float(bands['cheap']):.0f} / "
                     f"瘋狂帶≈{float(bands['euphoria']):.0f})")
        _p(f"  現價隱含 {z.get('implied_kind','倍數')} ≈ [bold]{z['implied_multiple']}[/bold]{extra}")
    # 便宜價何時出現
    if a["is_buy"]:
        _p(f"  [bold green]★ 已進入便宜價(含)以下 — 符合你預設的提醒條件[/bold green]")
    else:
        _p(f"  距便宜價還要跌 [bold]{a['drop_to_cheap_pct']}%[/bold]"
           + (f"   (年化波動率 {a['annual_vol_pct']}%)" if a["annual_vol_pct"] else ""))
        probs = a.get("prob_hit_cheap", {})
        if probs:
            ptxt = "、".join(f"{y}年內 {p}%" for y, p in probs.items())
            _p(f"  觸及便宜價機率(統計估計,非預測): {ptxt}")
    if a.get("price_percentile") is not None:
        _p(f"  現價位於過去約{config.get('history_years',5)}年股價的第 {a['price_percentile']} 百分位"
           "  (0=史上最低, 100=史上最高)")
    for w in z.get("warnings", []):
        _p(f"  [dim]· 注意:{w}[/dim]")
    if cfg.get("note"):
        _p(f"  [dim]{cfg['note']}[/dim]")


def render_summary(rows, config):
    _p()
    if HAS_RICH:
        t = Table(title="監測摘要 (依距便宜價排序)", box=box.SIMPLE_HEAVY)
        for col in ["標的", "代號", "現價", "價位", "距便宜", "1年觸及機率", "錨點"]:
            t.add_column(col)
        for it in rows:
            cfg, data = it["cfg"], it["data"]
            if it["error"]:
                t.add_row(cfg.get("name", ""), str(cfg["ticker"]), "—",
                          "[red]錯誤[/red]", "—", "—", "—")
                continue
            a, z = it["analysis"], it["zones"]
            color = REGION_COLOR.get(a["region"], "white")
            p1 = a.get("prob_hit_cheap", {})
            yr1 = next(iter(config.get("roi_horizons_years", [1])), 1)
            prob = f"{p1.get(yr1,'—')}%" if p1 else "—"
            drop = "已便宜" if a["is_buy"] else f"需跌{a['drop_to_cheap_pct']}%"
            t.add_row(cfg.get("name", ""), str(cfg["ticker"]),
                      money(data.price, data.currency),
                      f"[{color}]{a['region']}[/{color}]", drop, prob,
                      f"{z.get('anchor_kind','')}={z.get('anchor','—')}")
        _C.print(t)
    else:
        _p("標的 | 代號 | 現價 | 價位 | 距便宜 | 錨點")
        for it in rows:
            cfg, data = it["cfg"], it["data"]
            if it["error"]:
                _p(f"{cfg.get('name','')} | {cfg['ticker']} | 錯誤: {it['error']}")
                continue
            a, z = it["analysis"], it["zones"]
            drop = "已便宜" if a["is_buy"] else f"需跌{a['drop_to_cheap_pct']}%"
            _p(f"{cfg.get('name','')} | {cfg['ticker']} | {money(data.price,data.currency)} "
               f"| {a['region']} | {drop} | {z.get('anchor_kind','')}={z.get('anchor','—')}")


def render_roi(r, config):
    if "error" in r:
        _p(f"[red]{r['error']}[/red]")
        return
    _p()
    _p(f"[bold]══ 投報率情境試算:{r['name']} ({r['ticker']}) ══[/bold]")
    sh = f"{int(r['shares']):,} 股" if r['market'] == "TW" else f"{r['shares']:,.3f} 股(可碎股)"
    _p(f"  投入 {money(r['spent'], r['stock_ccy'])} → 買進 {sh}  @ {money(r['price'], r['stock_ccy'])}")
    if r["fx_note"]:
        _p(f"  [yellow]匯率:資金幣別 {r['cap_ccy']} ≠ 標的幣別 {r['stock_ccy']} "
           f"(USD/TWD≈{r['fx_usdtwd']}),含匯率風險[/yellow]")
    if r["dividend_yield_pct"]:
        _p(f"  (已含股利率 {r['dividend_yield_pct']}%/年 之簡化累積)")
    _p(f"  [dim]情境:若未來股價回到各價格帶,持有 N 年的報酬。這是 if-then 模型,非保證。[/dim]")
    _p()
    horizons = [row["years"] for row in r["scenarios"][0]["rows"]]
    if HAS_RICH:
        t = Table(box=box.SIMPLE)
        t.add_column("情境(目標價位)")
        t.add_column("目標價", justify="right")
        for y in horizons:
            t.add_column(f"持有{y}年\n總報酬 / 年化", justify="right")
        for sc in r["scenarios"]:
            cells = [f"{sc['label']}", money(sc["target_price"], r["stock_ccy"])]
            for row in sc["rows"]:
                cells.append(f"{row['total_return_pct']:+.0f}% / {row['annualized_pct']:+.0f}%")
            t.add_row(*cells)
        _C.print(t)
    else:
        _p("情境 | 目標價 | " + " | ".join(f"{y}年(總/年化)" for y in horizons))
        for sc in r["scenarios"]:
            cells = " | ".join(f"{row['total_return_pct']:+.0f}%/{row['annualized_pct']:+.0f}%"
                               for row in sc["rows"])
            _p(f"{sc['label']} | {money(sc['target_price'], r['stock_ccy'])} | {cells}")


DISCLAIMER = (
    "\n[dim]──────────────────────────────────────────────\n"
    "本工具僅作資訊與教育用途,不構成投資建議。所有「便宜價/目標價/報酬」\n"
    "均建立在 watchlist.yaml 內可被你修改的假設上;免費數據為延遲報價。\n"
    "投資前請自行查證並承擔風險。[/dim]"
)

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""美股/台股 AI+太空 成長股與 ETF 價格帶監測器 (CLI 入口)。

用法:
  python monitor.py report                 # 全清單價格帶報表
  python monitor.py report --ticker 2330   # 單一標的詳細卡片
  python monitor.py screen                 # 篩出目前便宜的標的
  python monitor.py roi NVDA 300000        # 投入 30 萬(預設台幣)試算報酬
  python monitor.py roi 2330 500000        # 台股投入 50 萬
  python monitor.py watch --interval 300   # 常駐輪詢,進入便宜價時桌面提醒
  python monitor.py bands 2317             # 等同 report --ticker 2317
"""

from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Windows 主控台預設非 UTF-8,中文會亂碼或丟例外 → 強制 UTF-8。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from aimonitor import providers, report
from aimonitor.valuation import compute_zones, ValuationError, ZONE_KEYS
from aimonitor.classify import analyze, classify_region
from aimonitor.roi import scenario_roi
from aimonitor.screener import screen


# --------------------------------------------------------------------------- #
#  設定載入
# --------------------------------------------------------------------------- #
def _load_yaml(path):
    try:
        import yaml
    except ImportError:
        sys.exit("需要 PyYAML:請執行  pip install pyyaml")
    if not os.path.exists(path):
        sys.exit(f"找不到設定檔:{path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_all():
    config = _load_yaml(os.path.join(HERE, "config.yaml"))
    wl = _load_yaml(os.path.join(HERE, "watchlist.yaml"))
    stocks = wl.get("stocks", []) if isinstance(wl, dict) else wl
    return config, stocks


def make_fetch_fn(config, use_cache=True):
    pcfg = config.get("providers", {})
    yrs = int(config.get("history_years", 5))
    return lambda cfg: providers.fetch(cfg, pcfg, yrs, use_cache=use_cache)


def find_stock(stocks, ticker):
    tk = str(ticker).upper()
    for s in stocks:
        if str(s["ticker"]).upper() == tk:
            return s
    sys.exit(f"清單中找不到代號 {ticker}。可用 report 看全部。")


# --------------------------------------------------------------------------- #
#  指令
# --------------------------------------------------------------------------- #
def cmd_report(args, config, stocks):
    fetch_fn = make_fetch_fn(config)
    targets = stocks
    if args.ticker:
        targets = [find_stock(stocks, args.ticker)]
    elif args.market:
        targets = [s for s in stocks if s["market"].upper() == args.market.upper()]

    horizons = config.get("roi_horizons_years", [1, 3, 5])
    rows = []
    for cfg in targets:
        data = fetch_fn(cfg)
        item = {"cfg": cfg, "data": data, "zones": None, "analysis": None, "error": ""}
        if not data.ok():
            item["error"] = data.error or "無資料"
        else:
            try:
                z = compute_zones(cfg, data, config)
                item["zones"] = z
                item["analysis"] = analyze(data.price, z["zones"], data.price_history, horizons)
            except ValuationError as e:
                item["error"] = f"估價失敗: {e}"
        rows.append(item)
        report.render_stock_card(item, config)

    if len(rows) > 1:
        report.render_summary(rows, config)
    report._p(report.DISCLAIMER)


def cmd_screen(args, config, stocks):
    fetch_fn = make_fetch_fn(config)
    rows = screen(stocks, fetch_fn, config)
    report._p("\n[bold]══ 篩選:目前最接近/已達便宜價的標的 ══[/bold]")
    buys = [r for r in rows if r["analysis"] and r["analysis"]["is_buy"]]
    if buys:
        report._p(f"[bold green]★ 已進入便宜價(含)以下:"
                  f"{'、'.join(r['cfg']['name'] for r in buys)}[/bold green]")
    else:
        report._p("[yellow]目前清單中沒有標的進入便宜價。以下依距便宜價排序。[/yellow]")
    report.render_summary(rows, config)
    report._p(report.DISCLAIMER)


def cmd_roi(args, config, stocks):
    cfg = find_stock(stocks, args.ticker)
    fetch_fn = make_fetch_fn(config)
    data = fetch_fn(cfg)
    if not data.ok():
        sys.exit(f"無法取得 {args.ticker} 報價:{data.error}")
    try:
        z = compute_zones(cfg, data, config)
    except ValuationError as e:
        sys.exit(f"估價失敗:{e}")
    cap_ccy = args.currency or config.get("base_currency", "TWD")
    # 跨幣別時抓即時匯率(失敗自動回退設定值)
    stock_ccy = data.currency or ("TWD" if cfg["market"].upper() == "TW" else "USD")
    if cap_ccy != stock_ccy:
        config.setdefault("fx", {})["USDTWD"] = providers.usd_twd(
            config.get("fx", {}).get("USDTWD", 32.0))
    r = scenario_roi(cfg, data, z, args.amount, config, capital_currency=cap_ccy)
    report.render_roi(r, config)
    report._p(report.DISCLAIMER)


def _notify(title, msg):
    """盡力而為的桌面通知;失敗則響鈴 + 文字。"""
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast(title, msg, duration=8, threaded=True)
        return
    except Exception:
        pass
    try:
        from plyer import notification
        notification.notify(title=title, message=msg, timeout=8)
        return
    except Exception:
        pass
    print("\a", end="")  # 終端機響鈴


def cmd_watch(args, config, stocks):
    fetch_fn = make_fetch_fn(config, use_cache=False)
    notify_zone = config.get("alert", {}).get("notify_zone", "cheap")
    do_desktop = config.get("alert", {}).get("desktop_notification", True)
    horizons = config.get("roi_horizons_years", [1, 3, 5])
    order = {k: i for i, k in enumerate(ZONE_KEYS)}
    last_region = {}
    interval = args.interval

    report._p(f"[bold]啟動監測[/bold] — 每 {interval}s 輪詢,進入「{notify_zone}」(含)以下即提醒。Ctrl+C 結束。")
    while True:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        triggered = []
        for cfg in stocks:
            data = fetch_fn(cfg)
            if not data.ok():
                continue
            try:
                z = compute_zones(cfg, data, config)
            except ValuationError:
                continue
            region = classify_region(data.price, z["zones"])
            # 是否 ≤ notify_zone 的門檻價
            threshold = z["zones"][notify_zone]
            hit = data.price <= threshold
            prev = last_region.get(cfg["ticker"])
            if hit and prev != region:                     # 進入提醒區(避免重複轟炸)
                triggered.append((cfg, data, region, threshold))
            last_region[cfg["ticker"]] = region
        if triggered:
            for cfg, data, region, thr in triggered:
                line = (f"{cfg['name']} ({cfg['ticker']}) {report.money(data.price, data.currency)} "
                        f"→ {region} ≤ {report.money(thr, data.currency)}")
                report._p(f"[bold green][{ts}] ★ 提醒:{line}[/bold green]")
                if do_desktop:
                    _notify("便宜價提醒", line)
        else:
            report._p(f"[dim][{ts}] 無觸發。[/dim]")
        if args.once:
            break
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description="美股/台股 成長股與 ETF 價格帶監測器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("report", help="價格帶報表")
    p.add_argument("--ticker", help="只看單一代號")
    p.add_argument("--market", help="只看 TW 或 US")

    sub.add_parser("screen", help="篩出目前便宜的標的")

    p = sub.add_parser("roi", help="投報率情境試算")
    p.add_argument("ticker")
    p.add_argument("amount", type=float, help="投入金額")
    p.add_argument("--currency", help="資金幣別 TWD/USD (預設同標的)")

    p = sub.add_parser("watch", help="常駐輪詢 + 便宜價提醒")
    p.add_argument("--interval", type=int, default=300, help="輪詢秒數(預設300)")
    p.add_argument("--once", action="store_true", help="只跑一次")

    p = sub.add_parser("bands", help="單一標的價格帶(同 report --ticker)")
    p.add_argument("ticker")

    args = ap.parse_args()
    config, stocks = load_all()

    if args.cmd == "report":
        cmd_report(args, config, stocks)
    elif args.cmd == "screen":
        cmd_screen(args, config, stocks)
    elif args.cmd == "roi":
        cmd_roi(args, config, stocks)
    elif args.cmd == "watch":
        cmd_watch(args, config, stocks)
    elif args.cmd == "bands":
        args.ticker = args.ticker
        args.market = None
        cmd_report(args, config, stocks)


if __name__ == "__main__":
    main()

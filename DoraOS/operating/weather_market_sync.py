#!/usr/bin/env python3
"""Sync official weather and market context for DoraOS Operating Feed."""

from __future__ import annotations

import argparse
import html
import json
import re
import xml.etree.ElementTree as ET
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

from common import DEFAULT_ENV_FILE, LOG_DIR, build_logger, ensure_dir, load_env_file, require_env, today_stamp, wait_for_network


CWA_DATASET = "F-C0032-001"
CWA_ENDPOINT = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{CWA_DATASET}"
TWSE_MI_INDEX_ENDPOINT = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
TWSE_STOCK_DAY_ENDPOINT = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
YAHOO_CHART_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart"
GOOGLE_NEWS_RSS_ENDPOINT = "https://news.google.com/rss/search"
DEFAULT_MARKET_WATCHLIST = [
    ("0050.TW", "0050 台灣50"),
    ("00931B.TW", "00931B 統一美債20年"),
    ("1519.TW", "1519 華城電機"),
    ("2454.TW", "2454 聯發科"),
    ("QQQ", "QQQ Nasdaq 100 ETF"),
    ("SPCX", "SPCX The Acquirers Fund"),
    ("VT", "VT Vanguard 全球股票"),
    ("VOO", "VOO Vanguard S&P 500"),
]
DEFAULT_NEWS_QUERIES = [
    "台股 AI 半導體",
    "美股 AI 科技",
    "台灣 金融市場",
]


def _fetch_json(url: str, timeout: float = 20.0) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "DoraOS/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_bytes(url: str, timeout: float = 20.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "DoraOS/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _strip_html(value: str) -> str:
    text = html.unescape(value or "").replace("\xa0", " ")
    return re.sub(r"<[^>]+>", "", text).strip()


def _clip(value: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _clean_news_title(title: str, source: str) -> str:
    text = _strip_html(title)
    if source:
        text = re.sub(rf"\s*[-｜|]\s*{re.escape(source)}\s*$", "", text).strip()
    return text


def _is_duplicate_summary(title: str, summary: str, source: str) -> bool:
    title_norm = re.sub(r"\W+", "", title.lower())
    summary_norm = re.sub(r"\W+", "", summary.lower())
    source_norm = re.sub(r"\W+", "", source.lower())
    if not summary_norm:
        return True
    if summary_norm == title_norm or summary_norm == f"{title_norm}{source_norm}":
        return True
    return title_norm and title_norm in summary_norm and len(summary_norm) <= len(title_norm) + len(source_norm) + 8


def _news_point(query: str, title: str) -> str:
    text = f"{query} {title}"
    if any(word in text for word in ["費半", "半導體", "AI", "聯發科", "晶片"]):
        return "半導體與 AI 供應鏈是今天市場情緒核心，先看風險方向，不急著轉成操作。"
    if any(word in text for word in ["Fed", "FOMC", "利率", "油價", "美股"]):
        return "美股與利率/油價訊號會影響科技股風險偏好，今天先放進觀察清單。"
    if any(word in text for word in ["台灣經濟", "GDP", "景氣", "下半年"]):
        return "台灣下半年景氣變數混雜，AI 出口、利率與內需需要一起看。"
    return f"這是「{query}」的今日高訊號新聞，先當作背景雷達。"


def _news_summary(query: str, title: str, raw_summary: str, source: str) -> str:
    summary = _strip_html(raw_summary)
    if not _is_duplicate_summary(title, summary, source):
        return summary
    if "美股" in query or "Fed" in title or "FOMC" in title:
        return "美股盤前與政策事件會牽動科技股風險偏好；重點是觀察 AI 供應鏈是否仍有支撐。"
    if "台股" in query or "半導體" in title or "AI" in title:
        return "台股與半導體新聞仍圍繞 AI 需求、供應鏈與估值情緒；適合做風險掃描。"
    if "台灣" in query or "經濟" in title:
        return "台灣經濟訊號多空交雜，需同時看出口、AI 投資、利率與消費動能。"
    return "RSS 未提供有效內文摘要；先保留標題與來源，作為今日閱讀雷達。"


def _parse_watchlist(config: Dict[str, str]) -> list[tuple[str, str]]:
    holdings = _parse_holdings(config)
    if holdings:
        return [(symbol, item["label"]) for symbol, item in holdings.items()]

    raw = config.get("DORAOS_MARKET_WATCHLIST", "").strip()
    if not raw:
        return DEFAULT_MARKET_WATCHLIST
    items: list[tuple[str, str]] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        if ":" in item:
            symbol, label = item.split(":", 1)
            items.append((symbol.strip(), label.strip() or symbol.strip()))
        else:
            items.append((item, item))
    return items or DEFAULT_MARKET_WATCHLIST


def _parse_holdings(config: Dict[str, str]) -> dict[str, dict[str, str]]:
    raw = config.get("DORAOS_MARKET_HOLDINGS", "").strip()
    if not raw:
        return {}
    holdings: dict[str, dict[str, str]] = {}
    for part in raw.split(";"):
        item = part.strip()
        if not item:
            continue
        fields = [field.strip() for field in item.split("|")]
        if len(fields) < 5:
            continue
        symbol, label, shares, avg_cost, currency = fields[:5]
        holdings[symbol] = {
            "label": label or symbol,
            "shares": shares,
            "avg_cost": avg_cost,
            "currency": currency or "",
        }
    return holdings


def _parse_news_queries(config: Dict[str, str]) -> list[str]:
    raw = config.get("DORAOS_NEWS_QUERIES", "").strip()
    if not raw:
        return DEFAULT_NEWS_QUERIES
    queries = [item.strip() for item in raw.split(";") if item.strip()]
    return queries or DEFAULT_NEWS_QUERIES


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace("NT$", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _fmt_number(value: Any, digits: int = 2) -> str:
    number = _to_float(value)
    if number is None:
        return "n/a"
    return f"{number:,.{digits}f}"


def _fmt_position_number(value: Any, currency: str) -> str:
    number = _to_float(value)
    if number is None:
        return "n/a"
    if currency.upper() == "TWD":
        return f"NT$ {number:,.0f}"
    if currency.upper() == "USD":
        return f"US$ {number:,.2f}"
    return f"{number:,.2f}"


def _fmt_percent(value: Any) -> str:
    if value is None or value == "":
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def _quote_indicator(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "⚪"
    if number > 0:
        return "🟢"
    if number < 0:
        return "🔴"
    return "⚪"


def _yahoo_chart_quote(symbol: str) -> Dict[str, Any] | None:
    encoded = urllib.parse.quote(symbol, safe="")
    data = _fetch_json(f"{YAHOO_CHART_ENDPOINT}/{encoded}?range=5d&interval=1d")
    results = data.get("chart", {}).get("result", []) or []
    if not results:
        return None
    result = results[0]
    meta = result.get("meta", {}) or {}
    closes = (
        result.get("indicators", {})
        .get("quote", [{}])[0]
        .get("close", [])
        or []
    )
    clean_closes = [float(value) for value in closes if value is not None]
    if clean_closes:
        meta["doraosMarketPrice"] = clean_closes[-1]
    if len(clean_closes) >= 2:
        meta["doraosPreviousClose"] = clean_closes[-2]
    return meta


def _twse_stock_quote(symbol: str) -> Dict[str, Any] | None:
    stock_no = symbol.replace(".TW", "").replace(".TWO", "").strip()
    if not stock_no:
        return None
    now = datetime.now()
    params = urllib.parse.urlencode(
        {
            "response": "json",
            "date": now.strftime("%Y%m%d"),
            "stockNo": stock_no,
        }
    )
    data = _fetch_json(f"{TWSE_STOCK_DAY_ENDPOINT}?{params}")
    rows = data.get("data", []) or []
    closes: list[float] = []
    for row in rows:
        if len(row) >= 7:
            close_value = _to_float(row[6])
            if close_value is not None:
                closes.append(close_value)
    if not closes:
        return None
    meta: Dict[str, Any] = {"symbol": symbol, "currency": "TWD", "exchangeName": "TWSE"}
    meta["doraosMarketPrice"] = closes[-1]
    if len(closes) >= 2:
        meta["doraosPreviousClose"] = closes[-2]
    return meta


def _market_quote(symbol: str) -> Dict[str, Any] | None:
    try:
        row = _yahoo_chart_quote(symbol)
    except Exception:
        row = None
    if row and (row.get("doraosMarketPrice") or row.get("regularMarketPrice")) is not None:
        return row
    if symbol.endswith(".TW") or symbol.endswith(".TWO"):
        try:
            return _twse_stock_quote(symbol)
        except Exception:
            return None
    return row


def _yahoo_watchlist(config: Dict[str, str]) -> list[str]:
    watchlist = _parse_watchlist(config)
    holdings = _parse_holdings(config)

    output: list[str] = []
    for symbol, label in watchlist:
        row = _market_quote(symbol)
        if not row:
            output.append(f"- ⚪ {label}: 報價未取得")
            continue
        price_value = row.get("doraosMarketPrice") or row.get("regularMarketPrice")
        if price_value is None:
            output.append(f"- ⚪ {label}: 報價未取得")
            continue
        previous_close = row.get("doraosPreviousClose")
        change_value = None
        change_pct_value = None
        if previous_close:
            change_value = float(price_value) - float(previous_close)
            change_pct_value = (change_value / float(previous_close)) * 100
        price = _fmt_number(price_value)
        change_pct = _fmt_percent(change_pct_value)
        change = _fmt_number(change_value)
        indicator = _quote_indicator(change_pct_value)
        holding = holdings.get(symbol)
        if holding:
            shares = _to_float(holding.get("shares"))
            avg_cost = _to_float(holding.get("avg_cost"))
            currency = holding.get("currency", "").upper()
            if shares is not None and avg_cost is not None:
                market_value = shares * float(price_value)
                cost_value = shares * avg_cost
                pnl_pct = ((market_value - cost_value) / cost_value * 100) if cost_value else None
                output.append(
                    f"- {indicator} {label}: {price}（{change_pct}）｜持有 {holding['shares']}｜估 {_fmt_position_number(market_value, currency)}｜損益 {_fmt_percent(pnl_pct)}"
                )
                continue
        output.append(f"- {indicator} {label}: {price}（{change_pct}, {change}）")
    return output


def _google_news_item(query: str) -> dict[str, str] | None:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "hl": "zh-TW",
            "gl": "TW",
            "ceid": "TW:zh-Hant",
        }
    )
    xml_bytes = _fetch_bytes(f"{GOOGLE_NEWS_RSS_ENDPOINT}?{params}")
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        return None
    for item in channel.findall("item"):
        raw_title = _strip_html(item.findtext("title") or "")
        description = _strip_html(item.findtext("description") or "")
        link = (item.findtext("link") or "").strip()
        source = ""
        for child in list(item):
            if child.tag.endswith("source"):
                source = (child.text or "").strip()
                break
        title = _clean_news_title(raw_title, source)
        if title:
            return {
                "query": query,
                "title": title,
                "point": _news_point(query, title),
                "summary": _news_summary(query, title, description, source),
                "source": source or "Google News",
                "link": link,
            }
    return None


def _news_radar(config: Dict[str, str], limit: int = 3) -> list[str]:
    articles: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for query in _parse_news_queries(config):
        try:
            article = _google_news_item(query)
        except Exception:
            article = None
        if not article:
            continue
        title_key = article["title"].lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        articles.append(article)
        if len(articles) >= limit:
            break

    lines: list[str] = []
    if len(articles) < limit:
        lines.append("- 新聞抓取不足 3 篇；保留此狀態，不補假新聞。")
    for idx, article in enumerate(articles, 1):
        lines.extend(
            [
                f"### {idx}. {_clip(article['title'], 80)}",
                f"- 重點：{_clip(article.get('point') or article['title'], 120)}",
                f"- 摘要：{_clip(article['summary'], 180)}",
                f"- 來源：{article['source']}",
            ]
        )
    return lines


def _cwa_forecast(config: Dict[str, str], generated_at: str) -> str:
    api_key = config.get("CWA_API_KEY", "").strip()
    location = config.get("CWA_LOCATION_NAME", "臺北市").strip() or "臺北市"
    if not api_key:
        return "\n".join(
            [
                "# 官方天氣 | Weather",
                "",
                f"- 生成時間 | generated: {generated_at}",
                "- 狀態 | status: auth-required",
                f"- 來源 | source: CWA Open Data {CWA_DATASET}",
                "- 說明 | note: 缺少 CWA_API_KEY；請在 DoraOS/.env 補上中央氣象署開放資料 API key。",
                "",
                "## Weather | 台北天氣",
                "",
                "- CWA official weather unavailable until CWA_API_KEY is configured.",
                "",
            ]
        )

    params = urllib.parse.urlencode({"Authorization": api_key, "locationName": location})
    data = _fetch_json(f"{CWA_ENDPOINT}?{params}")
    locations = data.get("records", {}).get("location", []) or []
    if not locations:
        raise ValueError(f"CWA returned no forecast location for {location}.")
    record = locations[0]
    elements = {item.get("elementName"): item.get("time", []) for item in record.get("weatherElement", [])}

    def value(element: str, idx: int = 0) -> str:
        times = elements.get(element) or []
        if not times:
            return ""
        params = times[min(idx, len(times) - 1)].get("parameter", {}) or {}
        return params.get("parameterName", "")

    wx = value("Wx")
    pop = value("PoP")
    min_t = value("MinT")
    max_t = value("MaxT")
    comfort = value("CI")
    start_time = (elements.get("Wx") or [{}])[0].get("startTime", "")
    end_time = (elements.get("Wx") or [{}])[0].get("endTime", "")

    return "\n".join(
        [
            "# 官方天氣 | Weather",
            "",
            f"- 生成時間 | generated: {generated_at}",
            "- 狀態 | status: live",
            f"- 來源 | source: CWA Open Data {CWA_DATASET}",
            f"- 地區 | location: {location}",
            f"- 預報區間 | window: {start_time} - {end_time}",
            "",
            "## Weather | 台北天氣",
            "",
            f"- {location}：{wx or '未提供天氣現象'}，溫度約 {min_t or '?'}-{max_t or '?'}°C，降雨機率 {pop or '?'}%。",
            f"- 舒適度：{comfort or '未提供'}。",
            "- 行動建議：帶傘、補水，外出保留移動緩衝；把天氣當成注意力負載的一部分來看。",
            "",
        ]
    )


def _twse_market(config: Dict[str, str], generated_at: str, lookback_days: int = 10) -> str:
    now = datetime.now()
    selected_date = ""
    taiex: list[str] | None = None
    for offset in range(lookback_days):
        date = now - timedelta(days=offset)
        ymd = date.strftime("%Y%m%d")
        params = urllib.parse.urlencode({"response": "json", "date": ymd, "type": "IND"})
        data = _fetch_json(f"{TWSE_MI_INDEX_ENDPOINT}?{params}")
        for table in data.get("tables", []) or []:
            if not isinstance(table, dict):
                continue
            for row in table.get("data", []) or []:
                if row and row[0] == "發行量加權股價指數":
                    selected_date = ymd
                    taiex = row
                    break
            if taiex:
                break
        if taiex:
            break

    if not taiex:
        raise ValueError("TWSE returned no TAIEX row in recent MI_INDEX data.")

    sign = _strip_html(taiex[2])
    direction = "上漲" if sign == "+" else "下跌" if sign == "-" else "持平"
    close = taiex[1]
    points = taiex[3]
    percent = taiex[4]
    iso_date = f"{selected_date[:4]}-{selected_date[4:6]}-{selected_date[6:]}"

    try:
        watchlist_lines = _yahoo_watchlist(config)
        watchlist_status = "live"
    except Exception as exc:
        watchlist_lines = [f"- 股票逐檔報價抓取失敗：{str(exc)[:180]}"]
        watchlist_status = "api-failed"
    news_lines = _news_radar(config, limit=3)

    return "\n".join(
        [
            "# 官方市場 | Market News",
            "",
            f"- 生成時間 | generated: {generated_at}",
            f"- 狀態 | status: {'live' if watchlist_status == 'live' else 'mixed-live'}",
            "- 來源 | source: TWSE MI_INDEX, Yahoo Finance quote",
            f"- 資料日期 | data date: {iso_date}",
            "",
            "## Market Snapshot | 市場快照",
            "",
            f"- 台股最近可用官方收盤：加權指數 {close}，{direction} {points} 點，{percent}%。",
            f"- 資料來自 TWSE MI_INDEX；若今天尚未收盤，會自動使用最近一個有資料的交易日（目前為 {iso_date}）。",
            "- 行動建議：用這段做風險掃描，不把市場波動自動變成待辦；需要交易前再看更完整圖表。",
            "",
            "## Watchlist | 追蹤股票",
            "",
            *watchlist_lines,
            "",
            "## News Radar | 今日新聞",
            "",
            *news_lines,
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync official CWA weather and TWSE market context.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logger = build_logger("doraos.weather_market_sync", LOG_DIR / "dora_weather_market_sync.log", verbose=args.verbose)
    config = load_env_file(Path(args.env_file))
    vault_path = Path(require_env(config, "OBSIDIAN_VAULT_PATH")).expanduser()
    network_timeout = float(config.get("NETWORK_WARMUP_TIMEOUT", "120") or "120")
    wait_for_network(timeout=network_timeout, logger=logger)

    today, generated_at = today_stamp()
    feed_dir = vault_path / "Resources" / "Operating Feed"
    weather_path = feed_dir / f"{today} Weather.md"
    market_path = feed_dir / f"{today} Market News.md"

    try:
        weather_text = _cwa_forecast(config, generated_at)
    except Exception as exc:
        weather_text = "\n".join(
            [
                "# 官方天氣 | Weather",
                "",
                f"- 生成時間 | generated: {generated_at}",
                "- 狀態 | status: api-failed",
                f"- 來源 | source: CWA Open Data {CWA_DATASET}",
                f"- 錯誤 | error: `{str(exc)[:220]}`",
                "",
                "## Weather | 台北天氣",
                "",
                "- CWA official weather fetch failed; keep this visible and use a manual fallback if needed.",
                "",
            ]
        )
        logger.error("CWA weather sync failed: %s", exc)

    try:
        market_text = _twse_market(config, generated_at)
    except Exception as exc:
        market_text = "\n".join(
            [
                "# 官方市場 | Market News",
                "",
                f"- 生成時間 | generated: {generated_at}",
                "- 狀態 | status: api-failed",
                "- 來源 | source: TWSE MI_INDEX",
                f"- 錯誤 | error: `{str(exc)[:220]}`",
                "",
                "## News Radar | 今日新聞",
                "",
                "- TWSE official market fetch failed; keep this visible and use a manual fallback if needed.",
                "",
            ]
        )
        logger.error("TWSE market sync failed: %s", exc)

    if args.dry_run:
        print(weather_text)
        print("---")
        print(market_text)
        return 0

    ensure_dir(feed_dir)
    weather_path.write_text(weather_text, encoding="utf-8")
    market_path.write_text(market_text, encoding="utf-8")
    logger.info("Wrote weather context to %s", weather_path)
    logger.info("Wrote market context to %s", market_path)
    print(weather_path)
    print(market_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

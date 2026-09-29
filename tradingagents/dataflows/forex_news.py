"""Structured Forex news with publication and observation cutoffs."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.forex_archive import read_snapshots, save_snapshot
from tradingagents.dataflows.forex_quality import DataInsufficientError, utc_timestamp
from tradingagents.forex.domain import get_forex_pair
from tradingagents.forex.symbols import canonical_to_yahoo

KEYWORDS = {
    "USD": ("fed", "dollar", "treasury", "united states"),
    "EUR": ("ecb", "euro", "germany", "france"),
    "GBP": ("boe", "sterling", "pound", "britain"),
    "JPY": ("boj", "yen", "japan"), "CHF": ("snb", "swiss", "franc"),
    "CAD": ("boc", "canada", "canadian"), "AUD": ("rba", "australia", "aussie"),
    "NZD": ("rbnz", "new zealand", "kiwi"),
}


class ForexNewsArticle(BaseModel):
    model_config = ConfigDict(frozen=True)
    headline: str
    publisher: str
    published_at_utc: datetime
    retrieved_at_utc: datetime
    currencies: tuple[str, ...]
    category: str = "currency_news"
    relevance: float
    url: str
    source: str = "Yahoo"
    summary: str = ""

    @field_validator("published_at_utc", "retrieved_at_utc", mode="before")
    @classmethod
    def valid_timestamp(cls, value):
        return utc_timestamp(value)


def fetch_forex_news(symbol, *, as_of=None, lookback_days=5, limit=8):
    from tradingagents.dataflows.forex_context import historical_market_context
    historical = historical_market_context()
    if historical is not None:
        bound = historical[2]
        as_of = min(utc_timestamp(as_of), bound) if as_of is not None else bound
    config = get_config()
    pair = get_forex_pair(symbol)
    if pair is None:
        raise DataInsufficientError("unknown news currency pair")
    if not 1 <= lookback_days <= 365 or not 1 <= limit <= 100:
        raise ValueError("invalid news window or limit")
    directory = config.get("forex_news_archive_dir") or Path(config.get("data_cache_dir", "cache")) / "forex_news"
    cutoff = utc_timestamp(as_of) if as_of is not None else datetime.now(timezone.utc)
    snapshots = list(read_snapshots(directory))
    if as_of is None:
        import yfinance as yf

        from tradingagents.dataflows.stockstats_utils import yf_retry
        from tradingagents.dataflows.yfinance_news import _extract_article_data

        try:
            raw = yf_retry(lambda: yf.Ticker(canonical_to_yahoo(pair.symbol)).get_news(count=100))
        except Exception:
            raise DataInsufficientError("Forex news provider unavailable") from None
        retrieved = datetime.now(timezone.utc)
        records = []
        if not isinstance(raw, list):
            raise DataInsufficientError("invalid Forex news provider response")
        for article in raw:
            try:
                data = _extract_article_data(article)
            except (TypeError, AttributeError, ValueError):
                continue
            if (data.get("title") in (None, "", "No title") or data.get("publisher") in (None, "Unknown", "")
                    or not str(data.get("link", "")).startswith(("https://", "http://"))
                    or data.get("pub_date") is None):
                continue
            try:
                published = utc_timestamp(data["pub_date"])
            except DataInsufficientError:
                continue
            if published > retrieved:
                continue
            summary = str(data.get("summary") or "")
            text = (data["title"] + " " + summary).lower()
            currencies = tuple(c for c, words in KEYWORDS.items() if any(w in text for w in words))
            affected = set(currencies) & {pair.base_currency, pair.quote_currency}
            if not affected:
                continue
            record = ForexNewsArticle(
                headline=data["title"], publisher=data["publisher"], url=data["link"],
                published_at_utc=published, retrieved_at_utc=retrieved,
                currencies=currencies, relevance=len(affected) / 2, summary=summary,
            )
            records.append(record.model_dump(mode="json"))
        snapshot = {"source": "Yahoo", "symbol": pair.symbol,
                    "retrieved_at_utc": retrieved.isoformat(), "records": records}
        save_snapshot(directory, snapshot)
        snapshots.append(snapshot)
        cutoff = retrieved
    start = cutoff - timedelta(days=lookback_days)
    results, seen_urls, seen_titles = [], set(), set()
    for snapshot in sorted(snapshots, key=lambda x: x.get("retrieved_at_utc", "")):
        if snapshot.get("symbol") != pair.symbol or utc_timestamp(snapshot["retrieved_at_utc"]) > cutoff:
            continue
        for raw in snapshot["records"]:
            item = ForexNewsArticle.model_validate(raw)
            if not start <= item.published_at_utc <= item.retrieved_at_utc <= cutoff:
                continue
            title = re.sub(r"\W+", "", item.headline.casefold())
            if item.url in seen_urls or title in seen_titles:
                continue
            seen_urls.add(item.url)
            seen_titles.add(title)
            results.append(item)
    if not results:
        raise DataInsufficientError("no verifiable Forex news in the requested historical window; coverage is unavailable")
    return sorted(results, key=lambda item: (item.published_at_utc, item.relevance), reverse=True)[:limit]

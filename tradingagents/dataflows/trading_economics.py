"""Trading Economics calendar adapter with immutable, observed-time archives.

Latest-value responses are never presented as historical vintages. Historical
queries require a snapshot observed at or before the requested cutoff.
"""

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.forex_archive import read_snapshots, save_snapshot
from tradingagents.dataflows.forex_quality import DataInsufficientError, utc_timestamp
from tradingagents.forex.domain import get_forex_pair

COUNTRIES = {
    "USD": ("united states",), "EUR": ("euro area", "germany", "france", "italy", "spain"),
    "GBP": ("united kingdom",), "JPY": ("japan",), "CHF": ("switzerland",),
    "AUD": ("australia",), "CAD": ("canada",), "NZD": ("new zealand",),
}


def _te_timestamp(value):
    # TE's documented JSON dates are UTC without a suffix.
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def _number(value, unit=""):
    if value in (None, "", "N/A", "-"):
        return None
    text = str(value).strip().replace(",", "")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?\s*[%KMBT]?", text, re.IGNORECASE):
        return None
    scales = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
    suffix = text[-1].upper()
    declared = str(unit).strip().upper()
    value = float(re.sub(r"[%KMBT\s]", "", text, flags=re.IGNORECASE))
    return value * scales.get(suffix, scales.get(declared, 1)) / scales.get(declared, 1)


class TradingEconomicsCalendar:
    def __init__(self, api_key=None, cache_dir=None, session=None, clock=None):
        config = get_config()
        self.api_key = api_key or os.getenv("TRADING_ECONOMICS_API_KEY") or os.getenv("TE_API_KEY")
        self.cache_dir = Path(cache_dir or config.get("forex_calendar_archive_dir")
                              or Path(config.get("data_cache_dir", "cache")) / "forex_calendar")
        self.session = session or requests.Session()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_age = config.get("forex_calendar_max_age_seconds", 900)

    def _countries(self, symbol):
        pair = get_forex_pair(symbol)
        if pair is None or any(c not in COUNTRIES for c in (pair.base_currency, pair.quote_currency)):
            raise DataInsufficientError("Trading Economics calendar currency coverage unavailable")
        return sorted({country for c in (pair.base_currency, pair.quote_currency) for country in COUNTRIES[c]})

    def _request(self, countries, start, end):
        if not self.api_key or self.api_key.lower().startswith("guest"):
            raise DataInsufficientError("TRADING_ECONOMICS_API_KEY is required for calendar coverage")
        url = "https://api.tradingeconomics.com/calendar/country/" + quote(",".join(countries)) + f"/{start}/{end}"
        for attempt in range(3):
            try:
                response = self.session.get(url, params={"c": self.api_key, "f": "json"}, timeout=20)
                if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                    time.sleep(0.25 * (2 ** attempt))
                    continue
                if response.status_code != 200:
                    raise DataInsufficientError(f"Trading Economics calendar unavailable (HTTP {response.status_code})")
                rows = response.json()
                if not isinstance(rows, list) or len(rows) >= 1000:
                    raise DataInsufficientError("Trading Economics incomplete or invalid calendar response")
                return rows
            except requests.RequestException:
                if attempt == 2:
                    # Request exceptions can contain the credential-bearing URL.
                    raise DataInsufficientError("Trading Economics calendar network failure") from None
                time.sleep(0.25 * (2 ** attempt))
            except (ValueError, TypeError) as exc:
                if isinstance(exc, DataInsufficientError):
                    raise
                raise DataInsufficientError("Trading Economics invalid JSON") from None
        raise DataInsufficientError("Trading Economics calendar unavailable")

    def refresh(self, symbol, start_date, end_date):
        """Fetch real records and retain an immutable snapshot with coverage metadata."""
        countries = self._countries(symbol)
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if start > end or (end - start).days > 366:
            raise ValueError("calendar window must be ordered and at most 366 days")
        rows = []
        day = start
        while day <= end:
            window_end = min(day + timedelta(days=6), end)
            rows.extend(self._request(countries, day.isoformat(), window_end.isoformat()))
            day = window_end + timedelta(days=1)
        retrieved = utc_timestamp(self.clock()).isoformat()
        snapshot = {"source": "Trading Economics", "retrieved_at_utc": retrieved,
                    "coverage_start": start_date, "coverage_end": end_date,
                    "countries": countries, "records": rows}
        # Validate the entire response before marking coverage as usable.
        self._events(snapshot)
        save_snapshot(self.cache_dir, snapshot)
        return snapshot

    def import_snapshot(self, path):
        """Import a trusted provider archive with its original observation timestamp.

        Importing today's download cannot create a historical vintage. The
        supplied observation time must come from the original collector.
        """
        try:
            snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
            if snapshot["source"] != "Trading Economics":
                raise ValueError("wrong provider")
            observed = utc_timestamp(snapshot["retrieved_at_utc"])
            if observed > utc_timestamp(self.clock()):
                raise ValueError("future observation")
            start, end = (date.fromisoformat(snapshot[key]) for key in ("coverage_start", "coverage_end"))
            if start > end or (end-start).days > 366 or not isinstance(snapshot["records"], list):
                raise ValueError("invalid coverage")
            supported = {country for countries in COUNTRIES.values() for country in countries}
            if not snapshot["countries"] or not set(snapshot["countries"]) <= supported:
                raise ValueError("invalid country coverage")
            self._events(snapshot)
        except (KeyError, ValueError, TypeError) as exc:
            raise DataInsufficientError("invalid Trading Economics archive import") from exc
        return save_snapshot(self.cache_dir, snapshot)

    def _events(self, snapshot):
        from tradingagents.forex.calendar import EconomicEvent, EventImpact

        events = []
        observed = utc_timestamp(snapshot["retrieved_at_utc"])
        for row in snapshot["records"]:
            try:
                country = str(row["Country"]).lower()
                if country not in snapshot["countries"]:
                    raise ValueError("unexpected country")
                currency = next(c for c, countries in COUNTRIES.items() if country in countries)
                scheduled = _te_timestamp(row["Date"])
                if not snapshot["coverage_start"] <= scheduled.date().isoformat() <= snapshot["coverage_end"]:
                    raise ValueError("outside coverage")
                updated = _te_timestamp(row["LastUpdate"])
                if updated > observed:
                    raise ValueError("future LastUpdate")
                if not row.get("CalendarId") or not row.get("Event") or not row.get("SourceURL"):
                    raise ValueError("missing provenance")
                actual = _number(row.get("Actual"), row.get("Unit"))
                events.append(EconomicEvent(
                    event_id=str(row["CalendarId"]), currency=currency, title=row["Event"],
                    impact={1: EventImpact.LOW, 2: EventImpact.MEDIUM, 3: EventImpact.HIGH}[int(row["Importance"])],
                    date=scheduled.date().isoformat(),
                    time_utc=scheduled.strftime("%H:%M:%S") if str(row.get("DateSpan", "0")) == "0" else None,
                    actual=actual, forecast=_number(row.get("Forecast"), row.get("Unit")), previous=_number(row.get("Previous"), row.get("Unit")),
                    unit=row.get("Unit") or "", country=row["Country"],
                    source="Trading Economics", source_url=row["SourceURL"],
                    retrieved_at_utc=observed, known_at_utc=observed,
                    # Conservative availability: a later update must not be backdated to scheduled release.
                    published_at_utc=max(scheduled, updated) if actual is not None else None,
                    revision_at_utc=updated if row.get("Revised") else None,
                    revised_previous=_number(row.get("Previous"), row.get("Unit")) if row.get("Revised") else None,
                    previous_before_revision=_number(row.get("Revised"), row.get("Unit")),
                    higher_is_bullish=not any(term in row["Event"].lower() for term in ("unemployment", "jobless")),
                ))
            except (KeyError, ValueError, TypeError, StopIteration):
                raise DataInsufficientError("Trading Economics calendar record lacks valid timing/provenance") from None
        if len({e.event_id for e in events}) != len(events):
            raise DataInsufficientError("duplicate Trading Economics calendar IDs")
        return events

    def query(self, symbol, start_date, end_date, as_of=None):
        countries = self._countries(symbol)
        cutoff = utc_timestamp(as_of) if as_of is not None else self.clock()
        candidates = []
        for item in read_snapshots(self.cache_dir):
            try:
                observed = utc_timestamp(item["retrieved_at_utc"])
                if (item["source"] == "Trading Economics" and observed <= cutoff
                        and (cutoff - observed).total_seconds() <= self.max_age
                        and set(countries) <= set(item["countries"])
                        and item["coverage_start"] <= start_date and item["coverage_end"] >= end_date):
                    candidates.append(item)
            except (OSError, ValueError, KeyError):
                continue
        if candidates:
            snapshot = max(candidates, key=lambda x: x["retrieved_at_utc"])
        elif as_of is None:
            snapshot = self.refresh(symbol, start_date, end_date)
            cutoff = utc_timestamp(snapshot["retrieved_at_utc"])
        else:
            raise DataInsufficientError("no timely Trading Economics calendar snapshot existed at cutoff; refresh live coverage or import an observed archive")
        return [event.clamp_to_as_of(cutoff.isoformat()) for event in self._events(snapshot)
                if start_date <= event.date <= end_date]

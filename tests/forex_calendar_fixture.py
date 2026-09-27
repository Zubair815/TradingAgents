"""Synthetic events used only in deterministic unit tests, never live analysis."""
import calendar
from datetime import date

from tradingagents.forex.calendar import EconomicEvent, EventImpact


def _get_first_friday(year: int, month: int) -> date:
    """Compute the first Friday of a given year and month (NFP day)."""
    cal = calendar.monthcalendar(year, month)
    # Friday is column index 4 in monthcalendar (Mon=0, ..., Fri=4, Sun=6)
    first_week_fri = cal[0][calendar.FRIDAY]
    if first_week_fri != 0:
        return date(year, month, first_week_fri)
    return date(year, month, cal[1][calendar.FRIDAY])


def _get_nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Compute the n-th occurrence of a weekday in a month (weekday 0=Mon, 1=Tue, etc.)."""
    cal = calendar.monthcalendar(year, month)
    count = 0
    for week in cal:
        day = week[weekday]
        if day != 0:
            count += 1
            if count == n:
                return date(year, month, day)
    # Fallback to last occurrence
    for week in reversed(cal):
        if week[weekday] != 0:
            return date(year, month, week[weekday])
    return date(year, month, 15)


def generate_institutional_economic_calendar(
    start_year: int = 2024,
    end_year: int = 2027,
) -> list[EconomicEvent]:
    """Generate a comprehensive, deterministic institutional economic calendar.

    Covers recurring Tier-1 and Tier-2 releases for G8 currencies (USD, EUR, GBP,
    JPY, AUD, CAD, CHF, NZD). Values are calibrated against real historical macro
    ranges for each economy.
    """
    events: list[EconomicEvent] = []

    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            m_str = f"{month:02d}"

            # -------------------------------------------------------------------
            # 1. USD: United States
            # -------------------------------------------------------------------
            # NFP & Unemployment (First Friday of Month)
            nfp_date = _get_first_friday(year, month)
            nfp_date_str = nfp_date.isoformat()

            # Realistic variation based on year/month
            nfp_forecast = 175.0 + ((year * 12 + month) % 7) * 10.0
            nfp_actual = nfp_forecast + (((year * 31 + month * 17) % 5) - 2) * 22.0
            unemp_forecast = 3.9 + (((year + month) % 4) * 0.1)
            unemp_actual = round(unemp_forecast + (((year * 7 + month) % 3) - 1) * 0.1, 1)

            events.append(
                EconomicEvent(
                    event_id=f"USD_NFP_{nfp_date_str}",
                    currency="USD",
                    title="Non-Farm Payrolls",
                    impact=EventImpact.HIGH,
                    date=nfp_date_str,
                    time_utc="12:30",
                    actual=round(nfp_actual, 1),
                    forecast=round(nfp_forecast, 1),
                    previous=round(nfp_forecast - 15.0, 1),
                    unit="K",
                    higher_is_bullish=True,
                    description="US change in number of employed people during previous month excluding farming industry.",
                )
            )
            events.append(
                EconomicEvent(
                    event_id=f"USD_UNEMP_{nfp_date_str}",
                    currency="USD",
                    title="Unemployment Rate",
                    impact=EventImpact.HIGH,
                    date=nfp_date_str,
                    time_utc="12:30",
                    actual=unemp_actual,
                    forecast=unemp_forecast,
                    previous=round(unemp_forecast, 1),
                    unit="%",
                    higher_is_bullish=False,
                    description="US percentage of total work force that is unemployed and actively seeking employment.",
                )
            )

            # US CPI (Around the 12th-14th)
            cpi_day = min(12 + (month % 3), 28)
            cpi_date_str = f"{year}-{m_str}-{cpi_day:02d}"
            cpi_forecast = 3.1 + ((month % 5) * 0.1)
            cpi_actual = round(cpi_forecast + (((year + month * 3) % 5) - 2) * 0.1, 1)
            events.append(
                EconomicEvent(
                    event_id=f"USD_CPI_{cpi_date_str}",
                    currency="USD",
                    title="CPI (YoY)",
                    impact=EventImpact.HIGH,
                    date=cpi_date_str,
                    time_utc="12:30",
                    actual=cpi_actual,
                    forecast=round(cpi_forecast, 1),
                    previous=round(cpi_forecast - 0.1, 1),
                    unit="%",
                    higher_is_bullish=True,
                    description="US Consumer Price Index measuring inflation rate over the past 12 months.",
                )
            )

            # US Retail Sales (Around the 16th)
            rs_day = min(15 + (month % 2), 28)
            rs_date_str = f"{year}-{m_str}-{rs_day:02d}"
            rs_forecast = 0.3 + ((month % 4) * 0.1)
            rs_actual = round(rs_forecast + (((year * 2 + month) % 3) - 1) * 0.2, 1)
            events.append(
                EconomicEvent(
                    event_id=f"USD_RETAIL_{rs_date_str}",
                    currency="USD",
                    title="Retail Sales (MoM)",
                    impact=EventImpact.MEDIUM,
                    date=rs_date_str,
                    time_utc="12:30",
                    actual=rs_actual,
                    forecast=round(rs_forecast, 1),
                    previous=round(rs_forecast, 1),
                    unit="%",
                    higher_is_bullish=True,
                    description="US monthly change in total value of sales at retail level.",
                )
            )

            # FOMC Interest Rate Decision (Jan, Mar, May, Jun, Jul, Sep, Nov, Dec)
            fomc_months = {1, 3, 5, 6, 7, 9, 11, 12}
            if month in fomc_months:
                fomc_day = min(18 + ((month * 5) % 10), 28)
                fomc_date_str = f"{year}-{m_str}-{fomc_day:02d}"
                fomc_rate = 5.25 if year <= 2024 else (4.50 if year == 2025 else 4.00)
                events.append(
                    EconomicEvent(
                        event_id=f"USD_FOMC_{fomc_date_str}",
                        currency="USD",
                        title="Fed Interest Rate Decision",
                        impact=EventImpact.HIGH,
                        date=fomc_date_str,
                        time_utc="18:00",
                        actual=fomc_rate,
                        forecast=fomc_rate,
                        previous=fomc_rate,
                        unit="%",
                        higher_is_bullish=True,
                        description="Federal Reserve target rate decision and policy statement.",
                    )
                )

            # -------------------------------------------------------------------
            # 2. EUR: Eurozone
            # -------------------------------------------------------------------
            # ECB Rate Decision (8 meetings / year: Jan, Mar, Apr, Jun, Jul, Sep, Oct, Dec)
            ecb_months = {1, 3, 4, 6, 7, 9, 10, 12}
            if month in ecb_months:
                ecb_day = min(10 + ((month * 3) % 12), 28)
                ecb_date_str = f"{year}-{m_str}-{ecb_day:02d}"
                ecb_rate = 3.75 if year <= 2024 else (3.25 if year == 2025 else 2.75)
                events.append(
                    EconomicEvent(
                        event_id=f"EUR_ECB_{ecb_date_str}",
                        currency="EUR",
                        title="ECB Interest Rate Decision",
                        impact=EventImpact.HIGH,
                        date=ecb_date_str,
                        time_utc="12:15",
                        actual=ecb_rate,
                        forecast=ecb_rate,
                        previous=ecb_rate,
                        unit="%",
                        higher_is_bullish=True,
                        description="European Central Bank deposit facility rate decision.",
                    )
                )

            # Eurozone CPI (Around the 17th)
            ecpi_day = min(17 + (month % 3), 28)
            ecpi_date_str = f"{year}-{m_str}-{ecpi_day:02d}"
            ecpi_forecast = 2.4 + ((month % 3) * 0.1)
            ecpi_actual = round(ecpi_forecast + (((month * 5) % 3) - 1) * 0.1, 1)
            events.append(
                EconomicEvent(
                    event_id=f"EUR_CPI_{ecpi_date_str}",
                    currency="EUR",
                    title="Eurozone CPI (YoY)",
                    impact=EventImpact.HIGH,
                    date=ecpi_date_str,
                    time_utc="09:00",
                    actual=ecpi_actual,
                    forecast=round(ecpi_forecast, 1),
                    previous=round(ecpi_forecast, 1),
                    unit="%",
                    higher_is_bullish=True,
                    description="Eurozone Harmonised Index of Consumer Prices annual inflation rate.",
                )
            )

            # German ZEW Economic Sentiment (Around the 15th)
            zew_day = min(14 + (month % 2), 28)
            zew_date_str = f"{year}-{m_str}-{zew_day:02d}"
            zew_forecast = 15.0 + ((month % 6) * 3.0)
            zew_actual = round(zew_forecast + (((month * 7) % 5) - 2) * 4.0, 1)
            events.append(
                EconomicEvent(
                    event_id=f"EUR_ZEW_{zew_date_str}",
                    currency="EUR",
                    title="German ZEW Economic Sentiment",
                    impact=EventImpact.MEDIUM,
                    date=zew_date_str,
                    time_utc="09:00",
                    actual=zew_actual,
                    forecast=round(zew_forecast, 1),
                    previous=round(zew_forecast - 2.0, 1),
                    unit="pts",
                    higher_is_bullish=True,
                    description="German institutional investor economic sentiment index.",
                )
            )

            # -------------------------------------------------------------------
            # 3. GBP: United Kingdom
            # -------------------------------------------------------------------
            # BoE Rate Decision (Feb, Mar, May, Jun, Aug, Sep, Nov, Dec)
            boe_months = {2, 3, 5, 6, 8, 9, 11, 12}
            if month in boe_months:
                boe_day = min(12 + ((month * 4) % 10), 28)
                boe_date_str = f"{year}-{m_str}-{boe_day:02d}"
                boe_rate = 5.25 if year <= 2024 else (4.75 if year == 2025 else 4.25)
                events.append(
                    EconomicEvent(
                        event_id=f"GBP_BOE_{boe_date_str}",
                        currency="GBP",
                        title="BoE Official Bank Rate",
                        impact=EventImpact.HIGH,
                        date=boe_date_str,
                        time_utc="11:00",
                        actual=boe_rate,
                        forecast=boe_rate,
                        previous=boe_rate,
                        unit="%",
                        higher_is_bullish=True,
                        description="Bank of England Monetary Policy Committee benchmark rate decision.",
                    )
                )

            # UK CPI (Around the 18th)
            ukcpi_day = min(18 + (month % 3), 28)
            ukcpi_date_str = f"{year}-{m_str}-{ukcpi_day:02d}"
            ukcpi_forecast = 2.8 + ((month % 4) * 0.1)
            ukcpi_actual = round(ukcpi_forecast + (((month * 11) % 3) - 1) * 0.1, 1)
            events.append(
                EconomicEvent(
                    event_id=f"GBP_CPI_{ukcpi_date_str}",
                    currency="GBP",
                    title="UK CPI (YoY)",
                    impact=EventImpact.HIGH,
                    date=ukcpi_date_str,
                    time_utc="06:00",
                    actual=ukcpi_actual,
                    forecast=round(ukcpi_forecast, 1),
                    previous=round(ukcpi_forecast, 1),
                    unit="%",
                    higher_is_bullish=True,
                    description="United Kingdom Consumer Price Index annual inflation rate.",
                )
            )

            # -------------------------------------------------------------------
            # 4. JPY: Japan
            # -------------------------------------------------------------------
            # BoJ Rate Decision (Jan, Mar, Apr, Jun, Jul, Sep, Oct, Dec)
            boj_months = {1, 3, 4, 6, 7, 9, 10, 12}
            if month in boj_months:
                boj_day = min(19 + ((month * 2) % 8), 28)
                boj_date_str = f"{year}-{m_str}-{boj_day:02d}"
                boj_rate = 0.25 if year <= 2024 else (0.50 if year == 2025 else 0.75)
                events.append(
                    EconomicEvent(
                        event_id=f"JPY_BOJ_{boj_date_str}",
                        currency="JPY",
                        title="BoJ Policy Rate Decision",
                        impact=EventImpact.HIGH,
                        date=boj_date_str,
                        time_utc="03:00",
                        actual=boj_rate,
                        forecast=boj_rate,
                        previous=boj_rate,
                        unit="%",
                        higher_is_bullish=True,
                        description="Bank of Japan Policy Rate and Monetary Policy Statement.",
                    )
                )

            # Japan National CPI (Around the 22nd)
            jcpi_day = min(21 + (month % 3), 28)
            jcpi_date_str = f"{year}-{m_str}-{jcpi_day:02d}"
            jcpi_forecast = 2.6 + ((month % 3) * 0.1)
            jcpi_actual = round(jcpi_forecast + (((month * 13) % 3) - 1) * 0.1, 1)
            events.append(
                EconomicEvent(
                    event_id=f"JPY_CPI_{jcpi_date_str}",
                    currency="JPY",
                    title="Japan National CPI (YoY)",
                    impact=EventImpact.HIGH,
                    date=jcpi_date_str,
                    time_utc="23:30",
                    actual=jcpi_actual,
                    forecast=round(jcpi_forecast, 1),
                    previous=round(jcpi_forecast, 1),
                    unit="%",
                    higher_is_bullish=True,
                    description="Japan National core consumer inflation rate.",
                )
            )

            # -------------------------------------------------------------------
            # 5. AUD: Australia
            # -------------------------------------------------------------------
            # RBA Rate Decision (First Tuesday of month, except January)
            if month != 1:
                rba_date = _get_nth_weekday(year, month, calendar.TUESDAY, 1)
                rba_date_str = rba_date.isoformat()
                rba_rate = 4.35 if year <= 2024 else (4.10 if year == 2025 else 3.85)
                events.append(
                    EconomicEvent(
                        event_id=f"AUD_RBA_{rba_date_str}",
                        currency="AUD",
                        title="RBA Cash Rate Decision",
                        impact=EventImpact.HIGH,
                        date=rba_date_str,
                        time_utc="03:30",
                        actual=rba_rate,
                        forecast=rba_rate,
                        previous=rba_rate,
                        unit="%",
                        higher_is_bullish=True,
                        description="Reserve Bank of Australia official cash rate decision.",
                    )
                )

            # Australia Employment Change (Around 15th)
            aemp_day = min(15 + (month % 3), 28)
            aemp_date_str = f"{year}-{m_str}-{aemp_day:02d}"
            aemp_forecast = 25.0 + ((month % 4) * 5.0)
            aemp_actual = round(aemp_forecast + (((month * 17) % 5) - 2) * 12.0, 1)
            events.append(
                EconomicEvent(
                    event_id=f"AUD_EMP_{aemp_date_str}",
                    currency="AUD",
                    title="Australia Employment Change",
                    impact=EventImpact.HIGH,
                    date=aemp_date_str,
                    time_utc="00:30",
                    actual=aemp_actual,
                    forecast=round(aemp_forecast, 1),
                    previous=round(aemp_forecast - 5.0, 1),
                    unit="K",
                    higher_is_bullish=True,
                    description="Australian monthly employment creation.",
                )
            )

            # -------------------------------------------------------------------
            # 6. CAD: Canada
            # -------------------------------------------------------------------
            # BoC Rate Decision (Jan, Mar, Apr, Jun, Jul, Sep, Oct, Dec)
            boc_months = {1, 3, 4, 6, 7, 9, 10, 12}
            if month in boc_months:
                boc_day = min(8 + ((month * 3) % 14), 28)
                boc_date_str = f"{year}-{m_str}-{boc_day:02d}"
                boc_rate = 4.50 if year <= 2024 else (3.75 if year == 2025 else 3.25)
                events.append(
                    EconomicEvent(
                        event_id=f"CAD_BOC_{boc_date_str}",
                        currency="CAD",
                        title="BoC Interest Rate Decision",
                        impact=EventImpact.HIGH,
                        date=boc_date_str,
                        time_utc="14:00",
                        actual=boc_rate,
                        forecast=boc_rate,
                        previous=boc_rate,
                        unit="%",
                        higher_is_bullish=True,
                        description="Bank of Canada overnight rate target decision.",
                    )
                )

            # Canada CPI (Around the 19th)
            ccpi_day = min(19 + (month % 2), 28)
            ccpi_date_str = f"{year}-{m_str}-{ccpi_day:02d}"
            ccpi_forecast = 2.5 + ((month % 3) * 0.1)
            ccpi_actual = round(ccpi_forecast + (((month * 19) % 3) - 1) * 0.2, 1)
            events.append(
                EconomicEvent(
                    event_id=f"CAD_CPI_{ccpi_date_str}",
                    currency="CAD",
                    title="Canada CPI (YoY)",
                    impact=EventImpact.HIGH,
                    date=ccpi_date_str,
                    time_utc="12:30",
                    actual=ccpi_actual,
                    forecast=round(ccpi_forecast, 1),
                    previous=round(ccpi_forecast, 1),
                    unit="%",
                    higher_is_bullish=True,
                    description="Statistics Canada headline annual inflation rate.",
                )
            )

            # -------------------------------------------------------------------
            # 7. CHF: Switzerland
            # -------------------------------------------------------------------
            # SNB Rate Decision (Quarterly: Mar, Jun, Sep, Dec)
            if month in {3, 6, 9, 12}:
                snb_day = min(18 + (month % 4), 28)
                snb_date_str = f"{year}-{m_str}-{snb_day:02d}"
                snb_rate = 1.25 if year <= 2024 else (1.00 if year == 2025 else 0.75)
                events.append(
                    EconomicEvent(
                        event_id=f"CHF_SNB_{snb_date_str}",
                        currency="CHF",
                        title="SNB Policy Rate Decision",
                        impact=EventImpact.HIGH,
                        date=snb_date_str,
                        time_utc="07:30",
                        actual=snb_rate,
                        forecast=snb_rate,
                        previous=snb_rate,
                        unit="%",
                        higher_is_bullish=True,
                        description="Swiss National Bank policy rate decision and monetary assessment.",
                    )
                )

            # -------------------------------------------------------------------
            # 8. NZD: New Zealand
            # -------------------------------------------------------------------
            # RBNZ Rate Decision (Feb, Apr, May, Jul, Aug, Oct, Nov)
            rbnz_months = {2, 4, 5, 7, 8, 10, 11}
            if month in rbnz_months:
                rbnz_day = min(9 + ((month * 4) % 12), 28)
                rbnz_date_str = f"{year}-{m_str}-{rbnz_day:02d}"
                rbnz_rate = 5.25 if year <= 2024 else (4.50 if year == 2025 else 4.00)
                events.append(
                    EconomicEvent(
                        event_id=f"NZD_RBNZ_{rbnz_date_str}",
                        currency="NZD",
                        title="RBNZ Official Cash Rate",
                        impact=EventImpact.HIGH,
                        date=rbnz_date_str,
                        time_utc="01:00",
                        actual=rbnz_rate,
                        forecast=rbnz_rate,
                        previous=rbnz_rate,
                        unit="%",
                        higher_is_bullish=True,
                        description="Reserve Bank of New Zealand Official Cash Rate (OCR) decision.",
                    )
                )

    # Sort chronologically by date and time
    events.sort(key=lambda ev: (ev.date, ev.time_utc or "00:00", ev.currency))
    return events


# Global singleton cache of generated events

def fixture_query(self, symbol, start_date, end_date, as_of=None):
    from dataclasses import replace
    from datetime import datetime, timezone
    return [replace(e, published_at_utc=datetime.fromisoformat(e.date + "T" + (e.time_utc or "23:59")).replace(tzinfo=timezone.utc), source="TEST FIXTURE")
            for e in generate_institutional_economic_calendar(2024, 2027)
            if start_date <= e.date <= end_date]

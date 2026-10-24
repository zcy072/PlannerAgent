"""Date-match weather forecasts to the requested trip window."""

from datetime import date, timedelta

from ..models.schemas import TripRequest, Weather


def has_forecast(item: Weather) -> bool:
    """False for the placeholder records created for dates without a forecast."""
    return bool(item.day_weather) and item.day_weather != "未知"


_BAD_OUTDOOR_WEATHER = ("雨", "雪", "雷", "雾", "霾", "沙", "冰雹", "大风")
_HIGH_TEMP_C = 35
_LOW_TEMP_C = -5


def is_unfavorable_for_outdoor(item: Weather) -> bool:
    """Daytime weather that makes long outdoor sightseeing unpleasant; unknown days are not."""
    if not has_forecast(item):
        return False
    if any(keyword in item.day_weather for keyword in _BAD_OUTDOOR_WEATHER):
        return True
    try:
        temp = float(item.day_temp)
    except (TypeError, ValueError):
        return False
    return temp >= _HIGH_TEMP_C or temp <= _LOW_TEMP_C


def uncovered_dates(matched: list[Weather]) -> list[str]:
    """Trip dates that still hold the "unknown" placeholder."""
    return [item.date for item in matched if not has_forecast(item)]


def merge_supplemental_weather(
    matched: list[Weather], supplemental: list[Weather],
) -> list[Weather]:
    """Fill placeholder dates from a secondary source; real Amap forecasts are never replaced."""
    extra = {item.date: item for item in supplemental if has_forecast(item)}
    return [
        item if has_forecast(item) else extra.get(item.date, item)
        for item in matched
    ]


def match_weather_to_trip_dates(
    forecasts: list[Weather], request: TripRequest,
) -> list[Weather]:
    """Return one weather record per trip date, marking uncovered dates unknown."""
    start = date.fromisoformat(request.start_date)
    by_date = {item.date: item for item in forecasts if item.date}
    matched = []

    for offset in range(request.travel_days):
        trip_date = (start + timedelta(days=offset)).isoformat()
        matched.append(by_date.get(trip_date) or Weather(
            date=trip_date,
            day_weather="未知",
            night_weather="未知",
            day_temp="未知",
            night_temp="未知",
            wind_direction="未知",
            wind_power="未知",
        ))

    return matched

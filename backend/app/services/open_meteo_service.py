"""Open-Meteo 天气补充：高德预报只覆盖近几天，这里用免费接口补齐更远的日期。"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, List

import httpx
from loguru import logger

from ..models.schemas import Weather

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE_LABEL = "Open-Meteo"
# 接口最多提供 16 天预报，超出范围的日期整体请求会被拒绝，所以请求前先裁剪
MAX_FORECAST_DAYS = 16

# WMO weather interpretation codes -> 中文描述
_WMO_DESCRIPTIONS = {
    0: "晴", 1: "晴", 2: "多云", 3: "阴",
    45: "雾", 48: "雾",
    51: "小雨", 53: "小雨", 55: "中雨", 56: "冻雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "小雪",
    80: "阵雨", 81: "阵雨", 82: "暴雨",
    85: "阵雪", 86: "阵雪",
    95: "雷阵雨", 96: "雷阵雨", 99: "雷阵雨",
}

_WIND_DIRECTIONS = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
# 蒲福风级上限（km/h），用于换算成与高德一致的"风力等级"
_BEAUFORT_UPPER_KMH = [1, 5, 11, 19, 28, 38, 49, 61, 74, 88, 102, 117]


def describe_weather_code(code: Any) -> str:
    try:
        return _WMO_DESCRIPTIONS.get(int(code), "")
    except (TypeError, ValueError):
        return ""


def wind_direction_name(degrees: Any) -> str:
    try:
        value = float(degrees)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(value):
        return ""
    return _WIND_DIRECTIONS[int(((value % 360) + 22.5) // 45) % 8]


def wind_power_level(speed_kmh: Any) -> str:
    try:
        value = float(speed_kmh)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(value) or value < 0:
        return ""
    for level, upper in enumerate(_BEAUFORT_UPPER_KMH):
        if value <= upper:
            return str(level)
    return "12"


def clamp_dates_to_forecast_window(dates: List[str], today: date | None = None) -> List[str]:
    """只保留 Open-Meteo 能预报的日期（今天起 16 天内）。"""
    today = today or date.today()
    last = today + timedelta(days=MAX_FORECAST_DAYS - 1)
    kept = []
    for item in dates:
        try:
            parsed = date.fromisoformat(item)
        except ValueError:
            continue
        if today <= parsed <= last:
            kept.append(item)
    return kept


def parse_daily_forecast(payload: Any, wanted_dates: List[str]) -> List[Weather]:
    """把 Open-Meteo 的 daily 数组转成 Weather；缺失字段的日期直接跳过。"""
    daily = payload.get("daily") if isinstance(payload, dict) else None
    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        return []

    wanted = set(wanted_dates)
    result: List[Weather] = []
    for index, day in enumerate(daily["time"]):
        if day not in wanted:
            continue

        def column(name: str) -> Any:
            values = daily.get(name)
            return values[index] if isinstance(values, list) and index < len(values) else None

        description = describe_weather_code(column("weather_code"))
        high, low = column("temperature_2m_max"), column("temperature_2m_min")
        if not description or not isinstance(high, (int, float)) or not isinstance(low, (int, float)):
            continue

        result.append(Weather(
            date=day,
            day_weather=description,
            night_weather=description,
            day_temp=int(round(high)),
            night_temp=int(round(low)),
            wind_direction=wind_direction_name(column("wind_direction_10m_dominant")),
            wind_power=wind_power_level(column("wind_speed_10m_max")),
            source=SOURCE_LABEL,
        ))
    return result


async def fetch_forecast(
    longitude: float, latitude: float, dates: List[str], timeout: float = 8.0,
) -> List[Weather]:
    """按坐标获取指定日期的日预报；日期超出窗口或请求失败时返回空列表。"""
    dates = clamp_dates_to_forecast_window(dates)
    if not dates:
        return []

    params = {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "wind_speed_10m_max,wind_direction_10m_dominant",
        "timezone": "Asia/Shanghai",
        "start_date": min(dates),
        "end_date": max(dates),
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(FORECAST_URL, params=params)
            response.raise_for_status()
            return parse_daily_forecast(response.json(), dates)
    except Exception as exc:
        # 补充数据不是关键路径，失败只降级为"暂无预报"
        logger.warning("[OpenMeteo] 天气补充失败 | error_type={}", type(exc).__name__)
        return []

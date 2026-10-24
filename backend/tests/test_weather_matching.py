import unittest
from datetime import date

from app.models.schemas import TripRequest, Weather
from app.planning.weather import match_weather_to_trip_dates


class WeatherMatchingTests(unittest.TestCase):
    def request(self, start="2026-11-02", end="2026-11-04"):
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        return TripRequest(
            city="北京", start_date=start, end_date=end,
            travel_days=days,
        )

    def test_keeps_only_requested_dates_and_fills_unknown_dates(self):
        forecasts = [
            Weather(date="2026-11-01", day_weather="过期"),
            Weather(date="2026-11-02", day_weather="晴"),
            Weather(date="2026-11-04", day_weather="雨"),
            Weather(date="2026-11-10", day_weather="超前"),
        ]

        result = match_weather_to_trip_dates(forecasts, self.request())

        self.assertEqual([item.date for item in result], [
            "2026-11-02", "2026-11-03", "2026-11-04",
        ])
        self.assertEqual(result[0].day_weather, "晴")
        self.assertEqual(result[1].day_weather, "未知")
        self.assertEqual(result[1].day_temp, "未知")
        self.assertEqual(result[2].day_weather, "雨")

    def test_unknown_dates_are_not_presented_to_llm_as_weather(self):
        from app.agents.planner_agent import _format_weather
        from app.agents.poi_agent import _format_weather_context

        result = match_weather_to_trip_dates(
            [Weather(date="2026-11-02", day_weather="雨", night_weather="阴",
                     day_temp="10", night_temp="5")],
            self.request(),
        )

        planner_text = _format_weather(result)
        self.assertIn("2026-11-02: 雨", planner_text)
        self.assertIn("2026-11-03: 暂无预报", planner_text)
        self.assertNotIn("未知°C", planner_text)

        poi_text = _format_weather_context(result)
        self.assertNotIn("2026-11-03", poi_text)
        self.assertNotIn("未知°C", poi_text)

        only_unknown = match_weather_to_trip_dates([], self.request())
        self.assertIn("暂无天气数据", _format_weather_context(only_unknown))

    def test_weather_failure_still_exposes_each_trip_date_as_unknown(self):
        result = match_weather_to_trip_dates([], self.request("2026-12-01", "2026-12-02"))

        self.assertEqual([item.date for item in result], ["2026-12-01", "2026-12-02"])
        self.assertTrue(all(item.day_weather == "未知" for item in result))


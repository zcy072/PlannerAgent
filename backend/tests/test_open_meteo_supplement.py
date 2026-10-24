import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.schemas import Location, TripRequest, Weather
from app.planning.weather import (
    match_weather_to_trip_dates,
    merge_supplemental_weather,
    uncovered_dates,
)
from app.services import open_meteo_service as om
from app.workflows.trip.graph import weather_node
from app.workflows.trip.sse import events_for_update


def _request(start="2026-06-10", days=3) -> TripRequest:
    return TripRequest(
        city="郑州", start_date=start, end_date="2026-06-12", travel_days=days,
        transportation="公共交通", accommodation="经济型酒店", preferences=[],
    )


def _amap_cast(day: str) -> Weather:
    return Weather(date=day, day_weather="晴", night_weather="多云", day_temp=30, night_temp=20)


class OpenMeteoParsingTests(unittest.TestCase):
    def test_wmo_code_and_wind_conversion(self):
        self.assertEqual(om.describe_weather_code(65), "大雨")
        self.assertEqual(om.describe_weather_code(999), "")
        self.assertEqual(om.describe_weather_code(None), "")
        self.assertEqual(om.wind_direction_name(0), "北")
        self.assertEqual(om.wind_direction_name(359), "北")
        self.assertEqual(om.wind_direction_name(135), "东南")
        self.assertEqual(om.wind_power_level(0), "0")
        self.assertEqual(om.wind_power_level(20), "4")
        self.assertEqual(om.wind_power_level(500), "12")
        self.assertEqual(om.wind_power_level("bad"), "")

    def test_clamps_dates_to_forecast_window(self):
        today = date(2026, 6, 1)
        kept = om.clamp_dates_to_forecast_window(
            ["2026-05-31", "2026-06-01", "2026-06-16", "2026-06-17", "not-a-date"], today=today,
        )
        self.assertEqual(kept, ["2026-06-01", "2026-06-16"])

    def test_parse_daily_forecast_marks_source_and_skips_bad_days(self):
        payload = {"daily": {
            "time": ["2026-06-10", "2026-06-11", "2026-06-12"],
            "weather_code": [0, None, 61],
            "temperature_2m_max": [31.4, 30.0, 25.6],
            "temperature_2m_min": [21.2, 20.0, 18.1],
            "wind_speed_10m_max": [12, 10, 25],
            "wind_direction_10m_dominant": [90, 90, 180],
        }}
        result = om.parse_daily_forecast(payload, ["2026-06-10", "2026-06-11", "2026-06-12"])

        self.assertEqual([item.date for item in result], ["2026-06-10", "2026-06-12"])
        self.assertEqual(result[0].day_weather, "晴")
        self.assertEqual(result[0].day_temp, 31)
        self.assertEqual(result[0].wind_direction, "东")
        self.assertEqual(result[1].day_weather, "小雨")
        self.assertTrue(all(item.source == om.SOURCE_LABEL for item in result))

    def test_parse_rejects_malformed_payloads(self):
        self.assertEqual(om.parse_daily_forecast(None, ["2026-06-10"]), [])
        self.assertEqual(om.parse_daily_forecast({"daily": {"time": "x"}}, ["2026-06-10"]), [])


class OpenMeteoFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_out_of_window_dates_make_no_request(self):
        with patch.object(om.httpx, "AsyncClient") as client:
            self.assertEqual(await om.fetch_forecast(113.6, 34.7, ["2000-01-01"]), [])
        client.assert_not_called()

    async def test_request_failure_degrades_to_empty(self):
        today = date.today().isoformat()
        with patch.object(om.httpx, "AsyncClient", side_effect=RuntimeError("boom")):
            self.assertEqual(await om.fetch_forecast(113.6, 34.7, [today]), [])


class MergeTests(unittest.TestCase):
    def test_merge_only_fills_placeholders(self):
        matched = match_weather_to_trip_dates([_amap_cast("2026-06-10")], _request())
        extra = [
            Weather(date="2026-06-10", day_weather="暴雨", day_temp=1, night_temp=1, source="Open-Meteo"),
            Weather(date="2026-06-11", day_weather="阴", day_temp=28, night_temp=19, source="Open-Meteo"),
        ]

        merged = merge_supplemental_weather(matched, extra)

        self.assertEqual(merged[0].day_weather, "晴")  # 高德真实预报不被覆盖
        self.assertEqual(merged[1].day_weather, "阴")
        self.assertEqual(merged[1].source, "Open-Meteo")
        self.assertEqual(uncovered_dates(merged), ["2026-06-12"])


class WeatherNodeSupplementTests(unittest.IsolatedAsyncioTestCase):
    def _state(self):
        return {"request": _request(), "node_traces": []}

    def _patches(self, amap, enabled=True, supplement=None):
        return (
            patch("app.workflows.trip.graph.get_settings",
                  return_value=SimpleNamespace(open_meteo_enabled=enabled)),
            patch("app.workflows.trip.graph.get_amap_service", return_value=amap),
            patch("app.workflows.trip.graph.fetch_open_meteo_forecast",
                  new=supplement or AsyncMock(return_value=[])),
        )

    async def test_fills_uncovered_dates_and_reports_in_trace(self):
        amap = SimpleNamespace(
            get_weather=AsyncMock(return_value=[_amap_cast("2026-06-10")]),
            geocode=AsyncMock(return_value=Location(longitude=113.6, latitude=34.7)),
        )
        supplement = AsyncMock(return_value=[
            Weather(date="2026-06-11", day_weather="阴", day_temp=28, night_temp=19, source="Open-Meteo"),
            Weather(date="2026-06-12", day_weather="雨", day_temp=26, night_temp=18, source="Open-Meteo"),
        ])
        p1, p2, p3 = self._patches(amap, supplement=supplement)
        with p1, p2, p3:
            update = await weather_node(self._state())

        self.assertEqual([w.day_weather for w in update["weather"]], ["晴", "阴", "雨"])
        supplement.assert_awaited_once()
        self.assertEqual(supplement.await_args.args[2], ["2026-06-11", "2026-06-12"])
        self.assertIn("Open-Meteo 补充 2 天", update["node_traces"][0]["summary"])

    async def test_disabled_flag_skips_supplement(self):
        amap = SimpleNamespace(get_weather=AsyncMock(return_value=[]), geocode=AsyncMock())
        supplement = AsyncMock()
        p1, p2, p3 = self._patches(amap, enabled=False, supplement=supplement)
        with p1, p2, p3:
            update = await weather_node(self._state())

        supplement.assert_not_awaited()
        amap.geocode.assert_not_awaited()
        self.assertTrue(all(w.day_weather == "未知" for w in update["weather"]))

    async def test_geocode_failure_keeps_unknown_placeholders(self):
        amap = SimpleNamespace(
            get_weather=AsyncMock(return_value=[]),
            geocode=AsyncMock(side_effect=RuntimeError("geocode down")),
        )
        p1, p2, p3 = self._patches(amap)
        with p1, p2, p3:
            update = await weather_node(self._state())

        self.assertEqual(len(update["weather"]), 3)
        self.assertTrue(all(w.day_weather == "未知" for w in update["weather"]))

    async def test_fully_covered_trip_makes_no_extra_calls(self):
        days = ["2026-06-10", "2026-06-11", "2026-06-12"]
        amap = SimpleNamespace(
            get_weather=AsyncMock(return_value=[_amap_cast(d) for d in days]),
            geocode=AsyncMock(),
        )
        supplement = AsyncMock()
        p1, p2, p3 = self._patches(amap, supplement=supplement)
        with p1, p2, p3:
            await weather_node(self._state())

        amap.geocode.assert_not_awaited()
        supplement.assert_not_awaited()


class CompleteEventTraceTests(unittest.TestCase):
    def test_complete_event_carries_accumulated_node_traces(self):
        traces = [{"node": "weather", "status": "completed", "duration_ms": 5, "summary": "ok"}]
        state = {"latest_trip_plan": {"days": []}}
        fields = {
            "trip_plan": {"days": []},
            "evaluator_report": {"passed": True, "issues": []},
            "retry_count": 0,
            "node_traces": traces,
        }

        events = [json.loads(e[6:]) for e in events_for_update("s1", "quality_gate", fields, state, 1)]

        complete = events[-1]
        self.assertEqual(complete["node"], "complete")
        self.assertEqual(complete["data"]["itinerary"]["node_traces"], traces)


if __name__ == "__main__":
    unittest.main()

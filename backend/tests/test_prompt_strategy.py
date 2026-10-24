"""Tests for weather-aware day assignment, hotel price labelling, and free-text passthrough."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.hotel_agent import run_hotel_agent
from app.agents.planner_agent import _format_hotels
from app.agents.poi_agent import _filter_target
from app.models.schemas import Hotel, Location, POI, Weather
from app.planning.itinerary_optimizer import optimize_poi_order
from app.planning.weather import is_unfavorable_for_outdoor


def poi(name: str, longitude: float, latitude: float = 30.0, category: str = "景点") -> POI:
    return POI(
        name=name, address=f"{name}路", category=category,
        location=Location(longitude=longitude, latitude=latitude),
    )


def forecast(date: str, day_weather: str, day_temp="20") -> Weather:
    return Weather(date=date, day_weather=day_weather, night_weather="多云",
                   day_temp=day_temp, night_temp="10")


class UnfavorableWeatherTests(unittest.TestCase):
    def test_rain_and_extreme_temperature_are_unfavorable(self):
        self.assertTrue(is_unfavorable_for_outdoor(forecast("d", "中雨")))
        self.assertTrue(is_unfavorable_for_outdoor(forecast("d", "晴", "37")))
        self.assertTrue(is_unfavorable_for_outdoor(forecast("d", "晴", "-8")))

    def test_clear_and_unknown_days_are_not(self):
        self.assertFalse(is_unfavorable_for_outdoor(forecast("d", "晴")))
        self.assertFalse(is_unfavorable_for_outdoor(forecast("d", "未知", "未知")))


class WeatherAwareRouteTests(unittest.TestCase):
    def pois(self):
        # Two well separated areas: a west indoor cluster and an east outdoor cluster.
        return [
            poi("东公园", 113.50), poi("东山", 113.51),
            poi("西博物馆", 113.00, category="博物馆"), poi("西美术馆", 113.01, category="美术馆"),
        ]

    def attraction_names(self, routes):
        return [[a["name"] for a in day["attractions"]] for day in routes]

    def test_indoor_group_moves_to_rainy_day(self):
        base = optimize_poi_order(self.pois(), 2)
        # Make the day currently holding the OUTDOOR group the rainy one.
        outdoor_day = 1 - base_indoor_day(base)
        weather = [forecast("d1", "晴"), forecast("d2", "晴")]
        weather[outdoor_day] = forecast("dy", "大雨")

        routes = optimize_poi_order(self.pois(), 2, weather=weather)

        self.assertEqual(base_indoor_day(routes), outdoor_day)
        self.assertIn("按天气调整", routes[outdoor_day]["optimization_reason"])

    def test_grouping_is_preserved(self):
        base = optimize_poi_order(self.pois(), 2)
        weather = [forecast("d1", "暴雨"), forecast("d2", "晴")]
        routes = optimize_poi_order(self.pois(), 2, weather=weather)
        self.assertEqual(
            sorted(map(sorted, self.attraction_names(base))),
            sorted(map(sorted, self.attraction_names(routes))),
        )

    def test_no_change_without_weather_or_when_every_day_is_bad(self):
        base = self.attraction_names(optimize_poi_order(self.pois(), 2))
        self.assertEqual(base, self.attraction_names(optimize_poi_order(self.pois(), 2, weather=[])))
        all_bad = [forecast("d1", "雨"), forecast("d2", "雪")]
        self.assertEqual(base, self.attraction_names(optimize_poi_order(self.pois(), 2, weather=all_bad)))

    def test_clear_weather_adds_no_weather_note(self):
        routes = optimize_poi_order(
            self.pois(), 2, weather=[forecast("d1", "晴"), forecast("d2", "晴")],
        )
        self.assertTrue(all("按天气" not in day["optimization_reason"] for day in routes))


def base_indoor_day(routes) -> int:
    for day in routes:
        if any(a["category"] in ("博物馆", "美术馆") for a in day["attractions"]):
            return day["day_index"]
    raise AssertionError("no indoor day")


class FilterTargetTests(unittest.TestCase):
    def test_scales_with_days_and_is_capped_by_pool(self):
        self.assertEqual(_filter_target(1), 12)
        self.assertEqual(_filter_target(5), 16)
        self.assertEqual(_filter_target(30), 20)


class HotelPriceLabelTests(unittest.TestCase):
    def test_placeholder_price_is_flagged_to_planner(self):
        estimated = Hotel(name="甲", price_range="预算推算约 ¥300/晚（非实时房价）", estimated_cost=300)
        real = Hotel(name="乙", price_range="¥420", estimated_cost=420)
        text = _format_hotels([estimated, real])
        self.assertIn("无真实报价", text)
        self.assertIn("价格: ¥420", text)
        self.assertEqual(text.count("无真实报价"), 1)


class HotelAgentFreeTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_free_text_and_nightly_budget_reach_prompts_and_prices_precede_filter(self):
        hotel = Hotel(name="甲", address="a")
        seen = []

        class FakeChain:
            def __init__(self, label):
                self.label = label

            async def ainvoke(self, variables):
                seen.append((self.label, variables, hotel.estimated_cost))
                return MagicMock(content='["酒店"]' if self.label == "strategy" else "[1]")

        class FakePrompt:
            def __init__(self, template):
                self.label = "strategy" if "生成 2-3" in template else "filter"

            def __or__(self, _llm):
                return FakeChain(self.label)

        amap = MagicMock()
        amap.search_hotels = AsyncMock(return_value=[hotel])
        with patch("app.agents.hotel_agent.ChatPromptTemplate.from_template", side_effect=FakePrompt), \
                patch("app.agents.hotel_agent.get_llm", return_value=MagicMock()), \
                patch("app.agents.hotel_agent.get_amap_service", return_value=amap):
            await run_hotel_agent("武汉", "舒适型", [3000, 5000], "deepseek",
                                  travel_days=3, free_text_input="带老人，不要爬楼")

        strategy, filtering = seen
        self.assertEqual(strategy[1]["free_text_input"], "带老人，不要爬楼")
        self.assertEqual(filtering[1]["free_text_input"], "带老人，不要爬楼")
        self.assertIn("每晚约", filtering[1]["budget_info"])
        self.assertGreater(filtering[2], 0, "prices must be filled before the LLM filter runs")


if __name__ == "__main__":
    unittest.main()

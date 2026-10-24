"""Route metrics must belong to the same complete alternative."""
import unittest
import json
from types import SimpleNamespace

from app.services.amap_mcp_service import _extract_route_metric


class McpMetricTests(unittest.TestCase):
    def test_live_driving_text_response_shape(self):
        payload = {'paths': [{'distance': '15605', 'duration': '2402',
                              'steps': [{'distance': '137', 'duration': '50'}]}]}
        metric = _extract_route_metric(SimpleNamespace(
            isError=False, structuredContent=None,
            content=[SimpleNamespace(text=json.dumps(payload))]))
        self.assertAlmostEqual(metric.distance_km, 15.605)
        self.assertEqual(metric.duration_min, 40)

    def test_live_transit_without_alternative_distance_falls_back(self):
        # Official MCP omits per-alternative distance in this observed response.
        for transits in ([], [{'duration': '6042', 'walking_distance': '3941',
                              'segments': [{'walking': {'distance': '1762'},
                                            'bus': {'buslines': [{'distance': '10420'}]}}]}]):
            with self.subTest(transits=transits):
                self.assertIsNone(self.metric({'distance': '15117', 'transits': transits}))

    def test_live_walking_text_response_shape(self):
        # Reduced from the official walking tool response observed on 2026-10-09.
        payload = {'route': {'origin': '116.397026,39.918058',
            'destination': '116.396718,39.925091', 'paths': [
                {'distance': 1311, 'duration': 1049,
                 'steps': [{'distance': 17, 'duration': 14}]}]}}
        metric = _extract_route_metric(SimpleNamespace(
            isError=False, structuredContent=None,
            content=[SimpleNamespace(text=json.dumps(payload))]))
        self.assertAlmostEqual(metric.distance_km, 1.311)
        self.assertEqual(metric.duration_min, 17)

    def metric(self, payload):
        return _extract_route_metric(SimpleNamespace(
            isError=False, structuredContent=payload, content=[]))

    def test_duration_is_not_taken_from_steps(self):
        metric = self.metric({'route': {'paths': [{'distance': '5000',
            'steps': [{'duration': '60'}]}]}})
        self.assertEqual(metric.distance_km, 5)
        self.assertIsNone(metric.duration_min)

    def test_transit_uses_one_complete_alternative(self):
        metric = self.metric({'route': {'distance': '9000', 'transits': [
            {'distance': '6000', 'duration': '1800'},
            {'distance': '7000', 'duration': '2400'}]}})
        self.assertEqual(metric.distance_km, 6)
        self.assertEqual(metric.duration_min, 30)

    def test_cost_duration_from_same_path(self):
        metric = self.metric({'data': {'route': {'paths': [
            {'distance': '3000', 'cost': {'duration': '600'}}]}}})
        self.assertEqual(metric.duration_min, 10)

    def test_segment_only_or_unknown_structure_is_rejected(self):
        for payload in ({'route': {'segments': [{'distance': '100'}]}},
                        {'unknown': {'distance': '100'}},
                        {'status': '0', 'route': {'distance': '100'}}):
            with self.subTest(payload=payload):
                self.assertIsNone(self.metric(payload))

    def test_duration_not_borrowed_from_other_alternative(self):
        metric = self.metric({'paths': [{'distance': '3000'},
                                       {'distance': '4000', 'duration': '600'}]})
        self.assertEqual(metric.distance_km, 3)
        self.assertIsNone(metric.duration_min)

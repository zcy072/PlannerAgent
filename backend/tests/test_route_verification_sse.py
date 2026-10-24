"""Optional route verification progress must not become a fatal SSE event."""
import json
import unittest

from app.workflows.trip.sse import events_for_update


class RouteVerificationSseTests(unittest.TestCase):
    def events(self, traces):
        return [json.loads(event[6:]) for event in events_for_update(
            'session', 'route_optimizer',
            {'route_skeleton': [{}], 'node_traces': traces}, {}, 1)]

    def test_disabled_preserves_existing_events(self):
        events = self.events([])
        self.assertEqual([e['node'] for e in events], ['route_optimizer', 'planner'])

    def test_partial_verification_summary_precedes_planner(self):
        summary = '高德 MCP 已校验 1 天、2 段真实路径'
        events = self.events([{'node': 'route_verification', 'summary': summary,
                               'status': 'completed'}])
        self.assertEqual([e['node'] for e in events],
                         ['route_optimizer', 'route_verification', 'planner'])
        self.assertEqual(events[1]['message'], summary)

    def test_fallback_is_informational_not_fatal(self):
        summary = '路线验证失败，保留坐标估算'
        events = self.events([{'node': 'route_verification', 'summary': summary,
                               'status': 'failed'}])
        self.assertEqual(events[1]['status'], 'completed')
        self.assertEqual(events[1]['message'], summary)
        self.assertEqual(events[-1]['node'], 'planner')

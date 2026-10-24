"""Shared configuration constants for planning agents and services."""

# POI Agent
POI_MAX_KEYWORDS = 5
POI_CANDIDATE_POOL_SIZE = 25
POI_PER_KEYWORD_LIMIT = 5
POI_FILTER_TARGET = 12

# Hotel Agent
HOTEL_MAX_KEYWORDS = 3
HOTEL_CANDIDATE_POOL_SIZE = 15
HOTEL_PER_KEYWORD_LIMIT = 3
HOTEL_FILTER_TARGET = 5

# Marks hotel prices that are budget-derived placeholders rather than Amap data.
BUDGET_ESTIMATE_MARKER = "预算推算"

# Planner Agent
PLANNER_MAX_POIS_DISPLAY = 15
PLANNER_MAX_HOTELS_DISPLAY = 5

# Deterministic rule evaluator
MAX_DAILY_MINUTES = 600
AVERAGE_ROUTE_SPEED_KMH = 30
WALKING_ROUTE_SPEED_KMH = 4.5
CYCLING_ROUTE_SPEED_KMH = 15
TRANSIT_ROUTE_SPEED_KMH = 20
# Conservative per-kilometre transport assumptions used only when no explicit
# transport amount is available in the generated plan.
WALKING_COST_PER_KM = 0
CYCLING_COST_PER_KM = 0.2
TRANSIT_COST_PER_KM = 0.8
DRIVING_COST_PER_KM = 1.2
BUDGET_LOWER_TOLERANCE = 0.5
BUDGET_UPPER_TOLERANCE = 0.25

# Chat service
CHAT_HISTORY_LIMIT = 20
CHAT_SESSION_HISTORY_LIMIT = 1000
CHAT_MESSAGE_MAX_CHARS = 1000
CHAT_SUGGESTION_LIMIT = 3

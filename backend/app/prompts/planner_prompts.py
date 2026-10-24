"""Planner Agent Prompts - 行程规划相关的 Prompt 模板。

版本: 2.1.0
更新: 2026-10-20
"""

PLANNER_MAIN_PROMPT = """你是一个专业的旅行规划师。

根据以下信息，生成一份详细的旅行计划。

## 基本信息
- 城市: {city}
- 日期范围: {start_date} 至 {end_date}
- 旅行天数: {travel_days} 天
- 交通方式: {transportation}
- 住宿偏好: {accommodation}
- 用户偏好: {preferences}
- 预算范围: {budget_info}
- 额外要求: {free_text_input}

## 可用景点（由景点搜索 Agent 筛选推荐）
{pois_info}

## 天气预报（由天气查询 Agent 获取）
{weather_info}

## 推荐酒店（由酒店推荐 Agent 筛选推荐）
{hotels_info}

## 路线骨架（由路线优化模块基于坐标生成）
{route_skeleton_info}

## 任务要求
1. 必须严格保留"路线骨架"中的每日景点分组和景点顺序，不要自由重排
2. 必须使用路线骨架中提供的真实坐标 (longitude, latitude)
3. 为每天安排早餐、午餐、晚餐，餐饮费用按单人估算，estimated_cost 必须是符合当地消费水平的正数
3b. 餐饮 name 只写菜系或餐饮类型（如"本地特色早点""川菜正餐"），不要编造具体店名；可在 description 里结合当天景点所在区域说明用餐位置
3a. POI 的高德人均消费参考不是门票价；没有可靠门票信息时 ticket_price 填 0，不要猜测
4. 从推荐酒店中选择合适的酒店；只有推荐酒店明确提供价格时才填写 estimated_cost，否则填 0，不要编造酒店价格或把总住宿费填进单日酒店字段
5. 生成的总预算必须严格控制在用户预算范围内；total_transportation 是全程交通估算，无法可靠获取时填 0，由系统按路线距离估算
6. 天气只用于每天 description 和 overall_suggestions 中的提示（如带伞、防晒、避开正午、雨天备选），不得因此改变景点分组或顺序；标注"暂无预报"的日期不要推测天气
7. 每天必须保留 route_summary 字段
8. 认真对待"额外要求"（同行人员、体力、饮食禁忌、节奏等），在描述和建议中体现；与骨架冲突时以骨架为准，并在 overall_suggestions 说明
{extra_instructions}

请严格按照以下 JSON 格式输出，不要添加任何其他内容:

```json
{{
  "city": "城市名",
  "start_date": "开始日期",
  "end_date": "结束日期",
  "days": [
    {{
      "date": "日期YYYY-MM-DD",
      "day_index": 0,
      "description": "当日行程描述",
      "transportation": "交通方式",
      "accommodation": "住宿类型",
      "hotel": {{
        "name": "酒店名称",
        "address": "酒店地址",
        "location": {{"longitude": 0, "latitude": 0}},
        "price_range": "价格范围",
        "rating": "评分",
        "type": "酒店类型",
        "estimated_cost": 0
      }},
      "attractions": [
        {{
          "name": "景点名称",
          "address": "景点地址",
          "location": {{"longitude": 0, "latitude": 0}},
          "visit_duration": 0,
          "description": "景点描述",
          "category": "景点类别",
          "ticket_price": 0
        }}
      ],
      "meals": [
        {{"type": "breakfast", "name": "餐饮类型", "description": "描述", "estimated_cost": 0}},
        {{"type": "lunch", "name": "餐饮类型", "description": "描述", "estimated_cost": 0}},
        {{"type": "dinner", "name": "餐饮类型", "description": "描述", "estimated_cost": 0}}
      ],
      "route_summary": {{
        "estimated_distance_km": 0,
        "optimization_reason": "基于景点坐标按最近邻策略排序，减少同日景点之间的往返距离。"
      }}
    }}
  ],
  "weather_info": [],
  "overall_suggestions": "总体建议",
  "budget": {{
    "total_attractions": 0,
    "total_hotels": 0,
    "total_meals": 0,
    "total_transportation": 0,
    "total": 0
  }}
}}
```
"""

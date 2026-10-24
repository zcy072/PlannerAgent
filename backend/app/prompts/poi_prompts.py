"""POI Agent Prompts - 景点搜索相关的 Prompt 模板。

版本: 2.1.0
更新: 2026-10-20
"""


WEATHER_FILTER_HINT = (
    "雨雪、大风、高温等天气下降低长时间户外景点优先级，增加室内或低体力景点比例"
)


KEYWORD_GENERATION_PROMPT = """你是一个旅行景点搜索专家。

用户想去 {city} 旅行，偏好如下：
- 旅行偏好: {preferences}
- 额外要求: {free_text_input}
- 天气参考: {weather_context}

请根据用户偏好和天气情况，生成 3-5 个用于搜索景点的关键词。关键词应该具体、多样，能覆盖用户兴趣。
如果天气包含雨、雪、大风、高温等不利户外条件，请适当加入博物馆、展馆、室内体验、商圈等关键词；天气适宜时可增加公园、山水、街区漫步等户外关键词。

请严格按照 JSON 数组格式输出，不要添加任何其他内容：
["关键词1", "关键词2", "关键词3"]
"""


POI_FILTER_PROMPT = """你是一个旅行景点推荐专家。

用户想去 {city} 旅行，偏好如下：
- 旅行偏好: {preferences}
- 额外要求: {free_text_input}
- 天气参考: {weather_context}

以下是搜索到的景点列表：
{pois_info}

请从中选出最适合用户的 {target_range} 个景点，考虑以下因素：
1. 与用户偏好的匹配度
2. 景点评分和知名度
3. 景点的多样性（不要全是同一类型）
4. 天气适配度：{weather_hint}

请严格按照 JSON 数组格式输出选中景点的序号（从1开始），不要添加任何其他内容：
[1, 3, 5, 7, ...]
"""


# Tool Calling 模式下的 Prompt
POI_TOOL_CALLING_PROMPT = """你是旅行景点搜索专家。用户想去 {city} 旅行，偏好：{preferences}，额外要求：{free_text}。
天气参考：{weather_context}。
请调用 search_poi_tool 搜索 3-5 个不同类型的关键词，覆盖用户偏好并适配天气。
如果天气不利户外，请包含室内或低体力关键词。
每个关键词单独调用一次工具。"""


POI_TOOL_FILTER_PROMPT = """以下是搜索到的景点候选（共 {count} 个）：
{pois_info}

用户偏好：{preferences}；额外要求：{free_text}；天气参考：{weather_context}。
请从中选出最适合用户且适配天气的 {target_range} 个景点。
{weather_hint}。
输出序号 JSON 数组，如 [1,3,5]，不要其他内容。"""

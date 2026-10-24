"""通用格式化工具：将数据结构格式化为 LLM 友好的文本。

提取自 poi_agent.py 和 hotel_agent.py 的重复代码。
"""

from typing import List

from ...models.schemas import POI, Hotel


def format_pois_for_filter(pois: List[POI]) -> str:
    """格式化 POI 列表供 LLM 筛选。

    Args:
        pois: POI 列表

    Returns:
        格式化的文本，每行一个 POI
    """
    lines = []
    for i, poi in enumerate(pois, 1):
        line = f"{i}. {poi.name} - {poi.address}"
        if poi.category:
            line += f" [{poi.category}]"
        if poi.rating:
            line += f" 评分:{poi.rating}"
        lines.append(line)
    return "\n".join(lines)


def format_hotels_for_filter(hotels: List[Hotel]) -> str:
    """格式化酒店列表供 LLM 筛选。

    Args:
        hotels: 酒店列表

    Returns:
        格式化的文本，每行一个酒店
    """
    lines = []
    for i, hotel in enumerate(hotels, 1):
        line = f"{i}. {hotel.name} - {hotel.address}"
        if hotel.rating:
            line += f" 评分:{hotel.rating}"
        if hotel.price_range:
            line += f" 价格:{hotel.price_range}"
        elif hotel.estimated_cost:
            line += f" 每晚参考价:¥{hotel.estimated_cost}（非实时房价）"
        if hotel.type:
            line += f" [{hotel.type}]"
        lines.append(line)
    return "\n".join(lines)

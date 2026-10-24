"""Amap tools for LLM tool calling."""

import time

from langchain_core.tools import tool
from loguru import logger

from ...models.schemas import POI
from ...services.amap_service import get_amap_service


@tool
async def search_poi_tool(keywords: str, city: str) -> dict:
    """搜索城市内的景点 POI。返回景点列表和候选数量。

    Args:
        keywords: 搜索关键词，如"历史古迹"、"自然风景"
        city: 目标城市名称
    """
    started_at = time.perf_counter()
    amap = get_amap_service()
    pois = await amap.search_poi(keywords, city, citylimit=True)
    duration_ms = int((time.perf_counter() - started_at) * 1000)

    logger.debug(f"[Tool] search_poi_tool | 关键词: {keywords} | 返回: {len(pois)} 个 | {duration_ms}ms")
    return {
        "pois": [_poi_to_dict(poi) for poi in pois],
        "count": len(pois),
        "keywords": keywords,
        "duration_ms": duration_ms,
    }


def _poi_to_dict(poi: POI) -> dict:
    return {
        "id": poi.id,
        "name": poi.name,
        "address": poi.address,
        "category": poi.category,
        "rating": poi.rating,
        "tel": poi.tel,
        "ticket_price": poi.ticket_price,
        "cost_reference": poi.cost_reference,
        "visit_duration": poi.visit_duration,
        "description": poi.description,
        "image_url": poi.image_url,
        "location": {
            "longitude": poi.location.longitude,
            "latitude": poi.location.latitude,
        } if poi.location else None,
    }

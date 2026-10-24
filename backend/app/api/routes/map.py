"""地图服务 API 路由"""

import re

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from typing import Optional

from ...services.amap_service import get_amap_service

router = APIRouter(prefix="/map", tags=["地图服务"])

# markers 是 "经度,纬度" 以 "|" 分隔的列表；上限避免构造超长上游请求。
_MARKERS_PATTERN = re.compile(r"^-?\d{1,3}(\.\d+)?,-?\d{1,2}(\.\d+)?(\|-?\d{1,3}(\.\d+)?,-?\d{1,2}(\.\d+)?){0,49}$")
_IMAGE_SIGNATURES = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8")


@router.get("/poi")
async def search_poi(
    keywords: str = Query(..., min_length=1, max_length=50, description="搜索关键词"),
    city: str = Query(..., min_length=1, max_length=50, description="城市")
):
    """搜索 POI"""
    amap = get_amap_service()
    pois = await amap.search_poi(keywords, city)

    return {
        "success": True,
        "data": [poi.model_dump() for poi in pois]
    }


@router.get("/weather")
async def get_weather(city: str = Query(..., min_length=1, max_length=50, description="城市")):
    """获取天气"""
    amap = get_amap_service()
    weather = await amap.get_weather(city)

    return {
        "success": True,
        "data": [w.model_dump() for w in weather]
    }


@router.get("/hotels")
async def search_hotels(
    city: str = Query(..., min_length=1, max_length=50, description="城市"),
    hotel_type: str = Query(default="酒店", max_length=50, description="酒店类型")
):
    """搜索酒店"""
    amap = get_amap_service()
    hotels = await amap.search_hotels(city, hotel_type)

    return {
        "success": True,
        "data": [h.model_dump() for h in hotels]
    }


@router.get("/nearby-restaurants")
async def search_nearby_restaurants(
    longitude: float = Query(..., ge=-180, le=180),
    latitude: float = Query(..., ge=-90, le=90),
    radius: int = Query(default=3000, ge=500, le=5000),
):
    """按坐标搜索附近餐饮 POI。"""
    amap = get_amap_service()
    pois = await amap.search_nearby_restaurants(longitude, latitude, radius=radius)
    return {"success": True, "data": [poi.model_dump() for poi in pois]}


@router.get("/staticmap")
async def get_static_map(
    city: str = Query(..., min_length=1, max_length=50, description="城市"),
    markers: Optional[str] = Query(default=None, max_length=1500, description="标记点")
):
    """获取静态地图图片（用于导出）"""
    if markers and not _MARKERS_PATTERN.match(markers):
        raise HTTPException(status_code=422, detail="markers 格式应为 经度,纬度|经度,纬度")
    amap = get_amap_service()

    image_data = await amap.get_static_map(city, markers)
    # 高德出错时返回 200 + JSON 错误体，不能当成图片转发。
    if not image_data.startswith(_IMAGE_SIGNATURES):
        raise HTTPException(status_code=502, detail="地图服务未返回图片")

    return Response(
        content=image_data,
        media_type="image/png"
    )


@router.get("/geocode")
async def geocode(
    address: str = Query(..., min_length=1, max_length=100, description="地址"),
    city: Optional[str] = Query(default=None, max_length=50, description="城市")
):
    """地理编码"""
    amap = get_amap_service()
    location = await amap.geocode(address, city)

    if not location:
        raise HTTPException(status_code=404, detail="无法解析地址")

    return {
        "success": True,
        "data": location.model_dump()
    }

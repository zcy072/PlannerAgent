"""数据模型定义"""

from datetime import date as _date
import re
from typing import Annotated, List, Optional, Union, Dict, Any, Literal
from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator
from enum import Enum


# 会话 ID 由客户端生成（UUID 或 session_ 前缀的随机串），限制字符集和长度，
# 避免异常值进入 checkpoint 线程 ID、结果文件查找和日志。
SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


def validate_session_id(value: str) -> str:
    if not SESSION_ID_PATTERN.fullmatch(value):
        raise ValueError("session_id 只能包含字母、数字、下划线和连字符，长度 1-64")
    return value


SessionId = Annotated[str, AfterValidator(validate_session_id)]


# ===========================================
# 枚举类型
# ===========================================

class LLMProvider(str, Enum):
    DEEPSEEK = "deepseek"
    ALIYUN = "aliyun"
    OPENAI = "openai"


class TripStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ===========================================
# 基础数据模型
# ===========================================

class Location(BaseModel):
    """地理位置"""
    longitude: float = Field(..., ge=-180, le=180, description="经度")
    latitude: float = Field(..., ge=-90, le=90, description="纬度")


class POI(BaseModel):
    """兴趣点"""
    id: str = Field(default="", description="POI ID")
    name: str = Field(..., description="名称")
    address: str = Field(default="", description="地址")
    location: Optional[Location] = Field(default=None, description="坐标")
    category: Optional[str] = Field(default=None, description="类别")
    rating: Optional[float] = Field(default=None, description="评分")
    tel: Optional[str] = Field(default=None, description="电话")
    ticket_price: Optional[int] = Field(default=0, ge=0, description="门票价格")
    cost_reference: Optional[int] = Field(default=None, ge=0, description="高德 POI 人均消费参考价，不代表门票价")
    distance_m: Optional[int] = Field(default=None, ge=0, description="周边搜索距离（米）")
    visit_duration: Optional[int] = Field(default=120, ge=0, description="建议游览时长(分钟)")
    description: Optional[str] = Field(default=None, description="描述")
    image_url: Optional[str] = Field(default=None, description="图片URL")


class Weather(BaseModel):
    """天气信息"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    day_temp: Union[int, str] = Field(default=0, description="白天温度")
    night_temp: Union[int, str] = Field(default=0, description="夜间温度")
    wind_direction: str = Field(default="", description="风向")
    wind_power: str = Field(default="", description="风力")
    source: str = Field(default="", description="补充数据来源；空表示高德")


class Hotel(BaseModel):
    """酒店信息"""
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="地址")
    location: Optional[Location] = Field(default=None, description="坐标")
    price_range: str = Field(default="", description="价格范围")
    rating: str = Field(default="", description="评分")
    type: str = Field(default="", description="酒店类型")
    estimated_cost: int = Field(default=0, ge=0, description="预估费用")

    @field_validator("rating", mode="before")
    @classmethod
    def _coerce_rating_to_str(cls, value: Any) -> str:
        """高德返回的 rating 可能是数字（4.8），统一成字符串便于前端展示。"""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return str(value)


class Meal(BaseModel):
    """餐饮信息"""
    type: str = Field(..., description="类型: breakfast/lunch/dinner")
    name: str = Field(..., description="名称")
    address: Optional[str] = Field(default=None, description="地址")
    description: Optional[str] = Field(default=None, description="描述")
    estimated_cost: int = Field(default=0, ge=0, description="预估费用")


class Attraction(BaseModel):
    """景点信息（行程中的景点）"""
    name: str = Field(..., description="名称")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="坐标")
    visit_duration: int = Field(default=120, ge=0, description="游览时长(分钟)")
    description: str = Field(default="", description="描述")
    category: Optional[str] = Field(default=None, description="类别")
    ticket_price: int = Field(default=0, ge=0, description="门票价格")


class RouteSummary(BaseModel):
    """路线优化摘要"""
    estimated_distance_km: float = Field(default=0, ge=0, description="同日景点间预估距离(公里；MCP 校验后为道路距离)")
    estimated_duration_min: Optional[int] = Field(default=None, ge=0, description="经路径服务校验的预估通行时间(分钟)")
    optimization_reason: str = Field(default="", description="路线排序原因")


class DayPlan(BaseModel):
    """单日行程"""
    date: str = Field(..., description="日期")
    day_index: int = Field(..., ge=0, description="第几天(从0开始)")
    description: str = Field(..., description="当日描述")
    transportation: str = Field(default="", description="交通方式")
    accommodation: str = Field(default="", description="住宿")
    hotel: Optional[Hotel] = Field(default=None, description="推荐酒店")
    attractions: List[Attraction] = Field(default_factory=list, description="景点列表")
    meals: List[Meal] = Field(default_factory=list, description="餐饮列表")
    route_summary: Optional[RouteSummary] = Field(default=None, description="路线优化摘要")


class Budget(BaseModel):
    """预算信息"""
    total_attractions: int = Field(default=0, ge=0, description="景点门票总费用")
    total_hotels: int = Field(default=0, ge=0, description="酒店总费用")
    total_meals: int = Field(default=0, ge=0, description="餐饮总费用")
    total_transportation: int = Field(default=0, ge=0, description="交通总费用")
    transportation_is_estimated: bool = Field(default=False, description="交通费来自路线距离估算，修改后需重算")
    total: int = Field(default=0, ge=0, description="总费用")


class EvaluatorReportModel(BaseModel):
    """确定性规则检查报告"""
    passed: bool = Field(default=True, description="是否通过全部规则检查")
    issues: List[str] = Field(default_factory=list, description="发现的问题")
    suggestions: List[str] = Field(default_factory=list, description="给 Planner 的改进建议")
    day_issues: Dict[int, List[str]] = Field(default_factory=dict, description="按 day_index 分组的单日问题")
    global_issues: List[str] = Field(default_factory=list, description="无法归属到单日的全局问题")
    non_retryable_issues: List[str] = Field(default_factory=list, description="当前 Planner 重试无法修复的问题")


class NodeTraceModel(BaseModel):
    """一次工作流节点执行摘要。"""
    node: str
    status: str
    duration_ms: int = Field(ge=0)
    summary: str


class TripPlan(BaseModel):
    """旅行计划"""
    city: str = Field(..., description="城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(default_factory=list, description="每日行程")
    weather_info: List[Weather] = Field(default_factory=list, description="天气信息")
    overall_suggestions: str = Field(default="", description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")
    pois_pool: List[POI] = Field(default_factory=list, description="POI 候选池（用于增量替换）")
    hotels_pool: List[Hotel] = Field(default_factory=list, description="酒店候选池（用于增量替换）")
    evaluator_report: Optional[EvaluatorReportModel] = Field(default=None, description="自动评估报告")
    evaluator_warnings: List[str] = Field(default_factory=list, description="达到重试上限后保留的评估警告")
    node_traces: List[NodeTraceModel] = Field(default_factory=list, description="工作流节点执行轨迹")


# ===========================================
# 请求模型
# ===========================================

class TripRequest(BaseModel):
    """旅行规划请求"""
    session_id: str = Field(default="", description="会话ID；留空由服务端生成")
    city: str = Field(..., min_length=1, max_length=50, description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    travel_days: int = Field(..., ge=1, le=30, description="旅行天数")
    transportation: str = Field(default="公共交通", description="交通方式")
    accommodation: str = Field(default="经济型酒店", description="住宿偏好")
    preferences: List[str] = Field(default_factory=list, max_length=20, description="旅行偏好")
    free_text_input: Optional[str] = Field(default="", max_length=500, description="额外要求")
    llm_provider: Optional[LLMProvider] = Field(default=None, description="指定LLM提供商")
    budget: Optional[List[int]] = Field(default=None, min_length=2, max_length=2, description="预算范围 [最小值, 最大值]")

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, value: str) -> str:
        return validate_session_id(value) if value else value

    @field_validator("city")
    @classmethod
    def _validate_city(cls, value: str) -> str:
        city = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff ·'()（）-]+", city):
            raise ValueError("city 只能包含中文、字母、数字、空格及常用名称符号")
        return city

    @model_validator(mode="after")
    def _validate_dates_and_budget(self):
        """校验日期、天数和预算范围，避免错误请求进入 Agent 流程后才失败。"""
        # fromisoformat 只接受 YYYY-MM-DD，拒绝 "2026-1-5" 这类下游无法解析的写法。
        start = _date.fromisoformat(self.start_date)
        end = _date.fromisoformat(self.end_date)
        if end < start:
            raise ValueError("end_date 不能早于 start_date")

        actual_days = (end - start).days + 1
        if actual_days != self.travel_days:
            raise ValueError("travel_days 必须与 start_date/end_date 的日期范围一致")

        if self.budget is not None:
            min_budget, max_budget = self.budget
            if min_budget < 0 or max_budget < 0:
                raise ValueError("budget 不能为负数")
            if min_budget > max_budget:
                raise ValueError("budget 最小值不能大于最大值")

        return self


class ItemModifyRequest(BaseModel):
    """行程项目局部修改请求"""
    session_id: SessionId = Field(..., description="会话ID，用于加载持久化的行程")
    day_index: int = Field(..., ge=0, description="第几天（0-based）")
    item_type: Literal["attraction", "hotel"] = Field(..., description="类型: attraction / hotel")
    item_index: Optional[int] = Field(default=None, ge=0, description="景点列表中的下标，attraction 必填")
    action: Literal["delete", "replace"] = Field(..., description="动作: delete / replace")
    replacement: Optional[Dict[str, Any]] = Field(
        default=None,
        description="replace 时必填：可来自候选池或前端自定义搜索结果（POI / Hotel 完整 dict）",
    )
    llm_provider: Optional[LLMProvider] = Field(default=None, description="LLM 供应商，用于重写描述")


class ItemModifyResponse(BaseModel):
    """行程项目局部修改响应"""
    success: bool
    message: str
    trip_plan: Optional[TripPlan] = None
    changed_day_index: Optional[int] = None
    persistence_saved: bool = Field(default=True, description="修改是否已持久化")
    persistence_warning: Optional[str] = Field(default=None, description="持久化失败提示")


class ChatRequest(BaseModel):
    """对话式行程修改请求"""
    session_id: SessionId = Field(..., description="会话ID")
    message: str = Field(..., min_length=1, max_length=500, description="用户自然语言消息")
    llm_provider: Optional[LLMProvider] = Field(default=None, description="LLM 供应商")


class ChatResponse(BaseModel):
    """对话式行程修改响应"""
    success: bool = Field(default=True)
    reply: str = Field(default="", description="助手回复文本")
    trip_plan: Optional[TripPlan] = Field(default=None, description="修改后的行程；未修改时为 null")
    modified: bool = Field(default=False, description="是否实际修改了行程")
    action: Literal["delete", "replace", "answer", "clarify"] = Field(
        default="answer", description="意图：delete/replace/answer/clarify"
    )
    persistence_saved: Optional[bool] = Field(default=None, description="修改是否已持久化")
    persistence_warning: Optional[str] = Field(default=None, description="持久化失败提示")


class StreamingResponse(BaseModel):
    """流式响应"""
    session_id: str = Field(..., description="会话ID")
    step: int = Field(..., description="步骤序号")
    node: str = Field(..., description="当前节点")
    status: TripStatus = Field(..., description="状态")
    message: str = Field(default="", description="消息")
    data: Optional[Dict[str, Any]] = Field(default=None, description="数据")

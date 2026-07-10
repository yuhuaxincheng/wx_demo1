from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class RecommendRequest(BaseModel):
    store_id: str = "default_store"
    table_id: str = ""
    channel: str = "qr"
    people_count: str = Field(..., description="用餐人数，例如：1人、2人、3-4人、一家人、聚餐")
    budget_range: str = Field(..., description="预算范围，例如：50元以内、100元以内、不限制")
    budget_amount: Optional[float] = Field(None, description="用户填写的具体预算")
    meal_goal: str = Field(..., description="用餐目标，例如：减脂高蛋白、孩子吃、想吃辣解馋")
    dining_type: str = "未选择"
    need_staple: str = "都可以"
    taste: str = Field(..., description="清淡、正常、重口味")
    spicy_level: str = "都可以"
    avoid: str = ""
    note: str = ""


class RecommendItem(BaseModel):
    product_id: str
    name: str
    price: float
    spec: str = ""
    reason: str = ""


class Recommendation(BaseModel):
    title: str
    total_price: float
    fit_score: int = 80
    items: List[RecommendItem]
    reason: str
    upsell: str = ""
    warning: str = ""


class RecommendResponse(BaseModel):
    mode: str
    request_summary: str
    recommendations: List[Recommendation]
    debug: dict = {}

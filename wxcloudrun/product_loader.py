from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import math


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_EXCEL_PATH = BASE_DIR / "data" / "肉包公_AI推荐系统_测试商品库.xlsx"
DEFAULT_JSON_PATH = BASE_DIR / "data" / "seed_products.json"


@dataclass
class Product:
    product_id: str
    name: str
    category: str
    sub_category: str
    is_combo: bool
    price: float
    cost: float
    gross_margin: float
    spec: str
    unit: str
    people_min: int
    people_max: int
    budget_min: float
    budget_max: float
    stock: int
    status: str
    priority: int
    prep_minutes: int
    dine_in: bool
    takeaway: bool
    delivery: bool
    taste: str
    spicy: str
    scene_tags: List[str]
    suitable_people: List[str]
    unsuitable_people: List[str]
    diet_tags: List[str]
    allergy_note: str
    need_heat: bool
    description: str
    ingredients: str
    add_on: str
    ai_summary: str
    test_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _split_tags(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, float) and math.isnan(value):
        return []
    text = str(value).replace("，", ",").replace("、", ",")
    return [x.strip() for x in text.split(",") if x.strip()]


def _to_bool(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"是", "true", "1", "yes", "y"}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, float) and math.isnan(value):
            return default
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, float) and math.isnan(value):
            return default
        return int(float(value))
    except Exception:
        return default


def _row_to_product(row: Dict[str, Any]) -> Product:
    return Product(
        product_id=str(row.get("产品ID", "")).strip(),
        name=str(row.get("商品名称", "")).strip(),
        category=str(row.get("类目", "")).strip(),
        sub_category=str(row.get("子类", "")).strip(),
        is_combo=_to_bool(row.get("是否套餐")),
        price=_to_float(row.get("售价")),
        cost=_to_float(row.get("成本估算")),
        gross_margin=_to_float(row.get("毛利率(公式)")),
        spec=str(row.get("规格", "")).strip(),
        unit=str(row.get("单位", "")).strip(),
        people_min=max(1, _to_int(row.get("建议人数下限"), 1)),
        people_max=max(1, _to_int(row.get("建议人数上限"), 1)),
        budget_min=_to_float(row.get("推荐预算下限")),
        budget_max=_to_float(row.get("推荐预算上限"), 9999.0),
        stock=max(0, _to_int(row.get("库存"))),
        status=str(row.get("上架状态", "上架")).strip(),
        priority=max(1, min(10, _to_int(row.get("推荐权重(1-10)"), 5))),
        prep_minutes=max(0, _to_int(row.get("出餐时间(分钟)"))),
        dine_in=_to_bool(row.get("堂食可用")),
        takeaway=_to_bool(row.get("外带可用")),
        delivery=_to_bool(row.get("外卖可用")),
        taste=str(row.get("口味强度", "正常")).strip(),
        spicy=str(row.get("辣度", "都可以")).strip(),
        scene_tags=_split_tags(row.get("适合场景标签")),
        suitable_people=_split_tags(row.get("适合人群")),
        unsuitable_people=_split_tags(row.get("不适合人群")),
        diet_tags=_split_tags(row.get("饮食标签")),
        allergy_note=str(row.get("过敏/忌口提示", "")).strip(),
        need_heat=_to_bool(row.get("是否需要加热")),
        description=str(row.get("商品描述", "")).strip(),
        ingredients=str(row.get("组合内容/原料", "")).strip(),
        add_on=str(row.get("加购建议", "")).strip(),
        ai_summary=str(row.get("AI提示词商品摘要", "")).strip(),
        test_note=str(row.get("测试备注", "")).strip(),
    )


def load_products(excel_path: Path = DEFAULT_EXCEL_PATH) -> List[Product]:
    """优先读取 Excel 商品库；失败时读取 JSON 兜底数据。"""
    if excel_path.exists():
        try:
            import pandas as pd
            df = pd.read_excel(excel_path, sheet_name="商品库")
            products = [_row_to_product(row) for row in df.to_dict(orient="records")]
            return [p for p in products if p.product_id and p.name]
        except Exception as exc:
            print(f"[WARN] Excel 商品库读取失败，改用 JSON 兜底数据：{exc}")

    if DEFAULT_JSON_PATH.exists():
        data = json.loads(DEFAULT_JSON_PATH.read_text(encoding="utf-8"))
        return [Product(**item) for item in data]

    return []


def export_products_json(products: List[Product], path: Path = DEFAULT_JSON_PATH) -> None:
    path.write_text(
        json.dumps([p.to_dict() for p in products], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

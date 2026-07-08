from __future__ import annotations

from typing import Dict, List, Tuple
import itertools
import re

from .product_loader import Product
from .schemas import RecommendItem, Recommendation, RecommendRequest


SPICY_ORDER = {
    "不辣": 0,
    "微辣": 1,
    "中辣": 2,
    "麻辣": 3,
    "重辣": 4,
    "都可以": 99,
    "": 99,
}


def parse_budget(req: RecommendRequest) -> float:
    if req.budget_amount and req.budget_amount > 0:
        return float(req.budget_amount)
    text = req.budget_range or ""
    if "不限制" in text:
        return 9999.0
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1))
    return 100.0


def parse_people(req: RecommendRequest) -> int:
    text = req.people_count or "1人"
    if "聚餐" in text:
        return 6
    if "一家" in text:
        return 4
    if "3-4" in text:
        return 4
    m = re.search(r"(\d+)", text)
    if m:
        return int(m.group(1))
    return 1


def _contains_any(text: str, keywords: List[str]) -> bool:
    return any(k and k in text for k in keywords)


def is_allowed_by_hard_rules(p: Product, req: RecommendRequest) -> Tuple[bool, str]:
    if p.status != "上架":
        return False, "未上架"
    if p.stock <= 0:
        return False, "无库存"

    dining = req.dining_type or ""
    if dining == "堂食" and not p.dine_in:
        return False, "不支持堂食"
    if dining == "外带" and not p.takeaway:
        return False, "不支持外带"
    if dining == "外卖" and not p.delivery:
        return False, "不支持外卖"

    avoid_text = f"{req.avoid} {req.note}".strip()
    combined_text = f"{p.name} {p.description} {p.ingredients} {p.allergy_note}"
    if avoid_text:
        avoid_keywords = ["不要肥", "不吃肥", "不要辣", "不吃辣", "不要筋", "不吃筋", "不要鸡蛋", "不吃鸡蛋", "不要洋葱", "不吃洋葱"]
        if "不要辣" in avoid_text or "不吃辣" in avoid_text:
            if p.spicy not in {"不辣", "都可以", ""}:
                return False, "用户忌辣"
        if ("不要肥" in avoid_text or "不吃肥" in avoid_text) and _contains_any(combined_text, ["肥牛", "牛小排", "肥"]):
            return False, "用户不想吃肥肉"
        if ("不要筋" in avoid_text or "不吃筋" in avoid_text) and _contains_any(combined_text, ["筋", "牛筋"]):
            return False, "用户不想吃筋"
        if ("不要鸡蛋" in avoid_text or "不吃鸡蛋" in avoid_text) and _contains_any(combined_text, ["鸡蛋", "煎蛋", "蛋"]):
            return False, "用户不想吃鸡蛋"
        if ("不要洋葱" in avoid_text or "不吃洋葱" in avoid_text) and "洋葱" in combined_text:
            return False, "用户不想吃洋葱"

    if "孩子" in req.meal_goal or "孩子" in avoid_text or "孩子" in req.note:
        if p.spicy not in {"不辣", "都可以", ""}:
            return False, "孩子场景不推荐辣味"
        if _contains_any(" ".join(p.unsuitable_people), ["孩子"]):
            return False, "商品标记不适合孩子"

    if req.spicy_level == "不辣" and p.spicy not in {"不辣", "都可以", ""}:
        return False, "用户选择不辣"

    return True, ""


def score_product(p: Product, req: RecommendRequest, budget: float, people: int) -> float:
    score = p.priority * 8
    score += max(0, p.gross_margin) * 10

    goal_text = req.meal_goal or ""
    taste_text = req.taste or ""

    if goal_text in p.scene_tags:
        score += 35
    if goal_text in p.suitable_people:
        score += 25

    # 关键词泛化匹配
    tag_text = " ".join(p.scene_tags + p.suitable_people + p.diet_tags + [p.category, p.sub_category, p.description])
    if "减脂" in goal_text and _contains_any(tag_text, ["减脂", "高蛋白", "低脂", "低油"]):
        score += 35
    if "孩子" in goal_text and _contains_any(tag_text, ["孩子", "不辣", "软烂", "温和"]):
        score += 35
    if "辣" in goal_text and _contains_any(tag_text + p.spicy, ["辣", "麻辣", "香辣", "下饭"]):
        score += 35
    if "烤肉" in goal_text and _contains_any(tag_text, ["烤肉", "聚餐", "烧烤"]):
        score += 35
    if "备餐" in goal_text and _contains_any(tag_text, ["备餐", "家庭", "冷藏", "外带"]):
        score += 25

    if p.taste == taste_text:
        score += 12
    if req.spicy_level and req.spicy_level != "都可以":
        if p.spicy == req.spicy_level:
            score += 15
        elif SPICY_ORDER.get(p.spicy, 99) <= SPICY_ORDER.get(req.spicy_level, 99):
            score += 6

    if p.people_min <= people <= p.people_max:
        score += 12
    elif p.people_max < people and p.is_combo:
        score += 4

    if p.budget_min <= budget <= p.budget_max:
        score += 12
    if p.price <= budget:
        score += 8
    else:
        score -= 20

    if req.need_staple == "需要主食" and _contains_any(p.category + p.sub_category + p.description, ["饭", "面", "主食", "水饺", "馄饨"]):
        score += 14
    if req.need_staple == "不需要主食" and _contains_any(p.category + p.sub_category + p.description, ["饭", "面", "主食"]):
        score -= 12

    return round(score, 2)


def filter_and_rank_products(products: List[Product], req: RecommendRequest, limit: int = 20) -> List[Product]:
    budget = parse_budget(req)
    people = parse_people(req)
    candidates = []
    for p in products:
        ok, _ = is_allowed_by_hard_rules(p, req)
        if not ok:
            continue
        # 单品可以超过预算一点，用组合时会控制总价；超太多则过滤
        if p.price > budget * 1.4 and budget < 9999:
            continue
        candidates.append((score_product(p, req, budget, people), p))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in candidates[:limit]]


def _make_item(p: Product, reason: str = "") -> RecommendItem:
    return RecommendItem(
        product_id=p.product_id,
        name=p.name,
        price=round(float(p.price), 2),
        spec=p.spec,
        reason=reason or p.description,
    )


def _combo_score(combo: Tuple[Product, ...], req: RecommendRequest, budget: float, people: int) -> float:
    total = sum(p.price for p in combo)
    if total > budget and budget < 9999:
        return -9999 - total
    s = sum(score_product(p, req, budget, people) for p in combo)
    # 价格越接近预算但不超预算越好，避免推荐过少
    if budget < 9999:
        s += max(0, 25 - abs(budget - total) / max(budget, 1) * 25)
    # 品类多样性
    s += len(set(p.category for p in combo)) * 5
    return s


def local_rule_recommend(products: List[Product], req: RecommendRequest) -> List[Recommendation]:
    budget = parse_budget(req)
    people = parse_people(req)
    ranked = filter_and_rank_products(products, req, limit=18)
    if not ranked:
        return []

    combos: List[Tuple[float, Tuple[Product, ...]]] = []
    # 优先已有套餐
    for p in ranked:
        if p.is_combo and p.price <= budget:
            combos.append((_combo_score((p,), req, budget, people) + 20, (p,)))
    # 单人/小预算：1-2件；家庭/高预算：2-4件
    max_len = 4 if people >= 3 or budget >= 100 else 3
    for r in range(1, max_len + 1):
        for combo in itertools.combinations(ranked[:14], r):
            total = sum(p.price for p in combo)
            if budget < 9999 and total > budget:
                continue
            if total <= 0:
                continue
            combos.append((_combo_score(combo, req, budget, people), combo))

    combos.sort(key=lambda x: x[0], reverse=True)

    seen_keys = set()
    recommendations: List[Recommendation] = []
    titles = [
        "更贴合你需求的搭配",
        "预算内更有满足感的搭配",
        "可作为备选的稳妥搭配",
    ]

    for _, combo in combos:
        key = tuple(sorted(p.product_id for p in combo))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        total = round(sum(p.price for p in combo), 2)
        if budget < 9999 and total > budget:
            continue
        items = [_make_item(p) for p in combo]
        title = titles[min(len(recommendations), len(titles) - 1)]
        if "减脂" in req.meal_goal:
            title = ["轻负担高蛋白搭配", "预算内减脂备选", "清爽饱腹搭配"][min(len(recommendations), 2)]
        elif "孩子" in req.meal_goal:
            title = ["孩子友好不辣搭配", "家庭温和口味搭配", "孩子备餐搭配"][min(len(recommendations), 2)]
        elif "辣" in req.meal_goal:
            title = ["香辣解馋搭配", "下饭重口味搭配", "辣味加购搭配"][min(len(recommendations), 2)]
        elif "烤肉" in req.meal_goal:
            title = ["烤肉聚餐搭配", "高客单烤肉组合", "轻聚餐备选"][min(len(recommendations), 2)]

        reason = f"这套共{len(items)}个商品，合计约{total}元，符合{req.people_count}、{req.budget_range}和{req.meal_goal}的需求。"
        if req.taste:
            reason += f"整体口味偏{req.taste}，更贴近你的选择。"
        upsell = combo[0].add_on if combo and combo[0].add_on else "可以让店员根据当日库存再补一个小份加购。"
        recommendations.append(
            Recommendation(
                title=title,
                total_price=total,
                fit_score=min(98, max(70, int(_combo_score(combo, req, budget, people) / max(1, len(combo))))),
                items=items,
                reason=reason,
                upsell=upsell,
                warning="",
            )
        )
        if len(recommendations) >= 3:
            break
    return recommendations

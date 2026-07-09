from __future__ import annotations

from typing import Any, Dict, List, Optional
import json
import os
import re

from .product_loader import Product
from .schemas import RecommendRequest, Recommendation, RecommendItem
from .recommender import parse_budget, parse_people


SYSTEM_DESCRIBE = """
你是肉包公门店的专业点餐推荐助手，擅长根据客户预算、人数、口味和用餐场景搭配满意的套餐。

必须遵守：
1. 只能推荐候选商品列表中真实存在的商品，不能编造商品、价格、优惠或库存。
2. 每套方案总价必须等于商品售价相加；如果不确定，由后台重新计算。
3. 孩子吃、不吃辣、清淡需求，不要推荐麻辣、重口味商品。
4. 减脂高蛋白需求，优先推荐高蛋白、低油、低脂、清淡类商品。
5. 输出自然、专业、克制，不要夸大，不要承诺医疗功效。
6. 必须只输出 JSON，不要输出 Markdown，不要解释 JSON 外的内容。
""".strip()


def build_question(req: RecommendRequest, candidates: List[Product]) -> str:
    budget = parse_budget(req)
    people = parse_people(req)
    candidate_rows = []
    for p in candidates:
        candidate_rows.append({
            "product_id": p.product_id,
            "name": p.name,
            "price": round(p.price, 2),
            "spec": p.spec,
            "category": p.category,
            "stock": p.stock,
            "taste": p.taste,
            "spicy": p.spicy,
            "scene_tags": p.scene_tags,
            "suitable_people": p.suitable_people,
            "unsuitable_people": p.unsuitable_people,
            "diet_tags": p.diet_tags,
            "description": p.description,
            "add_on": p.add_on,
        })

    payload = {
        "用户需求": {
            "门店": req.store_id,
            "桌号": req.table_id,
            "渠道": req.channel,
            "用餐人数原始输入": req.people_count,
            "用餐人数估计": people,
            "预算原始输入": req.budget_range,
            "预算上限估计": budget,
            "用餐目标": req.meal_goal,
            "用餐方式": req.dining_type,
            "是否需要主食": req.need_staple,
            "口味轻重": req.taste,
            "辣度": req.spicy_level,
            "忌口": req.avoid,
            "补充说明": req.note,
        },
        "候选商品": candidate_rows,
        "输出要求": {
            "recommendations": "给出2到3套推荐",
            "字段": ["title", "items", "reason", "upsell", "warning"],
            "items字段": ["product_id", "name", "reason"],
            "注意": "items中只需要product_id/name/reason，价格和总价由后台根据商品库重新计算",
        }
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


def call_llm(req: RecommendRequest, candidates: List[Product]) -> Optional[Dict[str, Any]]:
    use_llm = os.getenv("USE_LLM", "false").lower() in {"1", "true", "yes", "y"}
    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if not use_llm or not api_key or api_key.startswith("请填写"):
        return None

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("未安装 openai 依赖，请先执行：pip install -r requirements.txt") from exc

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
    )
    question = build_question(req, candidates)
    response = client.chat.completions.create(
        model=os.getenv("SILICONFLOW_MODEL", "Qwen/Qwen3.5-397B-A17B"),
        messages=[
            {"role": "system", "content": SYSTEM_DESCRIBE},
            {"role": "user", "content": question},
        ],
        temperature=0.4,
        max_tokens=600
    )
    content = response.choices[0].message.content
    return _extract_json(content)


def normalize_llm_result(raw: Dict[str, Any], candidates: List[Product]) -> List[Recommendation]:
    """把模型返回的 JSON 转成后端可信结果。价格、总价都用商品库重算。"""
    by_id = {p.product_id: p for p in candidates}
    by_name = {p.name: p for p in candidates}
    recs = raw.get("recommendations") or raw.get("推荐方案") or []
    normalized: List[Recommendation] = []
    for i, rec in enumerate(recs[:3]):
        raw_items = rec.get("items") or rec.get("商品") or []
        items: List[RecommendItem] = []
        used = set()
        for item in raw_items:
            pid = str(item.get("product_id") or item.get("产品ID") or "").strip()
            name = str(item.get("name") or item.get("商品名称") or "").strip()
            p = by_id.get(pid) or by_name.get(name)
            if not p or p.product_id in used:
                continue
            used.add(p.product_id)
            items.append(RecommendItem(
                product_id=p.product_id,
                name=p.name,
                price=round(p.price, 2),
                spec=p.spec,
                reason=str(item.get("reason") or p.description),
            ))
        if not items:
            continue
        total = round(sum(x.price for x in items), 2)
        normalized.append(Recommendation(
            title=str(rec.get("title") or rec.get("标题") or f"推荐方案{i+1}"),
            total_price=total,
            fit_score=int(rec.get("fit_score") or rec.get("匹配度") or 88),
            items=items,
            reason=str(rec.get("reason") or rec.get("推荐理由") or "这套搭配较符合你的预算、人数和口味需求。"),
            upsell=str(rec.get("upsell") or rec.get("加购建议") or ""),
            warning=str(rec.get("warning") or rec.get("提醒") or ""),
        ))
    return normalized

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .product_loader import Product
from .schemas import RecommendRequest, RecommendResponse


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_PATH = BASE_DIR / "data" / "recommendation_cache.json"
_CACHE_LOCK = threading.Lock()


# 这些字段代表“用户选择”。table_id/channel 不纳入缓存键，避免同一需求在不同桌号/渠道重复调用大模型。
# store_id 纳入缓存键，便于后续多门店商品或规则不同。
CACHE_REQUEST_FIELDS = [
    "store_id",
    "people_count",
    "budget_range",
    "budget_amount",
    "meal_goal",
    "dining_type",
    "need_staple",
    "taste",
    "spicy_level",
    "avoid",
    "note",
]


def _cache_enabled() -> bool:
    return os.getenv("RECOMMEND_CACHE_ENABLED", "true").lower() in {"1", "true", "yes", "y"}


def get_cache_path() -> Path:
    custom = os.getenv("RECOMMEND_CACHE_PATH", "").strip()
    if custom:
        return Path(custom)
    return DEFAULT_CACHE_PATH


def _model_to_dict(model: Any) -> Dict[str, Any]:
    """兼容 Pydantic v1/v2。"""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    # 合并多余空白，避免“不要辣  不要肥”和“不要辣 不要肥”被当成不同需求。
    return " ".join(text.split())


def normalized_request_dict(req: RecommendRequest) -> Dict[str, Any]:
    raw = _model_to_dict(req)
    normalized: Dict[str, Any] = {}
    for field in CACHE_REQUEST_FIELDS:
        value = raw.get(field, "")
        if field == "budget_amount":
            if value is None or value == "":
                normalized[field] = None
            else:
                try:
                    normalized[field] = round(float(value), 2)
                except Exception:
                    normalized[field] = _normalize_text(value)
        else:
            normalized[field] = _normalize_text(value)
    return normalized


def build_catalog_signature(products: List[Product]) -> str:
    """商品库签名：商品价格、库存、上下架、关键标签变化后，旧缓存自动不命中。"""
    rows = []
    for p in products:
        rows.append({
            "product_id": p.product_id,
            "name": p.name,
            "price": round(float(p.price), 2),
            "stock": int(p.stock),
            "status": p.status,
            "priority": int(p.priority),
            "category": p.category,
            "taste": p.taste,
            "spicy": p.spicy,
            "dine_in": bool(p.dine_in),
            "takeaway": bool(p.takeaway),
            "delivery": bool(p.delivery),
        })
    rows.sort(key=lambda x: x["product_id"])
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_cache_key(req: RecommendRequest, catalog_signature: str) -> str:
    payload = {
        "version": 1,
        "request": normalized_request_dict(req),
        "catalog_signature": catalog_signature,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _empty_cache() -> Dict[str, Any]:
    return {
        "version": 1,
        "description": "肉包公AI推荐系统缓存。命中相同用户选择和相同商品库签名时，不再调用大模型。",
        "entries": {},
    }


def _load_cache_unlocked(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _empty_cache()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "entries" not in data or not isinstance(data["entries"], dict):
            return _empty_cache()
        return data
    except Exception:
        # 缓存损坏时不影响点餐推荐主流程，自动重建。
        return _empty_cache()


def _save_cache_unlocked(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def get_cached_response(req: RecommendRequest, catalog_signature: str) -> Optional[RecommendResponse]:
    if not _cache_enabled():
        return None
    path = get_cache_path()
    key = make_cache_key(req, catalog_signature)
    now = datetime.now().isoformat(timespec="seconds")
    with _CACHE_LOCK:
        data = _load_cache_unlocked(path)
        entry = data.get("entries", {}).get(key)
        if not entry:
            return None
        response_data = entry.get("response")
        if not response_data:
            return None
        entry["hit_count"] = int(entry.get("hit_count", 0)) + 1
        entry["last_hit_at"] = now
        _save_cache_unlocked(path, data)

    response = RecommendResponse(**response_data)
    original_mode = response.mode
    response.mode = "cache_hit"
    response.debug = dict(response.debug or {})
    response.debug.update({
        "cache_hit": True,
        "cache_key": key,
        "cache_original_mode": original_mode,
        "cache_created_at": entry.get("created_at", ""),
        "cache_hit_count": entry.get("hit_count", 0),
    })
    return response


def save_recommendation_cache(
    req: RecommendRequest,
    catalog_signature: str,
    response: RecommendResponse,
) -> str:
    """保存最终推荐。返回 cache_key。"""
    if not _cache_enabled() or not response.recommendations:
        return ""
    path = get_cache_path()
    key = make_cache_key(req, catalog_signature)
    now = datetime.now().isoformat(timespec="seconds")
    response_data = _model_to_dict(response)
    response_data.setdefault("debug", {})
    # 避免把上一次缓存命中状态再次写入。
    response_data["debug"] = dict(response_data.get("debug") or {})
    response_data["debug"].update({
        "cache_hit": False,
        "cache_key": key,
    })

    entry = {
        "cache_key": key,
        "created_at": now,
        "updated_at": now,
        "hit_count": 0,
        "last_hit_at": "",
        "catalog_signature": catalog_signature,
        "request": normalized_request_dict(req),
        "response": response_data,
    }
    with _CACHE_LOCK:
        data = _load_cache_unlocked(path)
        old = data.setdefault("entries", {}).get(key)
        if old:
            entry["created_at"] = old.get("created_at", now)
            entry["hit_count"] = int(old.get("hit_count", 0))
            entry["last_hit_at"] = old.get("last_hit_at", "")
        data["entries"][key] = entry
        _save_cache_unlocked(path, data)
    return key


def cache_stats() -> Dict[str, Any]:
    path = get_cache_path()
    with _CACHE_LOCK:
        data = _load_cache_unlocked(path)
    entries = data.get("entries", {})
    total_hits = sum(int(e.get("hit_count", 0)) for e in entries.values())
    return {
        "enabled": _cache_enabled(),
        "path": str(path),
        "entry_count": len(entries),
        "total_hits": total_hits,
    }


def clear_cache() -> Dict[str, Any]:
    path = get_cache_path()
    with _CACHE_LOCK:
        _save_cache_unlocked(path, _empty_cache())
    return cache_stats()

from __future__ import annotations

from typing import Any

from flask import jsonify, render_template, request
from pydantic import ValidationError

from wxcloudrun import app
from wxcloudrun.llm_client import call_llm, normalize_llm_result
from wxcloudrun.product_loader import load_products
from wxcloudrun.recommendation_cache import (
    build_catalog_signature,
    cache_stats,
    clear_cache,
    get_cached_response,
    save_recommendation_cache,
)
from wxcloudrun.recommender import filter_and_rank_products, local_rule_recommend, parse_budget, parse_people
from wxcloudrun.schemas import RecommendRequest, RecommendResponse


PRODUCTS = load_products()
CATALOG_SIGNATURE = build_catalog_signature(PRODUCTS)


def _model_to_dict(model: Any) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _json_response(data: Any, status: int = 200):
    return jsonify(data), status


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return _json_response({
        "ok": True,
        "service": "roubaogong-ai-recommend",
        "products": len(PRODUCTS),
        "cache": cache_stats(),
    })


@app.route("/api/products")
def products():
    return _json_response({
        "count": len(PRODUCTS),
        "items": [p.to_dict() for p in PRODUCTS],
    })


@app.route("/api/cache/stats")
def get_cache_stats():
    return _json_response(cache_stats())


@app.route("/api/cache/clear", methods=["POST"])
def post_cache_clear():
    return _json_response(clear_cache())


@app.route("/api/recommend", methods=["POST"])
def recommend():
    payload = request.get_json(silent=True) or {}
    try:
        req = RecommendRequest(**payload)
    except ValidationError as exc:
        return _json_response({
            "error": "参数校验失败",
            "detail": exc.errors(),
        }, 400)

    budget = parse_budget(req)
    people = parse_people(req)

    cached = get_cached_response(req, CATALOG_SIGNATURE)
    if cached:
        return _json_response(_model_to_dict(cached))

    candidates = filter_and_rank_products(PRODUCTS, req, limit=24)

    mode = "local_rule"
    recommendations = []
    error = ""

    try:
        llm_raw = call_llm(req, candidates)
        if llm_raw:
            normalized = normalize_llm_result(llm_raw, candidates)
            if normalized:
                recommendations = normalized
                mode = "llm_with_backend_validation"
    except Exception as exc:
        error = str(exc)

    if not recommendations:
        recommendations = local_rule_recommend(PRODUCTS, req)
        mode = "local_rule_fallback" if error else "local_rule"

    summary = f"{req.people_count}，预算{req.budget_range}，{req.meal_goal}，口味{req.taste}，辣度{req.spicy_level}。"
    response = RecommendResponse(
        mode=mode,
        request_summary=summary,
        recommendations=recommendations,
        debug={
            "budget_estimate": budget,
            "people_estimate": people,
            "candidate_count": len(candidates),
            "candidate_ids": [p.product_id for p in candidates[:12]],
            "catalog_signature": CATALOG_SIGNATURE[:12],
            "cache_hit": False,
            "llm_error": error,
        },
    )

    cache_key = save_recommendation_cache(req, CATALOG_SIGNATURE, response)
    if cache_key:
        response.debug["cache_key"] = cache_key
    return _json_response(_model_to_dict(response))

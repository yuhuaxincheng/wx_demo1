from __future__ import annotations

from typing import Any
import os
import threading
import uuid

from flask import jsonify, render_template, request
from pydantic import ValidationError

from wxcloudrun import app
from wxcloudrun.db import (
    admin_summary,
    create_recommendation_task,
    db_enabled,
    get_member,
    get_recommendation_task,
    list_members,
    list_recommendation_logs,
    record_member_order,
    save_recommendation_log,
    update_recommendation_task,
    upsert_member,
)
from wxcloudrun.llm_client import call_llm, get_llm_timeout_seconds, normalize_llm_result
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
MEMORY_TASKS: dict[str, dict] = {}
MEMORY_TASKS_LOCK = threading.Lock()


def _model_to_dict(model: Any) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _json_response(data: Any, status: int = 200):
    return jsonify(data), status


def _memory_task_set(task_id: str, data: dict) -> None:
    with MEMORY_TASKS_LOCK:
        current = MEMORY_TASKS.get(task_id, {})
        current.update(data)
        MEMORY_TASKS[task_id] = current


def _memory_task_get(task_id: str) -> dict | None:
    with MEMORY_TASKS_LOCK:
        task = MEMORY_TASKS.get(task_id)
        return dict(task) if task else None


def _wechat_identity() -> dict:
    return {
        "openid": (
            request.headers.get("X-WX-OPENID")
            or request.headers.get("x-wx-openid")
            or ""
        ).strip(),
        "unionid": (
            request.headers.get("X-WX-UNIONID")
            or request.headers.get("x-wx-unionid")
            or ""
        ).strip(),
        "appid": (
            request.headers.get("X-WX-APPID")
            or request.headers.get("x-wx-appid")
            or ""
        ).strip(),
    }


def _admin_error():
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    token = (
        request.headers.get("X-ADMIN-TOKEN")
        or request.args.get("token")
        or ""
    ).strip()
    if not expected:
        return _json_response({
            "error": "ADMIN_TOKEN 未配置，后台查看功能未开启",
        }, 403)
    if token != expected:
        return _json_response({
            "error": "管理口令错误",
        }, 401)
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/admin")
def admin_page():
    return render_template("admin.html")


@app.route("/health")
def health():
    llm_api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    return _json_response({
        "ok": True,
        "service": "roubaogong-ai-recommend",
        "products": len(PRODUCTS),
        "cache": cache_stats(),
        "db_enabled": db_enabled(),
        "llm": {
            "use_llm": os.getenv("USE_LLM", "false").lower() in {"1", "true", "yes", "y"},
            "api_key_configured": bool(llm_api_key),
            "api_key_length": len(llm_api_key),
            "base_url": os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
            "model": os.getenv("SILICONFLOW_MODEL", "Qwen/Qwen3.5-397B-A17B"),
            "timeout_seconds": get_llm_timeout_seconds(),
        },
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


@app.route("/api/admin/summary")
def admin_get_summary():
    error = _admin_error()
    if error:
        return error
    try:
        return _json_response(admin_summary())
    except Exception as exc:
        return _json_response({
            "error": "读取汇总数据失败",
            "detail": str(exc),
        }, 500)


@app.route("/api/admin/members")
def admin_get_members():
    error = _admin_error()
    if error:
        return error
    try:
        rows = list_members(int(request.args.get("limit", 100)))
        return _json_response({
            "count": len(rows),
            "items": rows,
        })
    except Exception as exc:
        return _json_response({
            "error": "读取会员数据失败",
            "detail": str(exc),
        }, 500)


@app.route("/api/admin/recommendations")
def admin_get_recommendations():
    error = _admin_error()
    if error:
        return error
    try:
        rows = list_recommendation_logs(int(request.args.get("limit", 50)))
        return _json_response({
            "count": len(rows),
            "items": rows,
        })
    except Exception as exc:
        return _json_response({
            "error": "读取推荐记录失败",
            "detail": str(exc),
        }, 500)


@app.route("/api/member/login", methods=["POST"])
def member_login():
    payload = request.get_json(silent=True) or {}
    identity = _wechat_identity()
    openid = identity["openid"] or str(payload.get("openid") or "").strip()
    if not openid:
        return _json_response({
            "error": "未获取到微信用户 ID，请确认小程序通过 wx.cloud.callContainer 调用云托管服务",
        }, 400)

    member = upsert_member(
        openid=openid,
        unionid=identity["unionid"] or str(payload.get("unionid") or "").strip(),
        nick_name=str(payload.get("nickName") or payload.get("nick_name") or "").strip(),
        avatar_url=str(payload.get("avatarUrl") or payload.get("avatar_url") or "").strip(),
    )
    if not member:
        return _json_response({
            "error": "会员登录失败，请检查 MySQL 环境变量是否配置完整",
        }, 500)
    return _json_response({
        "member": member,
    })


@app.route("/api/member/me")
def member_me():
    identity = _wechat_identity()
    openid = identity["openid"] or str(request.args.get("openid") or "").strip()
    if not openid:
        return _json_response({
            "error": "未获取到微信用户 ID",
        }, 400)

    member = get_member(openid)
    if not member:
        return _json_response({
            "error": "会员不存在",
        }, 404)
    return _json_response({
        "member": member,
    })


@app.route("/api/member/order", methods=["POST"])
def member_order():
    payload = request.get_json(silent=True) or {}
    identity = _wechat_identity()
    openid = identity["openid"] or str(payload.get("openid") or "").strip()
    if not openid:
        return _json_response({
            "error": "未获取到微信用户 ID",
        }, 400)

    try:
        amount = float(payload.get("amount") or 0)
        member = record_member_order(openid, amount)
    except Exception as exc:
        return _json_response({
            "error": "会员消费统计写入失败",
            "detail": str(exc),
        }, 500)

    return _json_response({
        "member": member,
    })


@app.route("/api/recommend/history")
def recommend_history():
    limit = request.args.get("limit", 20)
    try:
        rows = list_recommendation_logs(int(limit))
    except Exception as exc:
        return _json_response({
            "error": "读取推荐历史失败",
            "detail": str(exc),
        }, 500)
    return _json_response({
        "count": len(rows),
        "items": rows,
    })


def _build_recommend_response(req: RecommendRequest) -> RecommendResponse:
    budget = parse_budget(req)
    people = parse_people(req)

    cached = get_cached_response(req, CATALOG_SIGNATURE)
    if cached:
        save_recommendation_log(req, cached)
        return cached

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
            else:
                error = "llm_result_invalid"
    except Exception as exc:
        error = str(exc)

    if not recommendations:
        recommendations = local_rule_recommend(PRODUCTS, req)
        mode = "local_rule_fallback" if error else "local_rule"

    summary = (
        f"{req.people_count}, budget {req.budget_range}, goal {req.meal_goal}, "
        f"taste {req.taste}, spicy {req.spicy_level}."
    )
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
    log_id = save_recommendation_log(req, response)
    if log_id:
        response.debug["db_log_id"] = log_id
    return response


def _run_recommendation_task(task_id: str, payload: dict) -> None:
    _memory_task_set(task_id, {"status": "running"})
    update_recommendation_task(task_id, "running")
    try:
        req = RecommendRequest(**payload)
        response = _build_recommend_response(req)
        response_data = _model_to_dict(response)
        _memory_task_set(task_id, {
            "status": "succeeded",
            "response": response_data,
            "error": "",
        })
        update_recommendation_task(task_id, "succeeded", response=response)
    except Exception as exc:
        message = str(exc)
        _memory_task_set(task_id, {
            "status": "failed",
            "error": message,
        })
        update_recommendation_task(task_id, "failed", error_text=message)


@app.route("/api/recommend/async", methods=["POST"])
def recommend_async():
    payload = request.get_json(silent=True) or {}
    try:
        req = RecommendRequest(**payload)
    except ValidationError as exc:
        return _json_response({
            "error": "参数校验失败",
            "detail": exc.errors(),
        }, 400)

    cached = get_cached_response(req, CATALOG_SIGNATURE)
    if cached:
        save_recommendation_log(req, cached)
        return _json_response({
            "status": "succeeded",
            "response": _model_to_dict(cached),
        })

    task_id = create_recommendation_task(req) or uuid.uuid4().hex
    _memory_task_set(task_id, {
        "task_id": task_id,
        "status": "pending",
        "request": _model_to_dict(req),
    })
    worker = threading.Thread(
        target=_run_recommendation_task,
        args=(task_id, _model_to_dict(req)),
        daemon=True,
    )
    worker.start()
    return _json_response({
        "task_id": task_id,
        "status": "running",
        "poll_interval_ms": 3000,
        "timeout_seconds": get_llm_timeout_seconds(),
    })


@app.route("/api/recommend/task", methods=["GET", "POST"])
def recommend_task():
    payload = request.get_json(silent=True) or {}
    task_id = str(payload.get("task_id") or request.args.get("task_id") or "").strip()
    if not task_id:
        return _json_response({
            "error": "task_id required",
        }, 400)

    task = get_recommendation_task(task_id) or _memory_task_get(task_id)
    if not task:
        return _json_response({
            "error": "task not found",
            "task_id": task_id,
        }, 404)

    result = {
        "task_id": task_id,
        "status": task.get("status") or "pending",
        "error": task.get("error_text") or task.get("error") or "",
    }
    response = task.get("response")
    if response:
        result["response"] = response
    if task.get("created_at"):
        result["created_at"] = task.get("created_at")
    if task.get("updated_at"):
        result["updated_at"] = task.get("updated_at")
    return _json_response(result)


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

    response = _build_recommend_response(req)
    return _json_response(_model_to_dict(response))

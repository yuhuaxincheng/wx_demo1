from __future__ import annotations

from typing import Any, Dict, List, Optional
import json
import os

import pymysql
from pymysql.cursors import DictCursor

from .schemas import RecommendRequest, RecommendResponse


DEFAULT_DB_NAME = "rbg_ai"


def db_enabled() -> bool:
    return bool(os.getenv("MYSQL_ADDRESS") and os.getenv("MYSQL_USERNAME"))


def _mysql_address() -> tuple[str, int]:
    address = os.getenv("MYSQL_ADDRESS", "").strip()
    host, _, port_text = address.partition(":")
    return host, int(port_text or "3306")


def _connect(use_database: bool = True, autocommit: bool = True):
    host, port = _mysql_address()
    database = os.getenv("MYSQL_DATABASE", DEFAULT_DB_NAME).strip() or DEFAULT_DB_NAME
    kwargs = {
        "host": host,
        "port": port,
        "user": os.getenv("MYSQL_USERNAME", "").strip(),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "charset": "utf8mb4",
        "autocommit": autocommit,
        "cursorclass": DictCursor,
        "connect_timeout": 5,
        "read_timeout": 8,
        "write_timeout": 8,
    }
    if use_database:
        kwargs["database"] = database
    return pymysql.connect(**kwargs)


def init_db() -> None:
    if not db_enabled():
        return

    database = os.getenv("MYSQL_DATABASE", DEFAULT_DB_NAME).strip() or DEFAULT_DB_NAME
    with _connect(use_database=False) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )

    with _connect(use_database=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS recommendation_logs (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  store_id VARCHAR(64) NOT NULL DEFAULT '',
                  table_id VARCHAR(64) NOT NULL DEFAULT '',
                  channel VARCHAR(64) NOT NULL DEFAULT '',
                  people_count VARCHAR(64) NOT NULL DEFAULT '',
                  budget_range VARCHAR(64) NOT NULL DEFAULT '',
                  meal_goal VARCHAR(128) NOT NULL DEFAULT '',
                  taste VARCHAR(64) NOT NULL DEFAULT '',
                  mode VARCHAR(64) NOT NULL DEFAULT '',
                  request_json LONGTEXT NOT NULL,
                  response_json LONGTEXT NOT NULL,
                  PRIMARY KEY (id),
                  KEY idx_created_at (created_at),
                  KEY idx_store_created (store_id, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS members (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  openid VARCHAR(128) NOT NULL,
                  unionid VARCHAR(128) NOT NULL DEFAULT '',
                  nick_name VARCHAR(128) NOT NULL DEFAULT '',
                  avatar_url VARCHAR(512) NOT NULL DEFAULT '',
                  order_count INT UNSIGNED NOT NULL DEFAULT 0,
                  total_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
                  PRIMARY KEY (id),
                  UNIQUE KEY uk_openid (openid),
                  KEY idx_updated_at (updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )


def _model_to_dict(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def save_recommendation_log(req: RecommendRequest, response: RecommendResponse) -> Optional[int]:
    if not db_enabled():
        return None

    try:
        init_db()
        request_data = _model_to_dict(req)
        response_data = _model_to_dict(response)
        with _connect(use_database=True) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO recommendation_logs (
                      store_id, table_id, channel, people_count, budget_range,
                      meal_goal, taste, mode, request_json, response_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        req.store_id,
                        req.table_id,
                        req.channel,
                        req.people_count,
                        req.budget_range,
                        req.meal_goal,
                        req.taste,
                        response.mode,
                        json.dumps(request_data, ensure_ascii=False),
                        json.dumps(response_data, ensure_ascii=False),
                    ),
                )
                return int(cursor.lastrowid)
    except Exception:
        return None


def upsert_member(openid: str, nick_name: str = "", avatar_url: str = "", unionid: str = "") -> Optional[Dict[str, Any]]:
    if not db_enabled() or not openid:
        return None

    init_db()
    with _connect(use_database=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO members (openid, unionid, nick_name, avatar_url)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  unionid = IF(VALUES(unionid) = '', unionid, VALUES(unionid)),
                  nick_name = IF(VALUES(nick_name) = '', nick_name, VALUES(nick_name)),
                  avatar_url = IF(VALUES(avatar_url) = '', avatar_url, VALUES(avatar_url))
                """,
                (openid, unionid or "", nick_name or "", avatar_url or ""),
            )
    return get_member(openid)


def get_member(openid: str) -> Optional[Dict[str, Any]]:
    if not db_enabled() or not openid:
        return None

    init_db()
    with _connect(use_database=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, created_at, updated_at, openid, unionid, nick_name,
                       avatar_url, order_count, total_amount
                FROM members
                WHERE openid = %s
                LIMIT 1
                """,
                (openid,),
            )
            row = cursor.fetchone()
    return _serialize_member(row)


def record_member_order(openid: str, amount: float) -> Optional[Dict[str, Any]]:
    if not db_enabled() or not openid:
        return None

    init_db()
    amount = round(max(float(amount or 0), 0), 2)
    with _connect(use_database=True, autocommit=False) as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT order_count, total_amount
                    FROM members
                    WHERE openid = %s
                    FOR UPDATE
                    """,
                    (openid,),
                )
                row = cursor.fetchone()
                if not row:
                    cursor.execute(
                        "INSERT INTO members (openid) VALUES (%s)",
                        (openid,),
                    )
                    old_count = 0
                    old_total = 0.0
                else:
                    old_count = int(row.get("order_count") or 0)
                    old_total = float(row.get("total_amount") or 0)

                new_count = old_count + 1
                new_total = round(old_total + amount, 2)
                cursor.execute(
                    """
                    UPDATE members
                    SET order_count = %s, total_amount = %s
                    WHERE openid = %s
                    """,
                    (new_count, new_total, openid),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return get_member(openid)


def _serialize_member(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    for key in ("created_at", "updated_at"):
        if row.get(key):
            row[key] = row[key].isoformat(sep=" ", timespec="seconds")
    row["order_count"] = int(row.get("order_count") or 0)
    row["total_amount"] = float(row.get("total_amount") or 0)
    return row


def list_members(limit: int = 100) -> List[Dict[str, Any]]:
    if not db_enabled():
        return []

    limit = max(1, min(int(limit or 100), 500))
    init_db()
    with _connect(use_database=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, created_at, updated_at, openid, unionid, nick_name,
                       avatar_url, order_count, total_amount
                FROM members
                ORDER BY updated_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()

    return [_serialize_member(row) for row in rows if row]


def admin_summary() -> Dict[str, Any]:
    if not db_enabled():
        return {
            "db_enabled": False,
            "member_count": 0,
            "order_count": 0,
            "total_amount": 0.0,
            "recommendation_count": 0,
        }

    init_db()
    with _connect(use_database=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS member_count,
                       COALESCE(SUM(order_count), 0) AS order_count,
                       COALESCE(SUM(total_amount), 0) AS total_amount
                FROM members
                """
            )
            member_row = cursor.fetchone() or {}
            cursor.execute("SELECT COUNT(*) AS recommendation_count FROM recommendation_logs")
            log_row = cursor.fetchone() or {}

    return {
        "db_enabled": True,
        "member_count": int(member_row.get("member_count") or 0),
        "order_count": int(member_row.get("order_count") or 0),
        "total_amount": float(member_row.get("total_amount") or 0),
        "recommendation_count": int(log_row.get("recommendation_count") or 0),
    }


def list_recommendation_logs(limit: int = 20) -> List[Dict[str, Any]]:
    if not db_enabled():
        return []

    limit = max(1, min(int(limit or 20), 100))
    init_db()
    with _connect(use_database=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, created_at, store_id, table_id, channel, people_count,
                       budget_range, meal_goal, taste, mode, request_json, response_json
                FROM recommendation_logs
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()

    for row in rows:
        row["created_at"] = row["created_at"].isoformat(sep=" ", timespec="seconds")
        for key in ("request_json", "response_json"):
            try:
                row[key.replace("_json", "")] = json.loads(row.pop(key))
            except Exception:
                row[key.replace("_json", "")] = {}
    return rows

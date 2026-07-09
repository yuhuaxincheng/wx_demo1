import os


DEBUG = os.environ.get("DEBUG", "false").lower() in {"1", "true", "yes", "y"}

USE_LLM = os.environ.get("USE_LLM", "true")
SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", " ")
SILICONFLOW_BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
SILICONFLOW_MODEL = os.environ.get("SILICONFLOW_MODEL", "Qwen/Qwen3.5-397B-A17B")

RECOMMEND_CACHE_ENABLED = os.environ.get("RECOMMEND_CACHE_ENABLED", "false")
RECOMMEND_CACHE_PATH = os.environ.get(
    "RECOMMEND_CACHE_PATH",
    "/app/wxcloudrun/data/recommendation_cache.json",
)

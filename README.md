#  AI 推荐云托管服务



## 接口

```text
GET  /health
GET  /api/products
POST /api/recommend
GET  /api/cache/stats
POST /api/cache/clear
```

## 云托管配置

```text
服务目录：wx_demo1
容器端口：80
建议服务名：wx_demo1
```

环境变量：

```text
USE_LLM=true
SILICONFLOW_API_KEY=你的大模型 Key
RECOMMEND_CACHE_ENABLED=true
RECOMMEND_CACHE_PATH=/app/wxcloudrun/data/recommendation_cache.json
```

## 小程序调用

根目录 `app.js`：

```js
globalData: {
  requestMode: 'cloud',
  apiBaseUrl: '',
  cloudEnvId: '你的云开发环境 ID',
  cloudServiceName: 'wx_demo1'
}
```

## 本地运行

```bash
pip install -r requirements.txt
python run.py 0.0.0.0 8000
```

访问：

```text
http://127.0.0.1:8000/health
```

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip -i https://mirrors.cloud.tencent.com/pypi/simple \
    && pip install -r requirements.txt -i https://mirrors.cloud.tencent.com/pypi/simple

COPY . .

EXPOSE 80

CMD ["python", "run.py", "0.0.0.0", "80"]

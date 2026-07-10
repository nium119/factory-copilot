FROM python:3.12-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖（国内镜像）
COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 -i https://pypi.tuna.tsinghua.edu.cn/simple/ --trusted-host pypi.tuna.tsinghua.edu.cn -r requirements.txt || \
    pip install --no-cache-dir --default-timeout=120 -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements.txt || \
    pip install --no-cache-dir --default-timeout=120 -r requirements.txt

# 应用代码
COPY . .

# 前端静态文件（nginx 或直接由 FastAPI 托管）
# 如果前端 dist 已构建，挂载到 /frontend/dist

EXPOSE 9001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9001"]

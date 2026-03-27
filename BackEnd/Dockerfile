FROM python:3.11-slim

WORKDIR /app

# 시스템 의존성 (opencv-headless, pymysql 등)
RUN apt-get update && apt-get install -y \
    gcc \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements_linux.txt .
RUN pip install --no-cache-dir --timeout=300 -r requirements_linux.txt

COPY . .

# static 디렉토리 생성 (맵 파일 마운트 대상)
RUN mkdir -p /app/static/maps

EXPOSE 8002

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]

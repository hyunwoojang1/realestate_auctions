FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# 차익 큐레이션 웹 (Flask + waitress WSGI, 프로덕션). http://localhost:8000
# dev server(flask run) 대신 waitress 로 서빙 → debug/reloader off 구조적 보장.
ENV AUCTION_HOST=0.0.0.0 AUCTION_PORT=8000
CMD ["python", "-m", "src.serve"]

FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# 차익 큐레이션 웹 (Flask). http://localhost:8000
CMD ["python", "-m", "flask", "--app", "src.web", "run", "--host=0.0.0.0", "--port=8000"]

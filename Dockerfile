FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

USER 1001

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8080"]
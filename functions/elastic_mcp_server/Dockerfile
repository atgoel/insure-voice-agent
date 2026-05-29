FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Cloud Run sets PORT automatically; default 8080 for local runs
ENV PORT=8080

CMD ["python", "main.py"]

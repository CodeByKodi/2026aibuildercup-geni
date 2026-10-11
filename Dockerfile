# Stage 1: build the React UI (frontend/) into static files.
FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: the Python service. FastAPI serves the JSON API and the built React app.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY workpilot/ ./workpilot/
COPY sample_data/ ./sample_data/
COPY --from=frontend /frontend/dist ./frontend/dist
ENV PORT=8080 PYTHONUNBUFFERED=1
CMD ["sh", "-c", "exec uvicorn workpilot.api:app --host 0.0.0.0 --port ${PORT} --timeout-keep-alive 75"]

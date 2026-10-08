FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY vendor/runtime_core /app/vendor/runtime_core
COPY config/models.toml /app/config/models.toml
COPY config/capabilities.toml /app/config/capabilities.toml
COPY cloud_trading /app/cloud_trading
COPY trading_agent /app/trading_agent
COPY skills /app/skills
COPY roles /app/roles
COPY knowledge /app/knowledge
ENV TRADING_DATA_DIR=/data/trading TRADING_RUNTIME_DIR=/data/runtime TRADING_HOST=0.0.0.0
EXPOSE 8097
CMD ["python", "-m", "cloud_trading"]

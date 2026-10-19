FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home marketplace && mkdir /data && chown marketplace /data
USER marketplace
ENV MARKETPLACE_DATA_DIR=/data MARKETPLACE_HOST=0.0.0.0 MARKETPLACE_PORT=8768
EXPOSE 8768
CMD ["better-marketplace", "serve"]

FROM python:3.12.10-slim

ARG NODE_VERSION=22.14.0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    && tar -xJf "node-v${NODE_VERSION}-linux-x64.tar.xz" -C /usr/local --strip-components=1 \
    && rm "node-v${NODE_VERSION}-linux-x64.tar.xz" \
    && npm install -g wrangler@4.45.0 \
    && apt-get purge -y curl xz-utils && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY hosted/requirements.txt ./hosted/requirements.txt
RUN pip install --no-cache-dir -r hosted/requirements.txt
COPY src ./src
COPY dashboard ./dashboard
COPY hosted ./hosted
COPY production_config.txt ./production_config.txt
ENV PYTHONUNBUFFERED=1 PORT=8080
CMD exec gunicorn --bind :${PORT} --workers 1 --threads 1 --timeout 600 hosted.app:app

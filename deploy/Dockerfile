FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /repo

WORKDIR /opt/epochgate
COPY packages/runtime /opt/epochgate/packages/runtime
RUN pip install --no-cache-dir /opt/epochgate/packages/runtime
COPY deploy/epochgate.container.yaml /etc/epochgate.yaml

EXPOSE 8765
VOLUME ["/data"]
CMD ["egctl", "serve", "--config", "/etc/epochgate.yaml"]

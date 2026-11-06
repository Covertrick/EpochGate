FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CFLAGS="-Wno-incompatible-pointer-types -Wno-error" \
    CXXFLAGS="-Wno-incompatible-pointer-types -Wno-error" \
    PATH="/opt/venv/bin:${PATH}"

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home --shell /bin/sh eg \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple \
        "setuptools<70" wheel "numpy<1.25" pyerfa pyyaml packaging hypothesis pytest cython extension-helpers \
    && chown -R eg:eg /opt/venv

COPY install-task.sh /usr/local/bin/install-task
RUN chmod 755 /usr/local/bin/install-task

USER eg
WORKDIR /workspace
CMD ["sleep", "infinity"]

FROM python:3.12-alpine AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv

RUN apk add --no-cache \
      build-base \
      libffi-dev \
      linux-headers \
      openssl-dev \
      zlib-dev \
    && python3 -m venv "${VIRTUAL_ENV}"

COPY requirements.txt /tmp/requirements.txt
RUN "${VIRTUAL_ENV}/bin/pip" install \
      --no-cache-dir \
      -r /tmp/requirements.txt


FROM python:3.12-alpine AS runtime

ENV PATH=/opt/venv/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apk add --no-cache libffi libstdc++ openssl zlib \
    && addgroup -S appuser \
    && adduser -S -G appuser appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY main.py /app/main.py
COPY src /app/src

RUN chown -R appuser:appuser /app

USER appuser

CMD ["/opt/venv/bin/python3", "main.py"]

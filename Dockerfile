FROM python:3.11.9-slim-bookworm

ARG SOURCE_COMMIT=unknown
LABEL org.opencontainers.image.title="ecommerce-agent-day15-offline-check" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPYCACHEPREFIX=/tmp/pycache \
    DAY15_SOURCE_COMMIT=${SOURCE_COMMIT}

WORKDIR /app

COPY requirements-day15.lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements-day15.lock.txt

COPY . .
RUN python -m compileall -q src tests streamlit_app.py

CMD ["python", "-m", "src.ecommerce_agent.day15_offline_check"]

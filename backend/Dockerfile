# Образ сервиса: API + раздача фронтенда + MCP-серверы (одна кодовая база, разные команды в compose).
# Собирается из корня репозитория: docker compose build (контекст — корень, нужен frontend/).

# ── фронтенд: React-конфигуратор -> frontend/app ──
FROM node:22-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build && rm -rf node_modules

# ── бэкенд ──
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# LibreOffice — рендер превью и PDF; poppler — PDF -> PNG; fontconfig — метрики шрифтов для вёрстки
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress poppler-utils fontconfig fonts-dejavu-core fonts-liberation2 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
# шрифты шаблонов (OFL), которых нет в дистрибутиве
COPY backend/scripts/install_fonts.sh scripts/install_fonts.sh
RUN FONT_DIR=/usr/share/fonts/prism bash scripts/install_fonts.sh

# зависимости отдельно от кода — слой кешируется, пока не меняется pyproject.toml
COPY backend/pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" \
        > /tmp/requirements.txt && pip install -r /tmp/requirements.txt

COPY backend/prism prism
COPY backend/app app
COPY backend/configs configs
COPY backend/skills skills
COPY backend/scripts scripts
COPY --from=frontend /src/frontend /app/frontend

RUN useradd --create-home --uid 1000 prism && mkdir -p data && chown -R prism /app/backend/data
USER prism
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS http://localhost:8000/api/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

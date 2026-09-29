# Prism — backend

Сервис генерации презентаций по шаблону: парсинг → поиск материалов → генерация → вёрстка → аудит → экспорт.

## Быстрый старт (docker compose)

```bash
cp backend/.env.example backend/.env    # LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, LLM_API_STYLE
docker compose up -d --build            # из корня репозитория
open http://localhost:8000
```

| Сервис | Что делает | Наружу |
|---|---|---|
| `backend` | API (FastAPI + LangGraph), фронтенд, рендер LibreOffice | `:8000` |
| `mcp-web` | MCP-сервер поиска: `web_search` (SearXNG), `fetch_page` (основной текст страницы) | нет |
| `mcp-pptx` | MCP-сервер pptx: `template_summary`, `inspect_deck`, `audit_deck`, `render_slide`, `set_shape_text` | нет |
| `searxng` | self-hosted метапоисковик, выдача в JSON | нет |

Шаблоны, колоды и база аккаунтов — в томе `prism-data`.

## Установка без Docker

```bash
cd backend
python -m venv venv && . venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # вписать LLM_API_KEY
scripts/install_fonts.sh    # шрифты шаблонов (Play, Montserrat, Carlito…) для рендера через LibreOffice
```

Для рендера превью и PDF нужен LibreOffice (`soffice`) и `pdftoppm` (poppler).

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `LLM_BASE_URL` | Провайдер модели — любой OpenAI-совместимый API (для Open WebUI оканчивается на `/api`) |
| `LLM_API_KEY` | Ключ провайдера |
| `LLM_MODEL` | Модель для текста |
| `LLM_VISION_MODEL` | Модель для картинок; пусто — та же, что `LLM_MODEL` |
| `LLM_API_STYLE` | `vllm` / `openrouter` / `plain` — как передавать режим размышлений и `top_k` |
| `MCP_WEB_URL` | MCP-сервер поиска (`http://mcp-web:8001/mcp` в compose); пусто — без поиска в интернете |
| `MCP_PPTX_URL` | MCP-сервер pptx (`http://mcp-pptx:8002/mcp` в compose) |
| `LLM_FAKE=1` | Демо-режим без провайдера: подставной текст, настоящие вёрстка и рендер |

## Аудит

После генерации каждую колоду проверяет детерминированный аудит (вёрстка, шаблон, плотность, целостность).
В режиме «Демонстрация» кнопка «Аудит» открывает панель: находки подсвечиваются на слайде, «проверить смысл»
запускает проверку моделью по картинкам слайдов, отмеченные находки исправляются кнопкой «Исправить».
Готовую колоду можно открыть по ссылке `/configurator.html?deck=<id>&audit=1`. Все проверки — в [AUDIT.md](../AUDIT.md).

## Поиск для короткого брифа

Узел `research` графа срабатывает, если бриф короче `research.min_brief_words` слов и почти без чисел
(«компания Nestle»). Скилл `researcher` пишет 2–3 запроса → MCP `web_search` (SearXNG) → MCP `fetch_page`
читает лучшие страницы (по одной с сайта) → скилл `research_digest` выбирает факты с источником. Факты
становятся `materials` планировщика и писателя: цифры в колоде берутся только из брифа или из них.
Бюджет — `research.budget_s` (60 с); не успели или MCP недоступен — колода по брифу, с предупреждением.
`fetch_page` не ходит во внутреннюю сеть (localhost, 10.x, 192.168.x, метаданные облака) — и на редиректах.

## MCP-серверы локально

```bash
SEARXNG_URL=http://localhost:8080 python -m prism.mcp.web_server --port 8001
python -m prism.mcp.pptx_server --port 8002     # файлы — только внутри backend/data
MCP_WEB_URL=http://127.0.0.1:8001/mcp MCP_PPTX_URL=http://127.0.0.1:8002/mcp uvicorn app.main:app
```

## Конфиг, роли, скиллы

- `configs/default.yaml` — провайдер (один, из `.env`) и **роли**. Узлы пайплайна обращаются к роли
  (`outline_planner`, `slide_writer`, `audit_vlm` …) — у роли свои температура, режим размышлений, лимиты.
  Смена модели или провайдера — только правка `.env`, код и конфиг не меняются.
- `skills/<name>/v<N>.yaml` — промпты (Jinja2) и привязка к роли. Версии закрепляются в секции
  `skills:` конфига, иначе берётся последняя. В manifest запуска пишется `name@vN` и sha256 файла.

## Запуск

```bash
(cd ../frontend && npm install && npm run build)   # React-конфигуратор -> frontend/app
uvicorn app.main:app --reload          # http://localhost:8000/configurator.html, API — /api/*
LLM_FAKE=1 uvicorn app.main:app        # демо-режим без провайдера: текст подставной, вёрстка настоящая
python scripts/generate.py -t data/templates/x.pptx -p "бриф…" [--slides 10]   # генерация из консоли
python scripts/demo_compose.py vk_tech   # вёрстка тестовой колоды без модели -> data/debug/
python scripts/smoke_llm.py            # проверка возможностей провайдера, отчёт в data/smoke/
python -m prism.parsing.extract data/templates/x.pptx   # извлечение шаблона в JSON (сырые факты)
python -m prism.parsing.spec data/templates/x.pptx      # полный разбор: токены, паттерны, ассеты
python -m prism.parsing.spec data/templates/x.pptx --llm   # + классификация слайдов моделью
python scripts/debug_patterns.py vk_tech 14 21         # рамки слотов и групп поверх рендера
python -m prism.audit.deterministic data/decks/<id>/<tpl>/v1/deck.pptx   # детерминированный аудит
pytest                                 # юнит-тесты (без сети)
pytest -m live                         # тесты с реальной моделью
```

## Ограничения

- Режим размышлений включается и выключается через `chat_template_kwargs.enable_thinking`. Это работает
  на vLLM, llama.cpp и SGLang. У другого провайдера smoke-тест `thinking_toggle` покажет, работает ли он.

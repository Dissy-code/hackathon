# ЦДС — backend (пакет `prism`)

Сервис генерации презентаций по шаблону: парсинг → поиск материалов → генерация → вёрстка → аудит → экспорт.

Запуск в Docker, переменные окружения и ограничения — в [корневом README](../README.md).
Здесь — то, что нужно для разработки.

## Установка для разработки

```bash
cd backend
python -m venv venv && . venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # вписать LLM_API_KEY
scripts/install_fonts.sh    # шрифты шаблонов (Play, Montserrat, Carlito…) для рендера через LibreOffice
```

Для рендера превью и PDF нужен LibreOffice (`soffice`) и `pdftoppm` (poppler).

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

## Особенности провайдеров

- Режим размышлений включается и выключается через `chat_template_kwargs.enable_thinking`. Это работает
  на vLLM, llama.cpp и SGLang. У другого провайдера smoke-тест `thinking_toggle` покажет, работает ли он.

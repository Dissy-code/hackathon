# Prism — backend

Сервис генерации презентаций по шаблону: парсинг → генерация → вёрстка → аудит → экспорт.

## Установка

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

## Конфиг, роли, скиллы

- `configs/default.yaml` — провайдер (один, из `.env`) и **роли**. Узлы пайплайна обращаются к роли
  (`outline_planner`, `slide_writer`, `audit_vlm` …) — у роли свои температура, режим размышлений, лимиты.
  Смена модели или провайдера — только правка `.env`, код и конфиг не меняются.
- `skills/<name>/v<N>.yaml` — промпты (Jinja2) и привязка к роли. Версии закрепляются в секции
  `skills:` конфига, иначе берётся последняя. В manifest запуска пишется `name@vN` и sha256 файла.

## Запуск

```bash
uvicorn app.main:app --reload          # http://localhost:8000/configurator.html, API — /api/*
python scripts/smoke_llm.py            # проверка возможностей провайдера, отчёт в data/smoke/
python -m prism.parsing.extract data/templates/x.pptx   # извлечение шаблона в JSON (сырые факты)
python -m prism.parsing.spec data/templates/x.pptx      # полный разбор: токены, паттерны, ассеты
python -m prism.parsing.spec data/templates/x.pptx --llm   # + классификация слайдов моделью
python scripts/debug_patterns.py vk_tech 14 21         # рамки слотов и групп поверх рендера
pytest                                 # юнит-тесты (без сети)
pytest -m live                         # тесты с реальной моделью
```

## Ограничения

- Режим размышлений включается и выключается через `chat_template_kwargs.enable_thinking`. Это работает
  на vLLM, llama.cpp и SGLang. У другого провайдера smoke-тест `thinking_toggle` покажет, работает ли он.

# Prism — backend

Сервис генерации презентаций по шаблону: парсинг → генерация → вёрстка → аудит → экспорт.

## Установка

```bash
cd backend
python -m venv venv && . venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # вписать OWUI_API_KEY
scripts/install_fonts.sh    # шрифты шаблонов (Play, Montserrat, Carlito…) для рендера через LibreOffice
```

Для рендера превью и PDF нужен LibreOffice (`soffice`) и `pdftoppm` (poppler).

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `PRISM_PROFILE` | Профиль моделей из `configs/default.yaml`: `dev` (Open WebUI) или `final` (инференс VK) |
| `OWUI_BASE_URL` | Open WebUI, OpenAI-совместимый API, оканчивается на `/api` |
| `OWUI_API_KEY` | Ключ Open WebUI (Settings → Account → API Keys) |
| `VK_LLM_BASE_URL`, `VK_LLM_API_KEY` | Инференс VK для финала |

## Конфиг, роли, скиллы

- `configs/default.yaml` — провайдеры, профили и **роли**. Узлы пайплайна обращаются к роли
  (`outline_planner`, `slide_writer`, `audit_vlm` …), а профиль решает, какая модель за ней стоит.
  Смена модели — `PRISM_PROFILE=final`, код не меняется.
- `skills/<name>/v<N>.yaml` — промпты (Jinja2) и привязка к роли. Версии закрепляются в секции
  `skills:` конфига, иначе берётся последняя. В manifest запуска пишется `name@vN` и sha256 файла.

## Запуск

```bash
uvicorn app.main:app --reload          # GET /health
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

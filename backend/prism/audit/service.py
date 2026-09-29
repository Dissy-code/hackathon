"""Аудит варианта колоды как состояние: что найдено, что исправлено, какая ревизия файла.

Файлы варианта (out/<шаблон>/v<N>/):
    deck.pptx, deck.pdf, preview/slide-NN.png  — текущая ревизия (после исправлений — новая);
    deck.orig.pptx                             — то, что собрала вёрстка, до первого исправления;
    report.json                                — отчёт вёрстки (тип каждого слайда);
    audit.json                                 — AuditState: находки обоих видов, ревизия, журнал исправлений.
Колода (out/): context.json — бриф, язык, материалы и шаблоны: то, с чем сверяет смысловой аудит.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from prism.audit import contextual
from prism.audit.deterministic import Issue, audit_deck, pattern_bg
from prism.audit.fixes import apply_fixes
from prism.export.html import export_html
from prism.parsing.spec import TemplateSpec
from prism.render import render_deck


class AuditState(BaseModel):
    rev: int = 0
    issues: list[Issue] = Field(default_factory=list)
    contextual: str = "none"                 # none | done | stale (после удаления слайдов) | error
    contextual_errors: list[str] = Field(default_factory=list)
    log: list[dict] = Field(default_factory=list)       # применённые исправления: {rev, issue, fix, message}


def load_state(vdir: Path) -> AuditState:
    path = vdir / "audit.json"
    return AuditState.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else AuditState()


def save_state(vdir: Path, state: AuditState) -> None:
    (vdir / "audit.json").write_text(state.model_dump_json(indent=1), encoding="utf-8")


def load_context(deck_dir: Path) -> dict:
    path = deck_dir / "context.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _report(vdir: Path, key: str) -> list[str]:
    path = vdir / "report.json"
    return [r[key] for r in json.loads(path.read_text(encoding="utf-8"))] if path.exists() else []


def _kinds(vdir: Path) -> list[str]:
    return _report(vdir, "kind")


def _template(context: dict, template_id: str) -> tuple[TemplateSpec | None, str | None]:
    tpl = context.get("templates", {}).get(template_id, {})
    spec_path = tpl.get("spec")
    spec = TemplateSpec.model_validate_json(Path(spec_path).read_text(encoding="utf-8")) \
        if spec_path and Path(spec_path).exists() else None
    return spec, tpl.get("pptx")


def deterministic(vdir: Path, context: dict, template_id: str) -> list[Issue]:
    spec, pptx = _template(context, template_id)
    return audit_deck(vdir / "deck.pptx", spec, pptx, _kinds(vdir), _report(vdir, "pattern"))


def run_deterministic(vdir: Path, context: dict, template_id: str) -> AuditState:
    """Первичный аудит сразу после вёрстки (узел audit графа)."""
    state = load_state(vdir)
    state.issues = [i for i in state.issues if i.mode == "contextual"] + deterministic(vdir, context, template_id)
    save_state(vdir, state)
    return state


async def run_contextual(vdir: Path, context: dict, llm, skills, only: set[int] | None = None) -> AuditState:
    """Смысловой аудит моделью: по слайдам (картинка + текст) и связность колоды."""
    if not (vdir / "audit.json").exists():             # детерминированная часть ещё не считалась
        await asyncio.to_thread(run_deterministic, vdir, context, vdir.parent.name)
    state = load_state(vdir)
    pngs = sorted((vdir / "preview").glob("*.png"))
    texts = contextual.slide_texts(vdir / "deck.pptx")
    kinds = _kinds(vdir)
    if not pngs or len(pngs) != len(texts):
        raise RuntimeError("нет превью слайдов для смысловой проверки")
    tpl_name = context.get("templates", {}).get(vdir.parent.name, {}).get("name", "")
    context = {**context, "template_name": Path(tpl_name).stem or "корпоративный шаблон"}
    per_slide, errors = await contextual.audit_slides(pngs, texts, kinds, context, llm, skills, only)
    story = await contextual.audit_storyline(texts, kinds, context, llm, skills) if only is None else []
    keep = [i for i in state.issues if i.mode == "deterministic"
            or (only is not None and i.slide not in only and i.check != "storyline")]
    state.issues = keep + per_slide + story
    state.contextual = "done"
    state.contextual_errors = errors
    save_state(vdir, state)
    return state


async def fix(vdir: Path, context: dict, template_id: str, choices: list[tuple[str, str]], llm, skills
              ) -> tuple[AuditState, dict]:
    """choices — (id находки, id исправления). Правит deck.pptx, перерисовывает превью, заново проверяет."""
    state = load_state(vdir)
    by_id = {i.id: i for i in state.issues}
    picked = [(by_id[iid], fid) for iid, fid in choices if iid in by_id]
    if not picked:
        raise ValueError("не выбрано ни одной находки из текущего аудита")
    deck = vdir / "deck.pptx"
    orig = vdir / "deck.orig.pptx"
    if not orig.exists():
        shutil.copy(deck, orig)
    spec, _ = _template(context, template_id)
    patterns = _report(vdir, "pattern")
    hints = [pattern_bg(spec, patterns, n) for n in range(1, len(patterns) + 1)]
    report = await apply_fixes(deck, picked, spec, llm=llm, skills=skills, context=context, bg_hints=hints)
    await asyncio.to_thread(render_deck, deck, vdir)
    await asyncio.to_thread(export_html, deck, vdir / "deck.html", context.get("brief", "Презентация")[:80])
    if report.deleted_slides:                      # номера слайдов в отчёте вёрстки сдвинулись
        _drop_report_slides(vdir, report.deleted_slides)
    state.rev += 1
    for issue, fid in picked:
        ok = issue.id in report.applied
        state.log.append({"rev": state.rev, "issue": issue.id, "fix": fid, "ok": ok,
                          "message": issue.message if ok else report.failed.get(issue.id, "")})
    fixed_ctx = {i.id for i, _ in picked if i.mode == "contextual" and i.id in report.applied}
    contextual_left = [i for i in state.issues if i.mode == "contextual" and i.id not in fixed_ctx]
    if report.deleted_slides:
        contextual_left = []
        if state.contextual == "done":
            state.contextual = "stale"
    state.issues = contextual_left + deterministic(vdir, context, template_id)
    save_state(vdir, state)
    return state, {"applied": report.applied, "failed": report.failed, "deleted_slides": report.deleted_slides}


def _drop_report_slides(vdir: Path, deleted: list[int]) -> None:
    path = vdir / "report.json"
    if not path.exists():
        return
    rows = [r for r in json.loads(path.read_text(encoding="utf-8")) if r["index"] not in deleted]
    for n, r in enumerate(rows, 1):
        r["index"] = n
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    # лишние превью от удалённых слайдов render_deck уже не пишет; убираем хвост старых файлов
    pngs = sorted((vdir / "preview").glob("*.png"))
    for p in pngs[len(rows):]:
        p.unlink(missing_ok=True)

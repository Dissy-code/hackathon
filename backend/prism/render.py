"""Рендер .pptx -> PDF -> PNG через LibreOffice и poppler: превью в интерфейсе, экспорт PDF, картинки для аудита."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

SOFFICE_TIMEOUT_S = 240


class RenderError(RuntimeError):
    pass


def pptx_to_pdf(pptx: Path, out_dir: Path) -> Path:
    """Отдельный профиль LibreOffice на вызов: иначе параллельные рендеры мешают друг другу."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lo-profile-") as profile:
        cmd = ["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
               "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=SOFFICE_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RenderError(f"LibreOffice не отработал: {e}") from e
    pdf = out_dir / f"{pptx.stem}.pdf"
    if not pdf.exists():
        raise RenderError(f"LibreOffice не создал PDF: {res.stderr.strip()[:300]}")
    return pdf


def pdf_to_png(pdf: Path, out_dir: Path, dpi: int = 60, prefix: str = "slide") -> list[Path]:
    """Страницы PDF в PNG: slide-01.png, slide-02.png…"""
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), f"{tmp}/p"],
                           capture_output=True, timeout=SOFFICE_TIMEOUT_S, check=True)
        except (OSError, subprocess.SubprocessError) as e:
            raise RenderError(f"pdftoppm не отработал: {e}") from e
        pages = sorted(Path(tmp).glob("p-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
        out = []
        for i, page in enumerate(pages, 1):
            target = out_dir / f"{prefix}-{i:02d}.png"
            shutil.move(page, target)
            out.append(target)
    return out


def render_deck(pptx: Path, out_dir: Path, dpi: int = 80) -> tuple[Path, list[Path]]:
    """PDF рядом с колодой + PNG превью слайдов в out_dir/preview (80 dpi: читаемо и для смыслового аудита)."""
    pdf = pptx_to_pdf(pptx, out_dir)
    return pdf, pdf_to_png(pdf, out_dir / "preview", dpi=dpi)

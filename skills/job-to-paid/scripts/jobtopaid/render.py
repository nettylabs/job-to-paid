"""Write documents to PDF and/or HTML.

Engines:
  builtin  pure-Python PDF (default; consistent everywhere; Western European text)
  chrome   HTML printed by a locally installed Chrome/Chromium/Edge (any language)
  auto     builtin, unless the text needs characters it can't show and a browser exists
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .documents import find_payment
from .html import render_html
from .pdf import _all_text, encodable, render_pdf
from .util import JTPError
from .view import build_receipt_view, build_view

_BROWSERS = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
    "microsoft-edge",
    "microsoft-edge-stable",
    "msedge",
)
_FIXED_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


def find_browser() -> str | None:
    env = os.environ.get("JTP_CHROME")
    if env and Path(env).exists():
        return env
    for name in _BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    for path in _FIXED_PATHS:
        if Path(path).exists():
            return path
    return None


def _browser_pdf(html_text: str, pdf_path: Path, browser: str) -> None:
    with tempfile.TemporaryDirectory(prefix="jtp-") as tmp:
        html_file = Path(tmp) / "doc.html"
        html_file.write_text(html_text, encoding="utf-8")
        base = [
            browser,
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            f"--user-data-dir={Path(tmp) / 'profile'}",
            "--no-pdf-header-footer",
            "--print-to-pdf-no-header",
            f"--print-to-pdf={pdf_path}",
        ]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            base.append("--no-sandbox")  # browsers refuse to start as root without it
        last_error = ""
        for headless in ("--headless=new", "--headless"):
            if pdf_path.exists():
                pdf_path.unlink()
            try:
                result = subprocess.run(
                    base[:1] + [headless] + base[1:] + [html_file.as_uri()], capture_output=True, text=True, timeout=90
                )
                last_error = (result.stderr or "")[-400:]
            except (OSError, subprocess.TimeoutExpired) as exc:
                last_error = str(exc)
                continue
            if pdf_path.exists() and pdf_path.stat().st_size > 0:
                return
        raise JTPError("The browser couldn't print the PDF.", hint=last_error.strip() or "Try --engine builtin.")


def write_view(view: dict, out_dir: Path, name: str, fmt: str = "pdf", engine: str = "auto") -> dict:
    if fmt not in ("pdf", "html", "both"):
        raise JTPError("format must be pdf, html or both.")
    if engine not in ("auto", "builtin", "chrome"):
        raise JTPError("engine must be auto, builtin or chrome.")
    out_dir.mkdir(parents=True, exist_ok=True)
    files, warnings = {}, []
    html_text = None
    if fmt in ("html", "both"):
        html_text = render_html(view)
        html_path = out_dir / f"{name}.html"
        html_path.write_text(html_text, encoding="utf-8")
        files["html"] = str(html_path)
    used = None
    if fmt in ("pdf", "both"):
        pdf_path = out_dir / f"{name}.pdf"
        needs_unicode = not all(encodable(t) for t in _all_text(view))
        browser = find_browser() if engine in ("chrome", "auto") else None
        if engine == "chrome" and not browser:
            raise JTPError(
                "No Chrome, Chromium or Edge found for --engine chrome.",
                hint="Install one, set JTP_CHROME to its path, or use --engine builtin.",
            )
        if engine == "chrome" or (engine == "auto" and needs_unicode and browser):
            try:
                _browser_pdf(html_text or render_html(view), pdf_path, browser)
                used = "chrome"
            except JTPError as exc:
                if engine == "chrome":
                    raise
                warnings.append(f"Browser PDF failed ({exc.message}); used the built-in PDF instead.")
        if used is None:
            warnings += render_pdf(view, pdf_path)
            used = "builtin"
        files["pdf"] = str(pdf_path)
    return {"files": files, "engine": used, "warnings": warnings}


def render_doc(store, doc: dict, fmt: str = "pdf", engine: str = "auto", out_dir=None) -> dict:
    view = build_view(store, doc)
    return write_view(view, Path(out_dir) if out_dir else store.output_dir, doc["id"], fmt, engine)


def render_receipt(store, payment_id: str, fmt: str = "pdf", engine: str = "auto", out_dir=None) -> dict:
    payment, invoice, job = find_payment(store, payment_id)
    view = build_receipt_view(store, payment, invoice, job)
    return write_view(view, Path(out_dir) if out_dir else store.output_dir, f"{payment['id']}-receipt", fmt, engine)


def python_ok() -> bool:
    return sys.version_info >= (3, 9)

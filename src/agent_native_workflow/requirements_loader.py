"""Requirements loader — reads requirements from any supported file format.

Supported formats:
  .md / .txt / .text  — plain text, read directly
  .docx               — Word document (requires: pip install python-docx)
  .pdf                — PDF (requires: pip install pypdf)
  .doc                — legacy Word (Open XML .doc only; binary .doc requires prior conversion to .docx)

Usage:
    from agent_native_workflow.requirements_loader import load_requirements

    text = load_requirements(Path("requirements.md"))
    text = load_requirements(Path("PROJ-123.docx"))   # Jira ticket
    text = load_requirements(Path("spec.pdf"))
"""

from __future__ import annotations

from pathlib import Path

_TEXT_SUFFIXES = {".md", ".txt", ".text", ""}


def load_requirements(path: Path) -> str:
    """Read requirements from any supported format. Returns plain text."""
    if not path.is_file():
        raise FileNotFoundError(f"Requirements file not found: {path}")

    suffix = path.suffix.lower()

    if suffix in _TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8")

    if suffix == ".docx":
        return _read_docx(path)

    if suffix == ".pdf":
        return _read_pdf(path)

    if suffix == ".doc":
        return _read_doc(path)

    # Unknown extension — attempt plain text read
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(
            f"Cannot read requirements file '{path}' as text.\n"
            f"Supported formats: .md, .txt, .docx, .pdf, .doc"
        ) from None


def is_text_format(path: Path) -> bool:
    """True if the file can be read directly by agents without conversion."""
    return path.suffix.lower() in _TEXT_SUFFIXES or path.suffix.lower() == ".md"


# ── Format-specific readers ───────────────────────────────────────────────────


def _read_docx(path: Path) -> str:
    from docx import Document  # type: ignore[import-untyped]

    doc = Document(str(path))
    sections: list[str] = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = para.style.name if para.style else ""
        # Promote headings to markdown
        if style.startswith("Heading 1"):
            sections.append(f"# {text}")
        elif style.startswith("Heading 2"):
            sections.append(f"## {text}")
        elif style.startswith("Heading 3"):
            sections.append(f"### {text}")
        else:
            sections.append(text)

    return "\n\n".join(sections)


def _read_doc(path: Path) -> str:
    import mammoth  # type: ignore[import-untyped]
    import zipfile

    try:
        with open(path, "rb") as f:
            result = mammoth.convert_to_markdown(f)
        return result.value
    except zipfile.BadZipFile:
        raise ValueError(
            f"Cannot read '{path}': legacy binary .doc format is not supported.\n"
            f"Please convert to .docx or .pdf first (e.g. open in Word/LibreOffice → Save As)."
        ) from None


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader  # type: ignore[import-untyped]

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())

    return "\n\n---\n\n".join(pages)

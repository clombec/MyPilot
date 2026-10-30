"""
importer.py

Import of a raw to-do pile: one non-empty line becomes one raw inbox task.

Nothing is interpreted. The original line is kept verbatim in `raw_text`,
the indentation is recorded in `notes`, and a provisional `parent` is set
only when the indentation is explicit and unambiguous. The agent reclassifies
everything later (see CLAUDE_clabpilot.md §6).
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction

from .models import AuditLog, Author, Project, Task

logger = logging.getLogger(__name__)

TAB_WIDTH = 4
BULLET_RE = re.compile(r"^(?:[-*•–·]|\d+[.)]|\[[ xX]?\])\s+")
ENCODINGS = ("utf-8-sig", "cp1252")


@dataclass
class ParsedLine:
    """One non-empty line of the raw file, with its indentation context."""

    line_no: int
    raw: str
    title: str
    indent: int
    level: int = 0
    under: int | None = None  # index of the enclosing line in the parsed list
    parent: int | None = None  # index of the provisional parent, if unambiguous


@dataclass
class ParseResult:
    """Outcome of parsing a raw file."""

    lines: list[ParsedLine] = field(default_factory=list)
    mixed_indent: bool = False


def read_lines(path: Path) -> list[str]:
    """Read a text file, trying UTF-8 first and then Windows-1252.

    Args:
        path: File to read.

    Returns:
        The file's lines without line terminators.

    Raises:
        UnicodeDecodeError: If no supported encoding can decode the file.
    """
    data = path.read_bytes()
    last_error: UnicodeDecodeError | None = None
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding).splitlines()
        except UnicodeDecodeError as exc:
            last_error = exc
    raise last_error  # type: ignore[misc]


def _indent_width(line: str) -> int:
    """Return the visual width of the leading whitespace of a line."""
    width = 0
    for char in line:
        if char == " ":
            width += 1
        elif char == "\t":
            width += TAB_WIDTH
        else:
            break
    return width


def _clean_title(line: str) -> str:
    """Strip whitespace and a leading bullet marker to build a task title."""
    title = BULLET_RE.sub("", line.strip(), count=1).strip()
    return (title or line.strip())[:500]


def parse_lines(lines: list[str]) -> ParseResult:
    """Parse raw lines into indentation-aware records, without creating anything.

    Parent rule (one level only, the parent is a "chantier"):
    a line gets a provisional parent only if it sits exactly one level under a
    top-level line, the file does not mix tabs and spaces, and the line does
    not dedent to an indentation never seen before.

    Args:
        lines: Lines of the raw file.

    Returns:
        A ParseResult listing every non-empty line.
    """
    result = ParseResult()
    leading = {ln[: len(ln) - len(ln.lstrip(" \t"))] for ln in lines if ln.strip()}
    result.mixed_indent = any("\t" in ws for ws in leading) and any(" " in ws for ws in leading)

    stack: list[int] = []  # indexes into result.lines, increasing indentation
    for line_no, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        item = ParsedLine(
            line_no=line_no, raw=raw, title=_clean_title(raw), indent=_indent_width(raw)
        )
        popped = False
        while stack and result.lines[stack[-1]].indent > item.indent:
            stack.pop()
            popped = True
        ambiguous = False
        if stack and result.lines[stack[-1]].indent == item.indent:
            stack.pop()  # sibling
        elif popped and item.indent > 0:
            ambiguous = True  # dedent to an indentation level never seen before
        elif not stack and item.indent > 0:
            ambiguous = True  # indented line with nothing above it

        item.level = len(stack) if not ambiguous else len(stack) + 1
        if stack:
            item.under = stack[-1]
        if (
            not ambiguous
            and not result.mixed_indent
            and len(stack) == 1
            and result.lines[stack[0]].indent == 0
        ):
            item.parent = stack[0]

        result.lines.append(item)
        stack.append(len(result.lines) - 1)
    return result


def _build_notes(item: ParsedLine, lines: list[ParsedLine]) -> str:
    """Describe the original position of a line, without interpreting it."""
    if item.level == 0:
        return ""
    note = f"niveau: {item.level}"
    if item.under is not None:
        note += f', sous: "{lines[item.under].title}"'
    return note


@transaction.atomic
def import_raw_lines(lines: list[str], project: Project | None = None) -> list[Task]:
    """Create one raw inbox task per non-empty line.

    Args:
        lines: Lines of the raw file.
        project: Project assigned to every created task, or None.

    Returns:
        The created tasks, in file order.
    """
    parsed = parse_lines(lines)
    tasks: list[Task] = []
    for item in parsed.lines:
        parent = tasks[item.parent] if item.parent is not None else None
        tasks.append(
            Task.objects.create(
                title=item.title,
                raw_text=item.raw,
                notes=_build_notes(item, parsed.lines),
                project=project,
                parent=parent,
                status=Task.Status.INBOX,
                clarity=Task.Clarity.RAW,
                created_by=Author.IMPORT,
            )
        )
    AuditLog.objects.create(
        tool="import_raw",
        arguments={"project": project.slug if project else None, "lines": len(tasks)},
        result=f"{len(tasks)} tasks created",
        undo_data={"created_task_ids": [t.pk for t in tasks]},
        actor=Author.IMPORT,
    )
    logger.info(
        "Raw import: %d tasks created (mixed indentation: %s)",
        len(tasks),
        parsed.mixed_indent,
    )
    return tasks

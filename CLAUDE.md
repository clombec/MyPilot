# CLAUDE.md — Coding Rules & Conventions

This file is read automatically by Claude Code at the start of every session.
It defines the coding standards for all projects in this workspace.

---

## 1. Language Policy

| Context | Language |
|---|---|
| Code, variables, functions, classes, constants | **English** |
| Comments and docstrings | **English** |
| UI text displayed to the user | **French** |
| Log messages | **English** |
| Error messages (internal) | **English** |
| Error messages (shown to user) | **French** |

---

## 2. Python Style — PEP 8

Reference: https://peps.python.org/pep-0008/

### Naming conventions

```python
my_variable  # snake_case — variables, functions, module names
MyClass  # PascalCase — classes
MY_CONSTANT  # UPPER_CASE — module-level constants
_private_func  # leading underscore — internal/private
__dunder__  # reserved for Python special methods
```

### Indentation & formatting

- 4 spaces per indentation level, never tabs
- Maximum line length: **99 characters**
- 2 blank lines between top-level definitions (classes, functions)
- 1 blank line between methods inside a class
- No trailing whitespace

### Imports

Grouped in this order, each group separated by a blank line:

```python
# 1. Standard library
import os
import json
from datetime import datetime
from pathlib import Path

# 2. Third-party
import yaml
from django.shortcuts import render
from django.http import JsonResponse

# 3. Local / project
from .models import ProsthesisRecord
from .services import get_sorted_records
```

Rules:
- Always use absolute imports for third-party packages
- Use relative imports (`.module`) for local project modules
- Never use wildcard imports (`from module import *`)

### Spacing

```python
# ✅ Correct
x = 1
my_list = [1, 2, 3]
result = my_func(a, b=None)
if x == 1:

# ❌ Wrong
x=1
my_list=[1,2,3]
result = my_func(a, b = None)
if x==1:
```

---

## 3. Docstrings — PEP 257 + Google Style

Reference: https://peps.python.org/pep-0257/

Always use **Google-style docstrings**, combined with PEP 484 type hints.

### Function — simple

```python
def parse_date(value: str | None) -> date | None:
    """Convert a DD/MM/YYYY string to a date object.

    Args:
        value: Date string in DD/MM/YYYY format, or None.

    Returns:
        A date object, or None if the input is empty or invalid.
    """
```

### Function — with exceptions

```python
def save_colors(colors: dict) -> None:
    """Write the colors dictionary to the YAML configuration file.

    Args:
        colors: Mapping of procedure name to hex color string.

    Raises:
        OSError: If the config file cannot be written.
    """
```

### Function — with example

```python
def get_sorted_records(sort_by: str = "patient", direction: str = "asc"):
    """Return all ProsthesisRecord objects sorted by the given field.

    Args:
        sort_by: Field name to sort on. Must be in SORTABLE_FIELDS.
            Defaults to 'patient'.
        direction: Sort direction, either 'asc' or 'desc'.
            Defaults to 'asc'.

    Returns:
        A Django QuerySet ordered by the specified field.

    Example:
        >>> records = get_sorted_records("send_date", "desc")
    """
```

### Class

```python
class DataSyncService:
    """Service for syncing records from the external data project.

    Handles upsert logic and date parsing for all ProsthesisRecord fields.

    Attributes:
        source_func: Callable that returns a list of record dicts.
    """
```

### Module

Every Python file starts with a module-level docstring, **in English** (the
language policy in §1 applies to the module docstring exactly as it does to
function and class docstrings — it is not an exception):

```python
"""
services.py

Business logic for ProsthesisRecord management.
Includes color configuration, record queries, and external data sync.
"""
```

---

## 4. Type Hints — PEP 484

Reference: https://peps.python.org/pep-0484/

- Always annotate **public function** signatures (args + return type)
- Use `X | None` instead of `Optional[X]` (Python 3.10+)
- Use `list[str]` instead of `List[str]` (Python 3.9+)

```python
# ✅ Modern style
def sync_records(records: list[dict]) -> dict:
def parse_date(value: str | None) -> date | None:
def load_colors() -> dict[str, str]:

# ❌ Old style (avoid)
from typing import Optional, List, Dict
def parse_date(value: Optional[str]) -> Optional[date]:
```

---

## 5. Error Handling

- Always use specific exception types, never bare `except:`
- Log errors with `logging.error()` or `logging.exception()` (includes traceback)
- Return meaningful values from service functions, raise in critical failures

```python
# ✅
try:
    data = json.loads(request.body)
except json.JSONDecodeError as exc:
    logging.error("Invalid JSON payload: %s", exc)
    return JsonResponse({"error": "Invalid JSON"}, status=400)

# ❌
try:
    data = json.loads(request.body)
except:
    pass
```

---

## 6. Logging

Use the standard `logging` module. Never use `print()` in production code.

```python
import logging

# Module-level — no logger creation needed if setup_logger() was called at startup
logging.info("Sync started")
logging.debug("Processing record: %s", patient_name)
logging.warning("Missing field: %s", field_name)
logging.error("Sync failed: %s", exc)
logging.exception("Unexpected error")  # includes full traceback
```

Log message format (set by `setup_logger`):
```
2026-04-07 10:23:01 | INFO     | Sync started
2026-04-07 10:23:01 | DEBUG    | Processing record: Dupont Jean
```

---

## 7. Django-specific Rules

- **Views** — one function per view, decorated explicitly (`@require_POST`, `@ensure_csrf_cookie`)
- **Services** — all business logic lives in `services.py`, never in views or models
- **Templates** — logic-free; all computation done in the view before passing context
- **URLs** — always name every route (`name='...'`)
- **Settings** — never hardcode secrets; use `os.environ.get()`

```python
# settings.py
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "change-me-in-production")
DEBUG = os.environ.get("DEBUG", "True") == "True"
```

---

## 8. File & Project Structure

```
project/
├── CLAUDE.md               ← this file
├── .env.example            ← versioned template, never .env itself
├── .gitignore
├── requirements.txt
├── README.md
├── manage.py
├── app/
│   ├── templatetags/
│   │   ├── __init__.py
│   │   └── app_extras.py
│   ├── templates/app/
│   │   └── index.html
│   ├── __init__.py
│   ├── apps.py
│   ├── models.py
│   ├── services.py         ← business logic only
│   ├── views.py            ← HTTP layer only
│   └── urls.py
```

---

## 9. Constants

Module-level constants go at the top of the file, after imports:

```python
SORTABLE_FIELDS = {
    "patient",
    "procedure",
    "send_date",
}

DEFAULT_COLOR = "#ffffff"
CONFIG_PATH = Path(__file__).parent / "configuration.yaml"
```

---

## 10. File Access Security — .gitignore

### Absolute rule

**Claude must never read, display, or process any file listed in `.gitignore`.**

This rule exists to prevent accidental exposure of secrets, credentials, local
configuration, and any other sensitive data that is intentionally excluded from
version control.

### Allowed exceptions

The following gitignored paths may be read without explicit permission, as they
contain no sensitive data:

```
.venv/          # virtual environment — may be inspected for installed packages
__pycache__/    # compiled bytecode — may be inspected for debugging
*.pyc           # compiled Python files
```

### All other gitignored files require explicit permission

This includes but is not limited to:

```
.env            # ⛔ secrets — NEVER read
*.sqlite3       # ⛔ local database — NEVER read
logs/           # ⛔ may contain sensitive runtime data — NEVER read
*.log           # ⛔ same as above
local_settings.py  # ⛔ local overrides — NEVER read
configuration.yaml # ⛔ may contain user data — NEVER read
```

### Procedure when a gitignored file is needed

If Claude needs the content of a restricted file to complete a task, it must:

1. **Stop and ask** — never attempt to read the file
2. **Explain why** the content is needed
3. **Wait** for the user to paste the relevant content directly in the prompt

```
# ✅ Correct behavior
"I need the content of your .env file to check the database configuration.
Please paste the relevant lines directly in the chat
(you can redact sensitive values if needed)."

# ❌ Forbidden
open(".env").read()
Path(".env").read_text()
```

### How Claude identifies gitignored files

At the start of each session, Claude reads `.gitignore` to build the list of
restricted paths. If `.gitignore` does not exist, Claude applies the default
restrictions listed above.

---

## 11. What Claude Should Always Do

- Write all code, variables, comments in **English**
- Apply PEP 8 formatting without being asked
- Add Google-style docstrings to every public function
- Add type hints to every public function signature
- Use `logging` instead of `print`
- Use `Path(__file__)` for file paths, never relative strings
- Group and order imports correctly
- Propose `.env.example` when secrets are involved
- **Never read any file listed in `.gitignore`** — ask the user to paste content instead
- Read `.gitignore` at session start to identify restricted files
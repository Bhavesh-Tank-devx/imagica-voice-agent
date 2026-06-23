---
name: python-code-style
description: >
  Apply this skill whenever Claude is writing, reviewing, refactoring, or explaining Python code of any kind —
  scripts, modules, classes, APIs, CLI tools, data pipelines, AI/ML code, or utility functions. This skill
  encodes production-grade Python style synthesized from PEP 8, Google Python Style Guide, and the STScI
  Python Guide. It governs: formatting, naming, imports, docstrings, type annotations, error handling,
  comprehensions, and code structure. Always use this skill when generating Python code, even for small
  snippets — correct style habits matter. Especially important for interview prep, production codebases,
  open-source contributions, and AI/ML project code.
---

# Python Code Style Skill

Synthesized from: **PEP 8**, **Google Python Style Guide**, and **STScI Python Style Guide**.

When writing Python code, apply ALL rules in this skill by default. Only deviate when the user's
existing codebase has a different consistent style (consistency within a project beats this guide).

---

## 1. Formatting & Layout

### Indentation & Line Length
- **4 spaces** per indentation level. Never tabs.
- Line limit: **88 characters** (Black-compatible, widely accepted in modern projects).
  - Docstrings and comments: **79 characters max** (PEP 8 conservative; aids readability).
- Wrap long lines using Python's implicit continuation inside `()`, `[]`, `{}`. Prefer this over `\`.

```python
# Good — implicit continuation
result = some_long_function_name(
    argument_one,
    argument_two,
    argument_three,
)

# Bad — backslash continuation (avoid unless unavoidable)
result = some_long_function_name(arg_one, \
                                 arg_two)
```

### Binary Operators at Line Breaks
Break **before** the operator (Knuth style — easier to scan):

```python
# Good
total = (
    gross_wages
    + taxable_interest
    - ira_deduction
    - student_loan_interest
)

# Bad
total = (gross_wages +
         taxable_interest -
         ira_deduction)
```

### Blank Lines
- **2 blank lines** before and after top-level functions and classes.
- **1 blank line** between methods inside a class.
- Use blank lines **sparingly** inside functions to separate logical blocks.

### Trailing Commas
Use trailing commas on multi-line structures. Makes version-control diffs cleaner:

```python
# Good
FILES = [
    "setup.cfg",
    "tox.ini",
]

# Good — tuple of one element always needs trailing comma
SINGLETON = ("value",)
```

---

## 2. Imports

Always at the top of the file, after module docstring, before globals/constants.

### Order (one blank line between each group)
1. Standard library (`os`, `sys`, `pathlib`, etc.)
2. Third-party packages (`numpy`, `requests`, `fastapi`, etc.)
3. Local application/project imports

```python
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from myproject.utils import helper
from myproject.models import User
```

### Rules
- **One import per line** for modules: `import os` not `import os, sys`.
- `from x import y` is fine for specific names from a module.
- **Never `from module import *`** — pollutes namespace, breaks tooling.
- Use **absolute imports** by default; explicit relative imports (`from . import x`) only when package layout demands it.
- Standard aliases to always respect: `import numpy as np`, `import pandas as pd`, `import matplotlib.pyplot as plt`.
- Module-level dunders (`__all__`, `__version__`, `__author__`) go after docstring but before imports (except `from __future__`).

---

## 3. Naming Conventions

| Entity | Convention | Example |
|---|---|---|
| Variables & functions | `snake_case` | `user_count`, `calculate_total()` |
| Classes | `PascalCase` | `UserProfile`, `DataLoader` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_RETRIES`, `BASE_URL` |
| Modules & packages | `lowercase` (short) | `utils.py`, `mypackage` |
| Private / internal | `_leading_underscore` | `_validate_input()` |
| Name-mangled (class) | `__double_underscore` | `__secret` (use rarely) |
| Dunder methods | `__dunder__` | `__init__`, `__repr__` |
| Type variables | Short, uppercase | `T`, `KT`, `VT` |
| Exception classes | `PascalCase` + `Error` suffix | `ValidationError`, `NotFoundError` |

**Naming philosophy:**
- Be descriptive. `number_of_retries` > `n` > `x`.
- Avoid single-letter names except in loops (`i`, `j`, `k`) or math (`x`, `y`).
- Avoid names that shadow builtins: `list`, `id`, `type`, `input`, `filter`.
- For acronyms in PascalCase: capitalize the whole acronym — `HTTPServer`, not `HttpServer`.

---

## 4. Docstrings

Write docstrings for **all public** modules, classes, functions, and methods.

### Format: Google-style (default for general code)

```python
def fetch_user(user_id: int, active_only: bool = True) -> dict:
    """Fetch a user record from the database.

    Args:
        user_id: The unique identifier of the user.
        active_only: If True, raises an error for inactive users.

    Returns:
        A dictionary containing user fields (id, name, email).

    Raises:
        ValueError: If user_id is not a positive integer.
        UserNotFoundError: If no user exists with the given ID.
    """
```

### Format: NumPy-style (for scientific/data/ML code)

```python
def compute_iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    """Compute Intersection over Union for two bounding boxes.

    Parameters
    ----------
    box_a : np.ndarray
        Array of shape (4,) in [x1, y1, x2, y2] format.
    box_b : np.ndarray
        Array of shape (4,) in [x1, y1, x2, y2] format.

    Returns
    -------
    float
        IoU score between 0.0 and 1.0.

    Notes
    -----
    Both boxes must be in the same coordinate space.
    """
```

### Docstring rules
- One-liners: `"""Return the square of n."""` — closing `"""` on same line.
- Multi-line: opening `"""` on first line, closing `"""` on its own line.
- Use **Google-style** for web/backend/general code.
- Use **NumPy-style** for ML, scientific, or data pipeline code.
- For generators, use `Yields:` instead of `Returns:`.

---

## 5. Type Annotations

Always annotate **public** functions and methods. Annotate private ones when non-obvious.

```python
from typing import Optional, Union
from collections.abc import Sequence

# Good — annotated public function
def process_items(
    items: Sequence[str],
    limit: Optional[int] = None,
) -> list[str]:
    ...

# Good — Python 3.10+ union syntax
def get_value(key: str) -> int | None:
    ...

# Good — typed class attributes
class Config:
    host: str
    port: int = 8080
    debug: bool = False
```

**Rules:**
- Never annotate `self` or `cls`.
- Use `X | None` (Python 3.10+) or `Optional[X]` for nullable types.
- Prefer built-in generics (`list[str]`, `dict[str, int]`) over `List`, `Dict` from `typing` (Python 3.9+).
- Use `from __future__ import annotations` for forward references in older codebases.
- `# type: ignore` is acceptable but must have an explanatory comment.

---

## 6. Exception Handling

```python
# Good — specific exception, minimal try block
try:
    value = int(user_input)
except ValueError:
    raise ValueError(f"Expected integer, got: {user_input!r}")

# Good — finally for cleanup
try:
    conn = connect()
    result = conn.query(sql)
finally:
    conn.close()

# Good — custom exception
class InsufficientFundsError(ValueError):
    """Raised when account balance is insufficient."""
    pass

# Bad — bare except catches EVERYTHING including KeyboardInterrupt, SystemExit
try:
    do_something()
except:          # NEVER do this
    pass

# Bad — catching base Exception is almost always wrong
try:
    do_something()
except Exception:  # Avoid unless re-raising or at outermost boundary
    pass
```

**Rules:**
- Raise `ValueError` for bad inputs, `TypeError` for wrong types, `RuntimeError` for unexpected state.
- Keep `try` blocks as small as possible — only the line(s) that can actually raise.
- Use `finally` for cleanup (file close, DB connection release).
- Custom exceptions must inherit from an appropriate base (`ValueError`, `Exception`, etc.).
- Exception class names must end in `Error`.
- Never use `assert` for runtime validation in production code (it's disabled with `-O`). Use `if ... raise` instead.

---

## 7. Comprehensions & Generators

```python
# Good — simple list comprehension
squares = [x**2 for x in range(10)]

# Good — with filter
evens = [x for x in range(20) if x % 2 == 0]

# Good — dict comprehension
word_lengths = {word: len(word) for word in words}

# Good — generator for large data (memory efficient)
total = sum(x**2 for x in range(1_000_000))

# Bad — nested for in comprehension (unreadable)
pairs = [(x, y) for x in range(5) for y in range(5) if x != y]

# Better — use a regular loop for nested logic
pairs = []
for x in range(5):
    for y in range(5):
        if x != y:
            pairs.append((x, y))
```

**Rules:**
- Max **one `for` clause** in a comprehension. Two or more → use a loop.
- Max **one `if` filter** per comprehension. More logic → use a loop or helper function.
- Prefer generator expressions (`(x for x in ...)`) over list comprehensions when the result is immediately consumed by `sum()`, `max()`, `any()`, etc.

---

## 8. Common Patterns & Pitfalls

### Mutable Default Arguments (Classic Bug)
```python
# Bug — default list is shared across ALL calls
def append_item(item, lst=[]):
    lst.append(item)
    return lst

# Fix — use None sentinel
def append_item(item, lst=None):
    if lst is None:
        lst = []
    lst.append(item)
    return lst
```

### None & Boolean Checks
```python
# Good
if value is None:        # identity check for None
if value is not None:
if items:                # implicit falsy for empty sequences
if not items:

# Bad
if value == None:        # == for None is an antipattern
if items != []:          # verbose, use `if items`
if flag == True:         # never compare to True/False with ==
```

### Default Iterators
```python
# Good — Pythonic
for key in my_dict:
for key, value in my_dict.items():
for line in file:

# Bad — redundant method calls
for key in my_dict.keys():     # .keys() is redundant
for line in file.readlines():  # loads all into memory
```

### String Formatting
```python
# Preferred — f-strings (Python 3.6+, most readable)
name = "Bhavesh"
msg = f"Hello, {name}! You have {count} messages."

# Acceptable — .format() for dynamic templates
template = "Hello, {}!".format(name)

# Avoid — % formatting (old style)
msg = "Hello, %s!" % name
```

### Context Managers
```python
# Always use `with` for files, sockets, DB connections
with open("data.txt", "r") as f:
    content = f.read()

# Multiple resources — Python 3.10+ style
with open("in.txt") as fin, open("out.txt", "w") as fout:
    fout.write(fin.read())
```

### Module Guard
```python
# Always include this in executable scripts
def main():
    ...

if __name__ == "__main__":
    main()
```

---

## 9. Code Structure & Function Design

- **Function length**: Aim for ≤40 lines. If longer, extract sub-functions.
- **Single responsibility**: Each function does one thing. Its name should fully describe it.
- **Avoid mutable global state**. Constants (`UPPER_CASE`) at module level are fine.
- **Properties** (`@property`) for computed attributes that are cheap and unsurprising. Don't hide expensive computation in properties.
- **Lambdas**: Only for one-liners. For anything >60 chars or multi-line, use `def`.
- Use `operator.mul`, `operator.add` etc. over `lambda x, y: x * y` for standard operations.

---

## 10. Whitespace Rules (Quick Reference)

```python
# Good — space around binary operators
x = x + 1
c = (a + b) * (a - b)
x = x*2 - 1       # okay: tighter binding communicates precedence

# Good — no space before colon/comma
spam(ham[1], {eggs: 2})
if x == 4: print(x)

# Good — no space before function call parens
spam(1)      # not spam (1)
lst[index]   # not lst [index]

# Good — keyword args: no spaces around =
def func(a, b=0): ...
func(a=1, b=2)

# Good — annotated params with default: space around =
def func(a: int, b: str = "default"): ...
```

---

## 11. Linting & Tooling (Production Standard)

Always configure these for any real project:

| Tool | Purpose | Config |
|---|---|---|
| `ruff` | Fast linter + formatter (replaces flake8+isort) | `ruff.toml` or `pyproject.toml` |
| `black` | Opinionated formatter (88 char default) | `pyproject.toml` |
| `mypy` | Static type checker | `mypy.ini` or `pyproject.toml` |
| `pytest` | Testing | `pytest.ini` or `pyproject.toml` |
| `pylint` | Deep linting (Google-preferred) | `.pylintrc` |

Minimal `pyproject.toml` setup:
```toml
[tool.black]
line-length = 88

[tool.ruff]
line-length = 88
select = ["E", "F", "I", "N", "UP"]

[tool.mypy]
python_version = "3.11"
strict = true
```

---

## 12. Interview & Production Checklist

Before finalizing any Python code, verify:
- [ ] All public functions/classes have docstrings with Args/Returns/Raises
- [ ] Type annotations on all public interfaces
- [ ] No bare `except:` or `except Exception:`
- [ ] No mutable default arguments
- [ ] Imports ordered: stdlib → third-party → local
- [ ] No `from x import *`
- [ ] `if __name__ == "__main__":` guard in scripts
- [ ] Context managers (`with`) for file/resource handling
- [ ] Comprehensions have ≤1 `for` clause
- [ ] Constants in `UPPER_SNAKE_CASE`
- [ ] No global mutable state
- [ ] Functions ≤40 lines; single responsibility

---

## Style Decision Priority

When rules conflict, follow this priority:
1. **Consistency within the existing codebase** — always trumps this guide
2. **Readability** — if a rule makes code harder to read, break it consciously
3. **This skill's rules** — the production-grade default
4. **PEP 8** — the community baseline

> "A Foolish Consistency is the Hobgoblin of Little Minds." — PEP 8
> When in doubt: make it readable first.

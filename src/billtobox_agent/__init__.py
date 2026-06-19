"""BillToBoxAgent — a supervised invoice-processing agent.

Sub-packages (filled in over the WORKPLAN phases):

* ``config``     — YAML + Pydantic configuration (task 4)
* ``data``       — SQLite engine, ORM models, repositories, migrations (task 5)
* ``monitoring`` — structlog + stdlib logging (task 7)
* ``mail``       — Gmail / Outlook / Doccle connectors (tasks 8-10)
* ``extraction`` — accounting-period logic + Claude extraction (tasks 6, 13)
* ``drive``      — Google Drive quarter folders + PDF upload (tasks 14-15)
* ``billtobox``  — SMTP send to the Billtobox mailbox (task 20)
* ``agent``      — linear worker pipeline + tool-calling loop (tasks 17, 21)
* ``web``        — FastAPI + Jinja2 dashboard, API, SSE log stream (tasks 18-19)
* ``utils``      — shared helpers (clock, redaction, ...)
"""

from __future__ import annotations

__version__ = "0.1.0"

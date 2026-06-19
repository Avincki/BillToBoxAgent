"""Pure accounting-period logic: map an invoice date to ``(fy_label, quarter)``.

No I/O, no dependencies. Calendar-year quarters by default; an offset fiscal year
is selected with ``fy_start_month`` (CLAUDE.md "Accounting Quarter Logic").

The fiscal year is labelled by the calendar year in which it *starts*: with
``fy_start_month=4`` the year Apr 2026 to Mar 2027 is "2026", so Jan-Mar 2026 fall in
Q4 of the prior label ("2025").
"""

from __future__ import annotations

from datetime import date


def period_for(
    invoice_date: date,
    fy_start_month: int = 1,
    fy_label_prefix: str = "",
) -> tuple[str, str]:
    """Return ``(fy_label, quarter)`` for ``invoice_date``.

    Examples — calendar year (``fy_start_month=1``)::

        2026-01-15 -> ("2026", "Q1")
        2026-12-31 -> ("2026", "Q4")

    Offset fiscal year (``fy_start_month=4``)::

        2026-04-01 -> ("2026", "Q1")
        2026-02-10 -> ("2025", "Q4")

    Label prefix (``fy_label_prefix="FY"``)::

        2026-05-31 -> ("FY2026", "Q2")
    """
    if not 1 <= fy_start_month <= 12:
        raise ValueError(f"fy_start_month must be 1-12, got {fy_start_month}")

    month_index = (invoice_date.month - fy_start_month) % 12
    quarter = month_index // 3 + 1
    fy_year = invoice_date.year if invoice_date.month >= fy_start_month else invoice_date.year - 1
    return f"{fy_label_prefix}{fy_year}", f"Q{quarter}"

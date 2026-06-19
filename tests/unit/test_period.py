from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from billtobox_agent.extraction import period_for


@pytest.mark.parametrize(
    ("month", "quarter"),
    [
        (1, "Q1"),
        (2, "Q1"),
        (3, "Q1"),
        (4, "Q2"),
        (5, "Q2"),
        (6, "Q2"),
        (7, "Q3"),
        (8, "Q3"),
        (9, "Q3"),
        (10, "Q4"),
        (11, "Q4"),
        (12, "Q4"),
    ],
)
def test_calendar_year_every_month(month: int, quarter: str) -> None:
    label, q = period_for(date(2026, month, 15))
    assert label == "2026"
    assert q == quarter


@pytest.mark.parametrize(
    ("month", "label", "quarter"),
    [
        (4, "2026", "Q1"),
        (5, "2026", "Q1"),
        (6, "2026", "Q1"),
        (7, "2026", "Q2"),
        (8, "2026", "Q2"),
        (9, "2026", "Q2"),
        (10, "2026", "Q3"),
        (11, "2026", "Q3"),
        (12, "2026", "Q3"),
        (1, "2025", "Q4"),
        (2, "2025", "Q4"),
        (3, "2025", "Q4"),
    ],
)
def test_april_fiscal_year_every_month(month: int, label: str, quarter: str) -> None:
    assert period_for(date(2026, month, 10), fy_start_month=4) == (label, quarter)


def test_calendar_boundary_months() -> None:
    assert period_for(date(2026, 1, 1)) == ("2026", "Q1")
    assert period_for(date(2026, 3, 31)) == ("2026", "Q1")
    assert period_for(date(2026, 4, 1)) == ("2026", "Q2")
    assert period_for(date(2026, 12, 31)) == ("2026", "Q4")


def test_fy_label_rollover_at_year_boundary() -> None:
    # The fiscal year starting Apr 2025 spans Dec 2025 (Q3) into Jan-Mar 2026 (Q4).
    assert period_for(date(2025, 12, 31), fy_start_month=4) == ("2025", "Q3")
    assert period_for(date(2026, 1, 1), fy_start_month=4) == ("2025", "Q4")
    assert period_for(date(2026, 3, 31), fy_start_month=4) == ("2025", "Q4")
    assert period_for(date(2026, 4, 1), fy_start_month=4) == ("2026", "Q1")


def test_july_fiscal_year_spot_checks() -> None:
    assert period_for(date(2026, 7, 1), fy_start_month=7) == ("2026", "Q1")
    assert period_for(date(2026, 6, 30), fy_start_month=7) == ("2025", "Q4")
    assert period_for(date(2026, 1, 15), fy_start_month=7) == ("2025", "Q3")


@pytest.mark.parametrize(("prefix", "expected"), [("", "2026"), ("FY", "FY2026")])
def test_label_prefix_variants(prefix: str, expected: str) -> None:
    label, _ = period_for(date(2026, 5, 31), fy_label_prefix=prefix)
    assert label == expected


@pytest.mark.parametrize("bad_month", [0, 13, -1])
def test_invalid_fy_start_month(bad_month: int) -> None:
    with pytest.raises(ValueError, match="fy_start_month"):
        period_for(date(2026, 1, 1), fy_start_month=bad_month)


@given(
    invoice_date=st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31)),
    fy_start=st.integers(min_value=1, max_value=12),
)
def test_property_label_and_quarter_well_formed(invoice_date: date, fy_start: int) -> None:
    label, quarter = period_for(invoice_date, fy_start_month=fy_start)
    assert quarter in {"Q1", "Q2", "Q3", "Q4"}
    # The label is the start year of the fiscal year the date falls in.
    assert label in {str(invoice_date.year), str(invoice_date.year - 1)}
    # The month equal to the fiscal-year start is always Q1.
    if invoice_date.month == fy_start:
        assert quarter == "Q1"

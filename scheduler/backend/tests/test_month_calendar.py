"""
month_calendar: the period day ranges, Alberta's statutory holidays and the
suggested yellow marks that replace the hand-edited template header.
"""

from __future__ import annotations

import datetime as dt

import pytest

from scheduler.backend import month_calendar as mc


def test_period_ranges_follow_the_spillover_convention():
    nov = mc.period_days(2026, 11)
    assert nov[0] == dt.date(2026, 11, 1) and nov[-1] == dt.date(2026, 12, 1) and len(nov) == 31
    dec = mc.period_days(2026, 12)
    assert dec[0] == dt.date(2026, 12, 2) and dec[-1] == dt.date(2027, 1, 1) and len(dec) == 31
    jan = mc.period_days(2027, 1)
    assert jan[0] == dt.date(2027, 1, 2) and jan[-1] == dt.date(2027, 1, 31) and len(jan) == 30
    feb = mc.period_days(2027, 2)
    assert feb[0] == dt.date(2027, 2, 1) and feb[-1] == dt.date(2027, 2, 28) and len(feb) == 28
    mar = mc.period_days(2027, 3)
    assert mar[0] == dt.date(2027, 3, 1) and mar[-1] == dt.date(2027, 3, 31) and len(mar) == 31
    assert mc.spillover_day(2026, 12) == dt.date(2027, 1, 1)
    assert mc.spillover_day(2027, 3) is None
    assert mc.period_id(2027, 3) == "2027-03" and mc.period_label(2027, 3) == "March 2027"


def test_alberta_statutory_holidays_known_dates():
    h2026 = mc.alberta_statutory_holidays(2026)
    assert h2026[dt.date(2026, 2, 16)] == "Family Day"
    assert h2026[dt.date(2026, 4, 3)] == "Good Friday"
    assert h2026[dt.date(2026, 5, 18)] == "Victoria Day"
    assert h2026[dt.date(2026, 9, 7)] == "Labour Day"
    assert h2026[dt.date(2026, 10, 12)] == "Thanksgiving"
    assert len(h2026) == 9
    h2027 = mc.alberta_statutory_holidays(2027)
    assert h2027[dt.date(2027, 2, 15)] == "Family Day"
    assert h2027[dt.date(2027, 3, 26)] == "Good Friday"
    assert h2027[dt.date(2027, 5, 24)] == "Victoria Day"
    assert h2027[dt.date(2027, 10, 11)] == "Thanksgiving"
    opt = mc.alberta_optional_holidays(2027)
    assert opt[dt.date(2027, 3, 29)] == "Easter Monday"
    assert opt[dt.date(2027, 8, 2)] == "Heritage Day"
    assert opt[dt.date(2027, 12, 26)] == "Boxing Day"


def test_suggested_marks_match_the_hand_made_templates():
    # October 2026: the 12th only (Thanksgiving), as the real template had.
    oct_marks = mc.suggested_marks(2026, 10)
    assert [(m.date.day, m.label) for m in oct_marks] == [(12, "Thanksgiving")]
    # November 2026: Remembrance Day; the Dec 1 spill-over day is NOT marked.
    assert [(m.date.day, m.label) for m in mc.suggested_marks(2026, 11)] == [(11, "Remembrance Day")]
    # December: Dec 24 -> Jan 1 as one Christmas window, every year on record.
    dec = mc.suggested_marks(2026, 12)
    assert [m.date for m in dec] == [dt.date(2026, 12, 24) + dt.timedelta(days=i) for i in range(9)]
    assert {m.label for m in dec} == {mc.CHRISTMAS_WINDOW_LABEL}
    assert mc.labelled_runs(dec) == [(dt.date(2026, 12, 24), dt.date(2027, 1, 1), mc.CHRISTMAS_WINDOW_LABEL)]
    # January 2027 starts on the 2nd, so New Year's Day belongs to December's sheet.
    assert mc.suggested_marks(2027, 1) == []
    # March 2027: Good Friday pre-marked; Easter Monday only a hint.
    assert [(m.date, m.label) for m in mc.suggested_marks(2027, 3)] == [(dt.date(2027, 3, 26), "Good Friday")]
    assert [(m.date, m.label) for m in mc.optional_holidays_in_period(2027, 3)] == [(dt.date(2027, 3, 29), "Easter Monday")]


def test_labelled_runs_and_descriptions():
    marks = [
        mc.MarkedDay(dt.date(2027, 3, 29), "EPSB Spring Break"),
        mc.MarkedDay(dt.date(2027, 3, 27), "EPSB Spring Break"),
        mc.MarkedDay(dt.date(2027, 3, 26), "Good Friday"),
        mc.MarkedDay(dt.date(2027, 3, 28), "EPSB Spring Break"),
        mc.MarkedDay(dt.date(2027, 3, 31), ""),
    ]
    assert mc.labelled_runs(marks) == [
        (dt.date(2027, 3, 26), dt.date(2027, 3, 26), "Good Friday"),
        (dt.date(2027, 3, 27), dt.date(2027, 3, 29), "EPSB Spring Break"),
        (dt.date(2027, 3, 31), dt.date(2027, 3, 31), ""),
    ]
    assert mc.describe_runs(marks) == ["Mar 26: Good Friday", "Mar 27 - Mar 29: EPSB Spring Break", "Mar 31"]


def test_explicit_period_range_overrides_the_default():
    # The scheduler types the sheet's first and last day (user request
    # 2026-10-31): December running Dec 1 -> Dec 31, say, or January from the 1st.
    assert mc.default_period_range(2026, 12) == (dt.date(2026, 12, 2), dt.date(2027, 1, 1))
    dec = mc.period_days(2026, 12, dt.date(2026, 12, 1), dt.date(2026, 12, 31))
    assert dec[0] == dt.date(2026, 12, 1) and dec[-1] == dt.date(2026, 12, 31) and len(dec) == 31
    assert mc.spillover_day(2026, 12, dt.date(2026, 12, 1), dt.date(2026, 12, 31)) is None
    # Two spill-over days: the first one is reported.
    assert mc.spillover_day(2026, 12, None, dt.date(2027, 1, 2)) == dt.date(2027, 1, 1)
    # Partial overrides keep the other end's default.
    assert mc.period_days(2027, 1, dt.date(2027, 1, 1))[0] == dt.date(2027, 1, 1)
    assert mc.period_days(2027, 1, dt.date(2027, 1, 1))[-1] == dt.date(2027, 1, 31)
    # A January sheet that starts on the 1st shows New Year's Day by name
    # (the Christmas window label only when the sheet reaches Dec 24).
    assert mc.suggested_marks(2027, 1, dt.date(2027, 1, 1)) == [mc.MarkedDay(dt.date(2027, 1, 1), "New Year's Day")]
    # A December sheet cut off before the 24th carries no Christmas marks at all.
    assert mc.suggested_marks(2026, 12, dt.date(2026, 12, 1), dt.date(2026, 12, 23)) == []
    # Suggested marks follow the explicit range.
    short_dec = mc.suggested_marks(2026, 12, dt.date(2026, 12, 1), dt.date(2026, 12, 31))
    assert [m.date.day for m in short_dec] == list(range(24, 32))
    # Guard rails: first day in the month, last day not before it, at most a week over.
    with pytest.raises(ValueError, match="first day must fall in December 2026"):
        mc.period_days(2026, 12, dt.date(2026, 11, 30))
    with pytest.raises(ValueError, match="before its first day"):
        mc.period_days(2026, 12, dt.date(2026, 12, 10), dt.date(2026, 12, 9))
    with pytest.raises(ValueError, match="at most 7 days past"):
        mc.period_days(2026, 12, None, dt.date(2027, 1, 8))
    assert mc.period_days(2026, 12, None, dt.date(2027, 1, 7))[-1] == dt.date(2027, 1, 7)

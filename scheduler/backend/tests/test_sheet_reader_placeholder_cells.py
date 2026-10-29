"""A letter-less placeholder in a name cell ("—-", "---") is an empty cell, not an unresolved physician."""
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.sheets_schedule_reader import parse_schedule_grid


def _roster():
    return {"P01": PhysicianConfig(id="P01", name="Doe J", first_name="Jane", last_name="Doe")}


def test_dash_placeholders_are_blank_cells():
    grid = [
        ["", "SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"],
        ["", "1", "2", "3", "4", "5", "6", "7"],
        ["RAH A", "Doe", "—-", "---", "", "None", "- -", "Doe"],
        ["0600-1200", "", "", "", "", "", "", ""],
    ]
    unresolved: list[str] = []
    result = parse_schedule_grid(grid, 2026, 11, _roster(), unresolved=unresolved)
    assert unresolved == []
    assert len(result.assignments) == 2
    assert len(result.unfilled) == 5

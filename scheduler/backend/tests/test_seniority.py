"""
Unit tests for config.py's seniority_multiplier() -- the hire_year-derived
multiplier that composes with priority_weight in generator_cpsat.py's
single requested-count-bonus usage site.
"""

from __future__ import annotations

from scheduler.backend.config import PhysicianConfig, seniority_multiplier

_CFG = {"seniority": {"per_year_rate": 0.02, "cap": 1.3}}


def test_no_hire_year_means_no_bonus():
    cfg = PhysicianConfig(id="Unknown", name="Unknown")
    assert seniority_multiplier(cfg, 2027, _CFG) == 1.0


def test_hired_this_year_means_no_bonus():
    cfg = PhysicianConfig(id="New", name="New", hire_year=2027)
    assert seniority_multiplier(cfg, 2027, _CFG) == 1.0


def test_future_hire_year_never_negative():
    """A hire_year after as_of_year (bad data) still floors at 0 years, not negative."""
    cfg = PhysicianConfig(id="Future", name="Future", hire_year=2030)
    assert seniority_multiplier(cfg, 2027, _CFG) == 1.0


def test_scales_linearly_with_years():
    cfg = PhysicianConfig(id="Mid", name="Mid", hire_year=2017)
    # 10 years * 2%/year = +20%
    assert seniority_multiplier(cfg, 2027, _CFG) == 1.2


def test_caps_at_configured_ceiling():
    cfg = PhysicianConfig(id="VerySenior", name="VerySenior", hire_year=1980)
    # 47 years * 2%/year would be +94% uncapped -- clamped to the 1.3 cap
    assert seniority_multiplier(cfg, 2027, _CFG) == 1.3


def test_falls_back_to_module_defaults_when_config_missing_seniority_block():
    cfg = PhysicianConfig(id="Senior", name="Senior", hire_year=2012)
    # 15 years * default 2%/year = +30%, exactly the default 1.3 cap
    assert seniority_multiplier(cfg, 2027, {}) == 1.3


def test_custom_rate_and_cap_from_config():
    cfg = PhysicianConfig(id="Mid", name="Mid", hire_year=2022)
    custom = {"seniority": {"per_year_rate": 0.10, "cap": 1.2}}
    # 5 years * 10%/year = +50% uncapped -- clamped to this config's 1.2 cap
    assert seniority_multiplier(cfg, 2027, custom) == 1.2

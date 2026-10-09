"""recent_gain_pct: the convergence signal behind the 'Converged' verdict."""

from scheduler.backend.generator_cpsat import recent_gain_pct


def test_gain_is_measured_against_the_best_value_at_the_window_start():
    # Real 60-min January run shape: 806,256 at 2022 s ... 807,734 at 3341 s, run ended at 3349 s.
    hist = [(100.0, 285_000.0), (2022.0, 806_256.0), (2740.0, 807_380.0), (3341.0, 807_734.0)]
    # Last 10 minutes start at 2749 s; best known then was 807,380.
    assert recent_gain_pct(hist, 3349.0, 600.0) == round((807_734 - 807_380) / 807_734 * 100, 3)   # ~0.044%
    # Last 25 minutes reach back before 2022 s -> compare with 285,000 (big gain).
    assert recent_gain_pct(hist, 3349.0, 1500.0) > 60


def test_window_before_any_solution_uses_the_first_value():
    hist = [(50.0, 1000.0), (80.0, 1010.0)]
    assert recent_gain_pct(hist, 90.0, 600.0) == round(10 / 1010 * 100, 3)
    assert recent_gain_pct([], 90.0, 600.0) is None

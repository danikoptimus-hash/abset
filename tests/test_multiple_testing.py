import numpy as np
import pytest
from statsmodels.stats.multitest import multipletests

from abkit.analysis import multiple_testing as mt


@pytest.fixture
def p_values():
    return [0.001, 0.02, 0.04, 0.03, 0.5, 0.7]


def test_bonferroni_matches_statsmodels(p_values):
    ours = mt.bonferroni(p_values)
    _, expected, _, _ = multipletests(p_values, method="bonferroni")
    assert ours == pytest.approx(expected.tolist())


def test_holm_matches_statsmodels(p_values):
    ours = mt.holm(p_values)
    _, expected, _, _ = multipletests(p_values, method="holm")
    assert ours == pytest.approx(expected.tolist())


def test_benjamini_hochberg_matches_statsmodels(p_values):
    ours = mt.benjamini_hochberg(p_values)
    _, expected, _, _ = multipletests(p_values, method="fdr_bh")
    assert ours == pytest.approx(expected.tolist())


def test_holm_matches_statsmodels_random():
    rng = np.random.default_rng(0)
    p_values = rng.uniform(0, 1, size=50).tolist()
    ours = mt.holm(p_values)
    _, expected, _, _ = multipletests(p_values, method="holm")
    assert ours == pytest.approx(expected.tolist())


def test_bh_matches_statsmodels_random():
    rng = np.random.default_rng(1)
    p_values = rng.uniform(0, 1, size=50).tolist()
    ours = mt.benjamini_hochberg(p_values)
    _, expected, _, _ = multipletests(p_values, method="fdr_bh")
    assert ours == pytest.approx(expected.tolist())


def test_adjust_p_values_empty_list():
    assert mt.adjust_p_values([], method="holm") == []


def test_adjust_p_values_unknown_method_raises():
    with pytest.raises(ValueError, match="Неизвестный метод"):
        mt.adjust_p_values([0.05], method="bogus")


def test_holm_never_decreases_relative_order_below_bonferroni_first():
    # наименьший p-value при holm умножается на m (как в bonferroni)
    p_values = [0.01, 0.02, 0.03]
    ours = mt.holm(p_values)
    assert ours[0] == pytest.approx(0.03)

import pytest

from abkit.viz.help_texts import (
    HELP_EXPANDER_LABEL,
    HELP_EXPANDER_LABEL_TABLE,
    get_help_text,
    get_warning,
    render_help_html,
)

_CHART_TYPES = [
    "forest",
    "distribution_continuous",
    "distribution_binary",
    "cumulative_lift",
    "segment_forest",
    "verdicts_table",
    "srm_table",
    "mde_table",
]


@pytest.mark.parametrize("chart_type", _CHART_TYPES)
def test_get_help_text_has_all_three_sections(chart_type):
    text = get_help_text(chart_type)
    assert "**Что показано**" in text
    assert "**Как читать**" in text
    assert "**Когда что-то не так**" in text
    # разделы разделены пустой строкой
    assert "\n\n" in text


def test_get_help_text_unknown_chart_type_raises():
    with pytest.raises(KeyError):
        get_help_text("no_such_chart")


def test_distribution_ratio_aliases_to_continuous():
    assert get_help_text("distribution_ratio") == get_help_text("distribution_continuous")


def test_di_abbreviation_expanded_on_first_mention():
    for chart_type in ("forest", "distribution_binary", "cumulative_lift", "segment_forest"):
        text = get_help_text(chart_type)
        assert "ДИ" in text
        assert "доверительный интервал" in text or "доверительные интервалы" in text


def test_cumulative_lift_and_segment_forest_have_persistent_warnings():
    assert get_warning("cumulative_lift") is not None
    assert "peeking" in get_warning("cumulative_lift")
    assert get_warning("segment_forest") is not None
    assert "exploratory" in get_warning("segment_forest")


def test_other_chart_types_have_no_persistent_warning():
    assert get_warning("forest") is None
    assert get_warning("distribution_continuous") is None
    assert get_warning("verdicts_table") is None


@pytest.mark.parametrize("chart_type", _CHART_TYPES)
def test_render_help_html_wraps_in_details_summary(chart_type):
    html = render_help_html(chart_type)
    assert html.startswith(f"<details><summary>{HELP_EXPANDER_LABEL}</summary>")
    assert html.endswith("</details>")
    assert "**" not in html  # markdown bold должен быть сконвертирован в <strong>
    assert "<strong>Что показано</strong>" in html


def test_render_help_html_table_uses_table_label():
    html = render_help_html("mde_table", table=True)
    assert HELP_EXPANDER_LABEL_TABLE in html


def test_render_help_html_escapes_html_special_chars():
    # ловим потенциальный XSS/поломку разметки, если кто-то допишет текст с <,>,&
    from abkit.viz import help_texts

    help_texts._HELP_TEXTS["_test_escaping"] = "**Что показано**\n\n<script>alert(1)</script> & co"
    try:
        html = render_help_html("_test_escaping")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
    finally:
        del help_texts._HELP_TEXTS["_test_escaping"]

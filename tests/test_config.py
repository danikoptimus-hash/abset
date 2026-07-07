import pytest
from pydantic import ValidationError

from abkit.config import DesignConfig, MetricConfig


def make_config(**overrides):
    defaults = dict(
        name="exp1",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")],
        mde=0.05,
    )
    defaults.update(overrides)
    return DesignConfig(**defaults)


def test_valid_config_roundtrip():
    config = make_config()
    dumped = config.model_dump(mode="json")
    restored = DesignConfig.model_validate(dumped)
    assert restored == config


def test_yaml_roundtrip(tmp_path):
    import yaml

    config = make_config(strata=["platform"], seed=42)
    path = tmp_path / "config.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config.model_dump(mode="json"), f, allow_unicode=True)

    with path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    restored = DesignConfig.model_validate(loaded)
    assert restored == config


def test_groups_must_sum_to_one():
    with pytest.raises(ValidationError, match="Сумма долей"):
        make_config(groups={"control": 0.5, "treatment": 0.6})


def test_cannot_specify_both_mde_and_sample_size():
    with pytest.raises(ValidationError, match="ровно одно"):
        make_config(mde=0.05, sample_size=1000)


def test_neither_mde_nor_sample_size_is_allowed():
    config = make_config(mde=None, sample_size=None)
    assert config.mde is None
    assert config.sample_size is None


def test_duplicate_metric_names_rejected():
    with pytest.raises(ValidationError, match="уникальны"):
        make_config(
            metrics=[
                MetricConfig(name="revenue", type="continuous"),
                MetricConfig(name="revenue", type="binary"),
            ]
        )


def test_ratio_metric_requires_num_den():
    with pytest.raises(ValidationError, match="num и den"):
        MetricConfig(name="conv_rate", type="ratio")


def test_ratio_metric_with_num_den_ok():
    metric = MetricConfig(name="conv_rate", type="ratio", num="conversions", den="sessions")
    assert metric.num == "conversions"
    assert metric.den == "sessions"


def test_empty_groups_rejected():
    with pytest.raises(ValidationError, match="groups не может быть пустым"):
        make_config(groups={})


def test_default_values():
    config = make_config()
    assert config.alpha == 0.05
    assert config.power == 0.8
    assert config.split_method == "stratified"
    assert config.isolation == "exclude"
    assert config.exclude_experiments == "all_active"
    assert config.strata == []

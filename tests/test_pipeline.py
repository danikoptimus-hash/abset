import pandas as pd
import pytest

from abkit.pipeline import MetricContext, Pipeline, PipelineError, Step


class DummyPreprocess(Step):
    stage = "preprocess"

    def apply(self, ctx: MetricContext) -> MetricContext:
        ctx.n_removed = {"control": 0, "treatment": 0}
        return ctx


class DummyVarianceReduction(Step):
    stage = "variance_reduction"

    def apply(self, ctx: MetricContext) -> MetricContext:
        ctx.variance_reduction = 0.3
        return ctx


class DummyTest(Step):
    stage = "test"

    def apply(self, ctx: MetricContext) -> MetricContext:
        from abkit.analysis.results import TestResult

        ctx.result = TestResult(
            metric=ctx.metric_name,
            method="Dummy",
            effect_abs=1.0,
            effect_rel=0.1,
            ci_abs=(0.5, 1.5),
            ci_rel=(0.05, 0.15),
            p_value=0.03,
            p_value_adjusted=None,
            n={"control": 100, "treatment": 100},
            n_removed=ctx.n_removed,
            variance_reduction=ctx.variance_reduction,
            warnings=ctx.warnings,
            is_designed_method=ctx.is_designed_method,
            treatment_group=ctx.treatment_name,
        )
        return ctx


class AnotherTest(Step):
    stage = "test"

    def apply(self, ctx: MetricContext) -> MetricContext:
        return ctx


def make_ctx() -> MetricContext:
    return MetricContext(
        metric_name="revenue",
        metric_type="continuous",
        control_name="control",
        treatment_name="treatment",
        values=pd.Series([1.0, 2.0, 3.0, 4.0]),
        group=pd.Series(["control", "control", "treatment", "treatment"]),
    )


def test_pipeline_valid_order_runs():
    pipeline = Pipeline([DummyPreprocess(), DummyVarianceReduction(), DummyTest()])
    ctx = pipeline.run(make_ctx())
    assert ctx.result is not None
    assert ctx.result.p_value == 0.03
    assert ctx.applied_steps == ["DummyPreprocess", "DummyVarianceReduction", "DummyTest"]


def test_pipeline_only_test_step_is_valid():
    pipeline = Pipeline([DummyTest()])
    ctx = pipeline.run(make_ctx())
    assert ctx.result is not None


def test_pipeline_requires_exactly_one_test_step_zero():
    with pytest.raises(PipelineError, match="ровно один test-шаг"):
        Pipeline([DummyPreprocess()])


def test_pipeline_requires_exactly_one_test_step_multiple():
    with pytest.raises(PipelineError, match="ровно один test-шаг"):
        Pipeline([DummyTest(), AnotherTest()])


def test_pipeline_rejects_wrong_stage_order():
    with pytest.raises(PipelineError, match="Нарушен порядок стадий"):
        Pipeline([DummyVarianceReduction(), DummyPreprocess(), DummyTest()])


def test_pipeline_rejects_test_before_preprocess():
    with pytest.raises(PipelineError, match="Нарушен порядок стадий"):
        Pipeline([DummyTest(), DummyPreprocess()])


def test_pipeline_method_name_concatenates_steps():
    pipeline = Pipeline([DummyPreprocess(), DummyTest()])
    assert pipeline.method_name == "DummyPreprocess + DummyTest"


def test_method_display_name_prefixes_upstream_steps():
    from abkit.pipeline import method_display_name

    pipeline = Pipeline([DummyPreprocess(), DummyVarianceReduction(), DummyTest()])
    ctx = pipeline.run(make_ctx())
    # applied_steps уже содержит все три к моменту вызова DummyTest.apply()
    assert method_display_name(ctx, "Own") == "DummyPreprocess + DummyVarianceReduction + Own"


def test_method_display_name_no_prefix_when_test_step_alone():
    from abkit.pipeline import method_display_name

    pipeline = Pipeline([DummyTest()])
    ctx = pipeline.run(make_ctx())
    assert method_display_name(ctx, "Own") == "Own"


def test_pipeline_rejects_naive_test_on_ratio_metric():
    pipeline = Pipeline([DummyTest()])
    ctx = make_ctx()
    ctx.metric_type = "ratio"
    with pytest.raises(PipelineError, match="DeltaMethodTTest"):
        pipeline.run(ctx)


def test_pipeline_allows_delta_method_ttest_on_ratio_metric():
    class DeltaMethodTTest(Step):
        stage = "test"

        def apply(self, ctx):
            return ctx

    pipeline = Pipeline([DeltaMethodTTest()])
    ctx = make_ctx()
    ctx.metric_type = "ratio"
    pipeline.run(ctx)  # не должно бросать


def test_pipeline_questionable_combination_warns():
    class CUPED(Step):
        stage = "variance_reduction"

        def apply(self, ctx):
            return ctx

    class MannWhitney(Step):
        stage = "test"

        def apply(self, ctx):
            return ctx

    pipeline = Pipeline([CUPED(), MannWhitney()])
    ctx = pipeline.run(make_ctx())
    assert any("методологически спорен" in w for w in ctx.warnings)

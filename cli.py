"""CLI abkit: design, analyze, validate, status, list."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import questionary
import typer
import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from abkit import PRODUCT_NAME, checks, storage
from abkit.config import DesignConfig, MetricConfig
from abkit.demo_data import generate_demo_design_data, generate_demo_post_data, make_demo_design_config
from abkit.experiment import DesignError, Experiment
from abkit.pipeline import PipelineError
from abkit.validation.simulation import run_aa, run_ab

if sys.platform == "win32":
    # rich использует win32-console API напрямую, который на некоторых
    # Windows-терминалах (не UTF-8 codepage) не может вывести кириллицу.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(add_completion=False, help=f"{PRODUCT_NAME} — A/B test design and analysis")
console = Console(legacy_windows=False)


def _load_data(path: str) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        console.print(f"[red]Error:[/red] file '{path}' not found")
        raise typer.Exit(code=1)
    suffix = file_path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(file_path)
    if suffix == ".csv":
        return pd.read_csv(file_path)
    console.print(f"[red]Error:[/red] unsupported file format '{suffix}' (need .csv or .parquet)")
    raise typer.Exit(code=1)


def _interactive_design_config(data: pd.DataFrame) -> DesignConfig:
    columns = list(data.columns)

    name = questionary.text("Experiment name:").ask()
    unit_col = questionary.select("Unit column (unit_col):", choices=columns).ask()

    console.print("Set the groups and their proportions (proportions must sum to 1).")
    groups: dict[str, float] = {}
    while True:
        prompt = f"Group name #{len(groups) + 1} (Enter to finish, minimum 2 groups):"
        group_name = questionary.text(prompt).ask()
        if not group_name:
            if len(groups) >= 2:
                break
            console.print("[yellow]At least 2 groups are required.[/yellow]")
            continue
        prop = questionary.text(f"Proportion of group '{group_name}':", default="0.5").ask()
        try:
            groups[group_name] = float(prop)
        except ValueError:
            console.print(f"[red]Error:[/red] '{prop}' is not a number")
            raise typer.Exit(code=1)

    console.print("Set the metrics (at least one).")
    metrics: list[MetricConfig] = []
    while True:
        prompt = f"Metric name #{len(metrics) + 1} (Enter to finish):"
        metric_name = questionary.text(prompt).ask()
        if not metric_name:
            if metrics:
                break
            console.print("[yellow]At least one metric is required.[/yellow]")
            continue
        metric_type = questionary.select("Metric type:", choices=["continuous", "binary", "ratio"]).ask()
        role = questionary.select("Metric role:", choices=["primary", "secondary"]).ask()
        kwargs: dict = dict(name=metric_name, type=metric_type, role=role)
        if metric_type == "ratio":
            kwargs["num"] = questionary.select("Numerator column:", choices=columns).ask()
            kwargs["den"] = questionary.select("Denominator column:", choices=columns).ask()
        elif questionary.confirm("Is there a pre-period covariate for CUPED?", default=False).ask():
            kwargs["pre_col"] = questionary.select("Pre-period column:", choices=columns).ask()
        try:
            metrics.append(MetricConfig(**kwargs))
        except ValidationError as e:
            console.print(f"[red]Error in metric '{metric_name}':[/red]\n{e}")
            raise typer.Exit(code=1)

    strata = questionary.checkbox("Strata (optional):", choices=columns).ask() or []

    size_mode = questionary.select(
        "How should the experiment size be determined?",
        choices=[
            "mde — set a target effect",
            "sample_size — set a sample size",
            "use all available data",
        ],
    ).ask()
    mde = None
    sample_size = None
    try:
        if size_mode.startswith("mde"):
            mde = float(questionary.text("Relative MDE (e.g. 0.05 = 5%):").ask())
        elif size_mode.startswith("sample_size"):
            sample_size = int(questionary.text("Total sample size:").ask())
    except ValueError:
        console.print("[red]Error:[/red] not a number")
        raise typer.Exit(code=1)

    split_method = questionary.select(
        "Split method:", choices=["stratified", "simple", "hash"], default="stratified"
    ).ask()
    isolation_mode = questionary.select(
        "Isolation from other active experiments:", choices=["exclude", "warn", "off"], default="exclude"
    ).ask()

    try:
        return DesignConfig(
            name=name,
            unit_col=unit_col,
            groups=groups,
            metrics=metrics,
            strata=strata,
            mde=mde,
            sample_size=sample_size,
            split_method=split_method,
            isolation=isolation_mode,
        )
    except ValidationError as e:
        console.print(f"[red]Error in config:[/red]\n{e}")
        raise typer.Exit(code=1)


def _print_design_summary(experiment: Experiment) -> None:
    console.print(f"[green]Experiment '{experiment.name}' designed.[/green]")
    console.print(f"Folder: {experiment.path}")

    group_table = Table(title="Group sizes")
    group_table.add_column("Group")
    group_table.add_column("Size")
    for group_name, size in experiment.report.group_sizes.items():
        group_table.add_row(group_name, str(size))
    console.print(group_table)

    power_table = Table(title="Power / MDE")
    power_table.add_column("Metric")
    power_table.add_column("MDE (rel.)")
    power_table.add_column("Group size")
    for metric_name, pr in experiment.report.power_results.items():
        power_table.add_row(
            metric_name,
            f"{pr.mde_rel:.2%}" if pr.mde_rel is not None else "-",
            f"{pr.sample_size_per_group:.0f}" if pr.sample_size_per_group is not None else "-",
        )
    console.print(power_table)

    if experiment.report.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for w in experiment.report.warnings:
            console.print(f"  - {w}")


@app.command()
def design(
    config: str = typer.Option(
        None, "--config", help="Path to a ready design.yaml (otherwise an interactive prompt)"
    ),
    data: str = typer.Option(None, "--data", help="Path to historical data (.csv/.parquet)"),
    save_config: str = typer.Option(None, "--save-config", help="Save the interactive answers to yaml"),
) -> None:
    """Design a new experiment."""
    experiments_dir = storage.get_experiments_dir()

    if config:
        config_path = Path(config)
        if not config_path.exists():
            console.print(f"[red]Error:[/red] config '{config}' not found")
            raise typer.Exit(code=1)
        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        try:
            design_config = DesignConfig.model_validate(raw)
        except ValidationError as e:
            console.print(f"[red]Error in config:[/red]\n{e}")
            raise typer.Exit(code=1)
        if not data:
            console.print("[red]Error:[/red] --data is required when using --config")
            raise typer.Exit(code=1)
        design_data = _load_data(data)
    else:
        if not data:
            data = questionary.path("Path to historical data (.csv/.parquet):").ask()
        design_data = _load_data(data)
        design_config = _interactive_design_config(design_data)
        if save_config:
            with open(save_config, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    design_config.model_dump(mode="json"), f, allow_unicode=True, sort_keys=False
                )
            console.print(f"Config saved to {save_config}")

    try:
        experiment = Experiment.design(design_config, design_data, experiments_dir=experiments_dir)
    except (DesignError, storage.StorageError) as e:
        console.print(f"[red]Design error:[/red] {e}")
        raise typer.Exit(code=1)

    _print_design_summary(experiment)


@app.command()
def analyze(
    name: str = typer.Argument(..., help="Experiment name"),
    data: str = typer.Option(..., "--data", help="Path to actual data (.csv/.parquet)"),
    compare: bool = typer.Option(False, "--compare", help="Compute alternative methods"),
    correction: str = typer.Option("holm", "--correction", help="Multiple-testing correction"),
    date_col: str = typer.Option(None, "--date-col", help="Date column for cumulative lift"),
) -> None:
    """Analyze an experiment using actual data."""
    experiments_dir = storage.get_experiments_dir()
    try:
        experiment = Experiment.load(name, experiments_dir=experiments_dir)
    except storage.StorageError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    analyze_data = _load_data(data)
    try:
        results = experiment.analyze(
            analyze_data, correction=correction, compare_methods=compare, date_col=date_col
        )
    except (checks.AnalysisError, DesignError, PipelineError, ValueError) as e:
        console.print(f"[red]Analysis error:[/red] {e}")
        raise typer.Exit(code=1)

    results.summary()
    report_path = results.report()
    console.print(f"Report: {report_path}")


@app.command()
def validate(
    name: str = typer.Argument(..., help="Experiment name"),
    data: str = typer.Option(..., "--data", help="Path to historical data for simulation"),
    n_sims: int = typer.Option(2000, "--n-sims", help="Number of simulations"),
    effect: float = typer.Option(None, "--effect", help="Relative effect for the A/B simulation"),
    compare: bool = typer.Option(False, "--compare", help="Include alternative methods in validation"),
) -> None:
    """Run A/A (and optionally A/B) simulations on design data."""
    experiments_dir = storage.get_experiments_dir()
    try:
        experiment = Experiment.load(name, experiments_dir=experiments_dir)
    except storage.StorageError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    sim_data = _load_data(data)

    console.print(f"Running A/A validation ({n_sims} simulations)...")
    try:
        aa_report = run_aa(sim_data, experiment.config, n_sims=n_sims, compare_methods=compare)
    except (checks.AnalysisError, KeyError, ValueError) as e:
        console.print(f"[red]Validation error:[/red] {e}")
        raise typer.Exit(code=1)
    aa_report.summary()

    if effect is not None:
        console.print(f"Running A/B validation (effect={effect:.2%})...")
        try:
            ab_report = run_ab(
                sim_data, experiment.config, n_sims=n_sims, effect=effect, compare_methods=compare
            )
        except (checks.AnalysisError, KeyError, ValueError) as e:
            console.print(f"[red]Validation error:[/red] {e}")
            raise typer.Exit(code=1)
        ab_report.summary()


@app.command()
def demo(
    n: int = typer.Option(5000, "--n", help="Size of the synthetic dataset"),
    effect: float = typer.Option(0.08, "--effect", help="Relative lift of revenue in the demo data"),
) -> None:
    """Generate synthetic data and run design -> analyze -> report to try out abkit."""
    experiments_dir = storage.get_experiments_dir()

    name = "demo"
    registry = storage.read_registry(experiments_dir)
    suffix = 1
    while name in registry:
        suffix += 1
        name = f"demo_{suffix}"

    console.print(f"Generating a synthetic dataset (n={n})...")
    design_data = generate_demo_design_data(n, seed=0)
    design_config = make_demo_design_config(name, n, seed=0)

    console.print("Designing the experiment...")
    experiment = Experiment.design(design_config, design_data, experiments_dir=experiments_dir)
    _print_design_summary(experiment)

    console.print("Generating actual data with a real effect and analyzing...")
    post_data = generate_demo_post_data(experiment.assignments, effect=effect, seed=1)

    results = experiment.analyze(post_data, compare_methods=True)
    results.summary()
    report_path = results.report()

    console.print(f"[green]Done![/green] Experiment: {name}")
    console.print(f"Verdict for revenue: {results.verdict('revenue', treatment_group='treatment')}")
    console.print(f"Design report: {experiment.path / 'design_report.html'}")
    console.print(f"Analysis report: {report_path}")


@app.command()
def status(
    name: str = typer.Argument(..., help="Experiment name"),
    new_status: str = typer.Argument(..., help="running|completed|archived"),
) -> None:
    """Change an experiment's status in the registry."""
    experiments_dir = storage.get_experiments_dir()
    try:
        storage.update_status(experiments_dir, name, new_status)
    except storage.StorageError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
    console.print(f"[green]Experiment '{name}' moved to status '{new_status}'.[/green]")


@app.command(name="list")
def list_experiments(
    active: bool = typer.Option(False, "--active", help="Show only active experiments"),
) -> None:
    """Show a table of registered experiments."""
    experiments_dir = storage.get_experiments_dir()
    registry = storage.list_experiments(experiments_dir, active_only=active)

    table = Table(title=f"{PRODUCT_NAME} experiments")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Created")
    table.add_column("Path")

    for name, entry in sorted(registry.items()):
        table.add_row(name, entry["status"], entry["created_at"], entry["path"])

    console.print(table)


if __name__ == "__main__":
    app()

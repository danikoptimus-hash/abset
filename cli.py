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

from abkit import checks, storage
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

app = typer.Typer(add_completion=False, help="abkit — дизайн и анализ A/B тестов")
console = Console(legacy_windows=False)


def _load_data(path: str) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        console.print(f"[red]Ошибка:[/red] файл '{path}' не найден")
        raise typer.Exit(code=1)
    suffix = file_path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(file_path)
    if suffix == ".csv":
        return pd.read_csv(file_path)
    console.print(f"[red]Ошибка:[/red] неподдерживаемый формат файла '{suffix}' (нужен .csv или .parquet)")
    raise typer.Exit(code=1)


def _interactive_design_config(data: pd.DataFrame) -> DesignConfig:
    columns = list(data.columns)

    name = questionary.text("Имя эксперимента:").ask()
    unit_col = questionary.select("Колонка юнита (unit_col):", choices=columns).ask()

    console.print("Задайте группы и их доли (сумма долей должна быть равна 1).")
    groups: dict[str, float] = {}
    while True:
        prompt = f"Имя группы #{len(groups) + 1} (Enter чтобы закончить, минимум 2 группы):"
        group_name = questionary.text(prompt).ask()
        if not group_name:
            if len(groups) >= 2:
                break
            console.print("[yellow]Нужно минимум 2 группы.[/yellow]")
            continue
        prop = questionary.text(f"Доля группы '{group_name}':", default="0.5").ask()
        try:
            groups[group_name] = float(prop)
        except ValueError:
            console.print(f"[red]Ошибка:[/red] '{prop}' не число")
            raise typer.Exit(code=1)

    console.print("Задайте метрики (минимум одна).")
    metrics: list[MetricConfig] = []
    while True:
        prompt = f"Имя метрики #{len(metrics) + 1} (Enter чтобы закончить):"
        metric_name = questionary.text(prompt).ask()
        if not metric_name:
            if metrics:
                break
            console.print("[yellow]Нужна хотя бы одна метрика.[/yellow]")
            continue
        metric_type = questionary.select("Тип метрики:", choices=["continuous", "binary", "ratio"]).ask()
        role = questionary.select("Роль метрики:", choices=["primary", "secondary"]).ask()
        kwargs: dict = dict(name=metric_name, type=metric_type, role=role)
        if metric_type == "ratio":
            kwargs["num"] = questionary.select("Колонка числителя:", choices=columns).ask()
            kwargs["den"] = questionary.select("Колонка знаменателя:", choices=columns).ask()
        elif questionary.confirm("Есть pre-period ковариата для CUPED?", default=False).ask():
            kwargs["pre_col"] = questionary.select("Колонка pre-period:", choices=columns).ask()
        try:
            metrics.append(MetricConfig(**kwargs))
        except ValidationError as e:
            console.print(f"[red]Ошибка в метрике '{metric_name}':[/red]\n{e}")
            raise typer.Exit(code=1)

    strata = questionary.checkbox("Страты (можно пропустить):", choices=columns).ask() or []

    size_mode = questionary.select(
        "Как задать размер эксперимента?",
        choices=[
            "mde — задать целевой эффект",
            "sample_size — задать размер выборки",
            "использовать все доступные данные",
        ],
    ).ask()
    mde = None
    sample_size = None
    try:
        if size_mode.startswith("mde"):
            mde = float(questionary.text("Относительный MDE (например 0.05 = 5%):").ask())
        elif size_mode.startswith("sample_size"):
            sample_size = int(questionary.text("Общий размер выборки:").ask())
    except ValueError:
        console.print("[red]Ошибка:[/red] введено не число")
        raise typer.Exit(code=1)

    split_method = questionary.select(
        "Метод сплита:", choices=["stratified", "simple", "hash"], default="stratified"
    ).ask()
    isolation_mode = questionary.select(
        "Изоляция от других активных экспериментов:", choices=["exclude", "warn", "off"], default="exclude"
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
        console.print(f"[red]Ошибка в конфиге:[/red]\n{e}")
        raise typer.Exit(code=1)


def _print_design_summary(experiment: Experiment) -> None:
    console.print(f"[green]Эксперимент '{experiment.name}' спроектирован.[/green]")
    console.print(f"Папка: {experiment.path}")

    group_table = Table(title="Размеры групп")
    group_table.add_column("Группа")
    group_table.add_column("Размер")
    for group_name, size in experiment.report.group_sizes.items():
        group_table.add_row(group_name, str(size))
    console.print(group_table)

    power_table = Table(title="Мощность / MDE")
    power_table.add_column("Метрика")
    power_table.add_column("MDE (отн.)")
    power_table.add_column("Размер группы")
    for metric_name, pr in experiment.report.power_results.items():
        power_table.add_row(
            metric_name,
            f"{pr.mde_rel:.2%}" if pr.mde_rel is not None else "-",
            f"{pr.sample_size_per_group:.0f}" if pr.sample_size_per_group is not None else "-",
        )
    console.print(power_table)

    if experiment.report.warnings:
        console.print("[yellow]Предупреждения:[/yellow]")
        for w in experiment.report.warnings:
            console.print(f"  - {w}")


@app.command()
def design(
    config: str = typer.Option(
        None, "--config", help="Путь к готовому design.yaml (иначе интерактивный опрос)"
    ),
    data: str = typer.Option(None, "--data", help="Путь к историческим данным (.csv/.parquet)"),
    save_config: str = typer.Option(None, "--save-config", help="Сохранить ответы опроса в yaml"),
) -> None:
    """Спроектировать новый эксперимент."""
    experiments_dir = storage.get_experiments_dir()

    if config:
        config_path = Path(config)
        if not config_path.exists():
            console.print(f"[red]Ошибка:[/red] конфиг '{config}' не найден")
            raise typer.Exit(code=1)
        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        try:
            design_config = DesignConfig.model_validate(raw)
        except ValidationError as e:
            console.print(f"[red]Ошибка в конфиге:[/red]\n{e}")
            raise typer.Exit(code=1)
        if not data:
            console.print("[red]Ошибка:[/red] нужен --data при использовании --config")
            raise typer.Exit(code=1)
        design_data = _load_data(data)
    else:
        if not data:
            data = questionary.path("Путь к историческим данным (.csv/.parquet):").ask()
        design_data = _load_data(data)
        design_config = _interactive_design_config(design_data)
        if save_config:
            with open(save_config, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    design_config.model_dump(mode="json"), f, allow_unicode=True, sort_keys=False
                )
            console.print(f"Конфиг сохранен в {save_config}")

    try:
        experiment = Experiment.design(design_config, design_data, experiments_dir=experiments_dir)
    except (DesignError, storage.StorageError) as e:
        console.print(f"[red]Ошибка дизайна:[/red] {e}")
        raise typer.Exit(code=1)

    _print_design_summary(experiment)


@app.command()
def analyze(
    name: str = typer.Argument(..., help="Имя эксперимента"),
    data: str = typer.Option(..., "--data", help="Путь к фактическим данным (.csv/.parquet)"),
    compare: bool = typer.Option(False, "--compare", help="Посчитать альтернативные методы"),
    correction: str = typer.Option("holm", "--correction", help="Поправка на множественность"),
    date_col: str = typer.Option(None, "--date-col", help="Колонка даты для кумулятивного лифта"),
) -> None:
    """Проанализировать эксперимент по фактическим данным."""
    experiments_dir = storage.get_experiments_dir()
    try:
        experiment = Experiment.load(name, experiments_dir=experiments_dir)
    except storage.StorageError as e:
        console.print(f"[red]Ошибка:[/red] {e}")
        raise typer.Exit(code=1)

    analyze_data = _load_data(data)
    try:
        results = experiment.analyze(
            analyze_data, correction=correction, compare_methods=compare, date_col=date_col
        )
    except (checks.AnalysisError, DesignError, PipelineError, ValueError) as e:
        console.print(f"[red]Ошибка анализа:[/red] {e}")
        raise typer.Exit(code=1)

    results.summary()
    report_path = results.report()
    console.print(f"Отчет: {report_path}")


@app.command()
def validate(
    name: str = typer.Argument(..., help="Имя эксперимента"),
    data: str = typer.Option(..., "--data", help="Путь к историческим данным для симуляции"),
    n_sims: int = typer.Option(2000, "--n-sims", help="Число симуляций"),
    effect: float = typer.Option(None, "--effect", help="Относительный эффект для A/B симуляции"),
    compare: bool = typer.Option(False, "--compare", help="Включить альтернативные методы в валидацию"),
) -> None:
    """Провести A/A (и опционально A/B) симуляции на данных дизайна."""
    experiments_dir = storage.get_experiments_dir()
    try:
        experiment = Experiment.load(name, experiments_dir=experiments_dir)
    except storage.StorageError as e:
        console.print(f"[red]Ошибка:[/red] {e}")
        raise typer.Exit(code=1)

    sim_data = _load_data(data)

    console.print(f"Запуск A/A валидации ({n_sims} симуляций)...")
    try:
        aa_report = run_aa(sim_data, experiment.config, n_sims=n_sims, compare_methods=compare)
    except (checks.AnalysisError, KeyError, ValueError) as e:
        console.print(f"[red]Ошибка валидации:[/red] {e}")
        raise typer.Exit(code=1)
    aa_report.summary()

    if effect is not None:
        console.print(f"Запуск A/B валидации (эффект={effect:.2%})...")
        try:
            ab_report = run_ab(
                sim_data, experiment.config, n_sims=n_sims, effect=effect, compare_methods=compare
            )
        except (checks.AnalysisError, KeyError, ValueError) as e:
            console.print(f"[red]Ошибка валидации:[/red] {e}")
            raise typer.Exit(code=1)
        ab_report.summary()


@app.command()
def demo(
    n: int = typer.Option(5000, "--n", help="Размер синтетического датасета"),
    effect: float = typer.Option(0.08, "--effect", help="Относительный лифт revenue в demo-данных"),
) -> None:
    """Сгенерировать синтетические данные и прогнать design -> analyze -> report для знакомства с abkit."""
    experiments_dir = storage.get_experiments_dir()

    name = "demo"
    registry = storage.read_registry(experiments_dir)
    suffix = 1
    while name in registry:
        suffix += 1
        name = f"demo_{suffix}"

    console.print(f"Генерирую синтетический датасет (n={n})...")
    design_data = generate_demo_design_data(n, seed=0)
    design_config = make_demo_design_config(name, n, seed=0)

    console.print("Проектирую эксперимент...")
    experiment = Experiment.design(design_config, design_data, experiments_dir=experiments_dir)
    _print_design_summary(experiment)

    console.print("Генерирую фактические данные с реальным эффектом и анализирую...")
    post_data = generate_demo_post_data(experiment.assignments, effect=effect, seed=1)

    results = experiment.analyze(post_data, compare_methods=True)
    results.summary()
    report_path = results.report()

    console.print(f"[green]Готово![/green] Эксперимент: {name}")
    console.print(f"Вердикт по revenue: {results.verdict('revenue', treatment_group='treatment')}")
    console.print(f"Отчет по дизайну: {experiment.path / 'design_report.html'}")
    console.print(f"Отчет по анализу: {report_path}")


@app.command()
def status(
    name: str = typer.Argument(..., help="Имя эксперимента"),
    new_status: str = typer.Argument(..., help="running|completed|archived"),
) -> None:
    """Изменить статус эксперимента в реестре."""
    experiments_dir = storage.get_experiments_dir()
    try:
        storage.update_status(experiments_dir, name, new_status)
    except storage.StorageError as e:
        console.print(f"[red]Ошибка:[/red] {e}")
        raise typer.Exit(code=1)
    console.print(f"[green]Эксперимент '{name}' переведен в статус '{new_status}'.[/green]")


@app.command(name="list")
def list_experiments(
    active: bool = typer.Option(False, "--active", help="Показать только активные эксперименты"),
) -> None:
    """Показать таблицу зарегистрированных экспериментов."""
    experiments_dir = storage.get_experiments_dir()
    registry = storage.list_experiments(experiments_dir, active_only=active)

    table = Table(title="Эксперименты abkit")
    table.add_column("Имя")
    table.add_column("Статус")
    table.add_column("Создан")
    table.add_column("Путь")

    for name, entry in sorted(registry.items()):
        table.add_row(name, entry["status"], entry["created_at"], entry["path"])

    console.print(table)


if __name__ == "__main__":
    app()

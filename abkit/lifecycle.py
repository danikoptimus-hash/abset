"""Авто-завершение теста по плановой дате окончания (item B3).

`experiments.planned_end_date` (миграция 0023) — календарная ДАТА, а не момент.
Отсюда единственный содержательный вопрос этого модуля: когда именно
"наступила" плановая дата. Ответ — в КОНЦЕ этого дня, а не в его начале:
пользователь, поставивший 20-е, имеет в виду "тест идет по 20-е включительно",
и авто-завершение в 00:00 20-го отрезало бы целый день сбора данных, который он
рассчитывал получить. Поэтому cutoff = "вчерашняя дата по UTC": тест с
planned_end_date == 20-е авто-завершается первым проходом после полуночи 21-го.

Два входа, одна и та же чистая логика (никаких расхождений между ними):
  - периодический sweep в фоновом потоке (abkit/monitoring.py::
    MonitoringCollector — тот же существующий in-process планировщик, что
    снимает мониторинг и гоняет retention; отдельного сервиса/треда под это
    не заводится);
  - ленивая проверка при открытии страницы теста (backend/routers/
    experiments.py) — чтобы UI никогда не показывал "running" на тесте,
    у которого плановая дата уже прошла, даже если тик планировщика еще не
    случился.

Побочных эффектов, кроме самого перехода + записи в audit_log, нет — уведомления
осознанно вне скоупа.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import structlog

log = structlog.get_logger(__name__)

# Как часто фоновый поток проверяет "не пора ли кого-то завершить" (B3: «e.g.
# every 10 min»). Не настраивается через env: величина ничего не стоит и ни на
# что, кроме задержки перехода, не влияет — а лишняя ручка в OPERATIONS.md,
# которую некому крутить, только запутает. Ленивая проверка при открытии
# страницы и так закрывает случай "нужно прямо сейчас".
AUTO_COMPLETE_INTERVAL_SECONDS = 10 * 60

AUTO_COMPLETE_ACTION = "experiment.auto_completed"
AUTO_COMPLETE_REASON = "auto-completed: planned end date reached"


def auto_completion_cutoff(now: datetime | None = None) -> date:
    """Максимальная planned_end_date, которую уже пора завершать.

    Возвращает ВЧЕРАШНЮЮ дату по UTC — см. модульный docstring: день,
    указанный пользователем, входит в тест целиком.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment.astimezone(timezone.utc) - timedelta(days=1)).date()


def planned_end_reached(planned_end_date: date | None, now: datetime | None = None) -> bool:
    """Прошла ли плановая дата окончания. None (даты нет) — всегда False."""
    if planned_end_date is None:
        return False
    return planned_end_date <= auto_completion_cutoff(now)


def _complete(name: str, experiment_id: str) -> None:
    """Сам переход + audit-запись. Пользователя нет (это система), поэтому
    user_id/user_email в audit_log остаются NULL — фронт рисует такие строки
    как "system" (frontend/src/pages/experiment/HistorySection.tsx). Отдельного
    служебного пользователя ради этого не заводим: "никто из людей этого не
    делал" честнее, чем выдуманный аккаунт."""
    from abkit.db.repositories import AuditRepo, ExperimentRepo

    ExperimentRepo().update_status(name, "completed")
    AuditRepo().log(
        action=AUTO_COMPLETE_ACTION,
        object_type="experiment",
        object_id=experiment_id,
        object_name=name,
        details={"from": "running", "to": "completed", "reason": AUTO_COMPLETE_REASON},
    )
    log.info("lifecycle.auto_completed", experiment=name)


def auto_complete_due_experiments(now: datetime | None = None) -> list[str]:
    """Периодический проход: переводит в 'completed' все running-тесты, чья
    плановая дата уже прошла. Возвращает имена переведенных.

    "Ровно один раз" обеспечено самим гейтом status == 'running' в запросе
    (см. ExperimentRepo.list_due_for_auto_completion) — после перехода тест
    больше не попадает в выборку. Отдельная колонка "уже авто-завершен" не
    нужна и была бы вредна: ручной возврат в running (это разрешенный переход)
    должен снова включать авто-завершение, а не остаться заблокированным
    навсегда.

    Падение на одном тесте не должно ронять весь проход — остальные все равно
    надо завершить.
    """
    from abkit.db.repositories import ExperimentRepo

    due = ExperimentRepo().list_due_for_auto_completion(auto_completion_cutoff(now))
    completed: list[str] = []
    for exp in due:
        try:
            _complete(exp.name, str(exp.id))
            completed.append(exp.name)
        except Exception:
            log.error("lifecycle.auto_complete_failed", experiment=exp.name, exc_info=True)
    return completed


def auto_complete_if_due(exp, now: datetime | None = None) -> bool:
    """Ленивая проверка ОДНОГО теста (открытие страницы). True — перевели.

    Принимает уже прочитанную строку эксперимента, а не имя: вызывающий
    (GET /experiments/{name}) ее в любом случае только что достал, и второй
    запрос в БД на каждое открытие страницы ради этой проверки был бы платой
    ни за что. Условие полностью совпадает с sweep'ом выше.
    """
    if exp.status != "running" or not planned_end_reached(exp.planned_end_date, now):
        return False
    try:
        _complete(exp.name, str(exp.id))
    except Exception:
        log.error("lifecycle.auto_complete_failed", experiment=exp.name, exc_info=True)
        return False
    return True

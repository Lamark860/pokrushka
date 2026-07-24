"""Планировщик фоновых задач.

Запуск: `python3 -m app.scheduler.runner`

Две задачи:
  • раз в сутки — добор UTM из TGTrack (у сервиса данные о подписке живут 30 дней,
    поэтому опрашивать нужно регулярно);
  • раз в неделю — проверка свежести статистики: если Артур давно не приносил цифры,
    система сама об этом скажет.

Расписание сдвинуто на «неудобные» минуты, чтобы задачи не стартовали ровно в час.
Каждый прогон пишется в `job_runs` — его видно в интерфейсе.
"""
import logging

from app.config import get_settings
from app.db import SessionLocal
from app.services.reminders import run_reminder
from app.services.tgtrack_sync import run_sync

log = logging.getLogger(__name__)


def job_tgtrack_sync() -> None:
    settings = get_settings()
    if not settings.tgtrack_tg_api_key and not settings.tgtrack_max_api_key:
        log.info("Ключей TGTrack нет — пропускаю добор меток")
        return
    with SessionLocal() as db:
        result = run_sync(db, settings=settings)
    log.info("TGTrack: %s", result.as_log())


def job_metrics_reminder() -> None:
    with SessionLocal() as db:
        stale = run_reminder(db)
    if stale:
        log.info("Напоминание: %s", "; ".join(item.as_text() for item in stale))
    else:
        log.info("Статистика свежая, напоминать не о чем")


def build_scheduler():
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone="Europe/Moscow")
    scheduler.add_job(
        job_tgtrack_sync, CronTrigger(hour=4, minute=17), id="tgtrack-sync",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_metrics_reminder, CronTrigger(day_of_week="mon", hour=10, minute=23),
        id="metrics-reminder", max_instances=1, coalesce=True,
    )
    return scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    scheduler = build_scheduler()
    log.info("Планировщик запущен: %s", ", ".join(job.id for job in scheduler.get_jobs()))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Останавливаюсь")


if __name__ == "__main__":
    main()

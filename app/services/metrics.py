"""Импорт метрик площадок и работа со срезами.

Артур публикует статьи руками, поэтому и статистику приносит сам: выгрузкой из Студии
Дзена или вводом с экрана. Headless-парсинг личных кабинетов сознательно отложен —
Дзен-аккаунт это Яндекс-аккаунт с почтой и монетизацией, рисковать им ради цифр,
которые можно выгрузить в один клик, незачем.

Каждый импорт кладёт **срез за день** (`metrics_snapshots`), а не перезаписывает единственную
строку: только так видно динамику и приросты между неделями.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tables import NUMERIC_FIELDS, Table, parse_date, parse_number, parse_ratio
from app.models.content import Article
from app.models.tracking import METRICS_SOURCE_IMPORT, METRICS_SOURCE_MANUAL, MetricsSnapshot

log = logging.getLogger(__name__)

MATCH_URL = "url"
MATCH_TITLE = "title"

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES = re.compile(r"\s+")


@dataclass
class ImportReport:
    total: int = 0
    created: int = 0
    updated: int = 0
    matched_by_url: int = 0
    matched_by_title: int = 0
    unmatched: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def matched(self) -> int:
        return self.matched_by_url + self.matched_by_title

    def as_log(self) -> str:
        return (
            f"строк {self.total}, сопоставлено {self.matched} "
            f"(по ссылке {self.matched_by_url}, по заголовку {self.matched_by_title}), "
            f"создано {self.created}, обновлено {self.updated}"
        )


def normalize_title(value: str) -> str:
    return _SPACES.sub(" ", _PUNCT.sub(" ", (value or "").strip().lower())).strip()


def normalize_url(value: str) -> str:
    """Схема и метки не важны: у Дзена в выгрузке ссылка почти наверняка без UTM."""
    text = (value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parts = urlparse(text)
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    return f"{host}{path}"


def _article_index(db: Session, project_id: int) -> tuple[dict[str, Article], dict[str, Article]]:
    articles = db.scalars(select(Article).where(Article.project_id == project_id)).all()
    by_url = {
        normalize_url(a.published_url): a for a in articles if a.published_url
    }
    by_title = {normalize_title(a.title): a for a in articles if a.title}
    return by_url, by_title


def _day_start(value: date | datetime) -> datetime:
    """Срез привязан к дню: два импорта за один день — это один срез, а не два."""
    if isinstance(value, datetime):
        value = value.date()
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _values_from_row(row: dict[str, str], mapping: dict[str, str]) -> dict[str, int]:
    values: dict[str, int] = {}
    for field_name in NUMERIC_FIELDS:
        header = mapping.get(field_name)
        if header is None:
            continue
        number = parse_number(row.get(header))
        if number is not None:
            values[field_name] = max(number, 0)

    # Дочитывания могут прийти только процентом — считаем их от показов
    if "reads" not in values and mapping.get("read_ratio") and values.get("views"):
        ratio = parse_ratio(row.get(mapping["read_ratio"]))
        if ratio is not None:
            values["reads"] = int(round(values["views"] * ratio / 100))
    return values


def upsert_snapshot(
    db: Session,
    article: Article,
    values: dict[str, int],
    *,
    collected_at: datetime,
    source: str,
) -> bool:
    """Записать срез. True — создан новый, False — обновлён существующий за тот же день."""
    existing = db.scalar(
        select(MetricsSnapshot).where(
            MetricsSnapshot.article_id == article.id,
            MetricsSnapshot.collected_at == collected_at,
            MetricsSnapshot.source == source,
        )
    )
    target = existing or MetricsSnapshot(
        tenant_id=article.tenant_id,
        article_id=article.id,
        collected_at=collected_at,
        source=source,
    )
    for name, number in values.items():
        setattr(target, name, number)
    if existing is None:
        db.add(target)
    return existing is None


def import_metrics(
    db: Session,
    project_id: int,
    table: Table,
    *,
    mapping: dict[str, str] | None = None,
    collected_at: date | datetime | None = None,
    source: str = METRICS_SOURCE_IMPORT,
    dry_run: bool = False,
) -> ImportReport:
    """Разложить выгрузку по статьям проекта.

    `dry_run` показывает, что получится, ничего не записывая: сначала смотрим отчёт,
    потом применяем.
    """
    mapping = mapping or table.mapping
    report = ImportReport(dry_run=dry_run)

    if not any(mapping.get(name) for name in NUMERIC_FIELDS) and not mapping.get("read_ratio"):
        report.warnings.append(
            "В файле не найдено ни одной колонки с цифрами — сопоставьте колонки вручную"
        )
        return report
    if not mapping.get("url") and not mapping.get("title"):
        report.warnings.append(
            "Не найдено ни ссылки, ни заголовка — по чему сопоставлять статьи, непонятно"
        )
        return report

    by_url, by_title = _article_index(db, project_id)
    default_day = _day_start(collected_at or datetime.now(timezone.utc))

    for row in table.rows:
        report.total += 1
        raw_url = row.get(mapping.get("url", ""), "")
        raw_title = row.get(mapping.get("title", ""), "")

        article, matched_by = None, None
        if raw_url:
            article = by_url.get(normalize_url(raw_url))
            matched_by = MATCH_URL if article else None
        if article is None and raw_title:
            article = by_title.get(normalize_title(raw_title))
            matched_by = MATCH_TITLE if article else None

        if article is None:
            report.unmatched.append({"title": raw_title, "url": raw_url})
            continue

        values = _values_from_row(row, mapping)
        if not values:
            report.unmatched.append(
                {"title": raw_title or article.title, "url": raw_url, "reason": "нет цифр"}
            )
            continue

        if matched_by == MATCH_URL:
            report.matched_by_url += 1
        else:
            report.matched_by_title += 1

        day = default_day
        if mapping.get("collected_at"):
            parsed = parse_date(row.get(mapping["collected_at"]))
            if parsed is not None:
                day = _day_start(parsed)

        if dry_run:
            existing = db.scalar(
                select(MetricsSnapshot.id).where(
                    MetricsSnapshot.article_id == article.id,
                    MetricsSnapshot.collected_at == day,
                    MetricsSnapshot.source == source,
                )
            )
            report.updated += 1 if existing else 0
            report.created += 0 if existing else 1
            continue

        if upsert_snapshot(db, article, values, collected_at=day, source=source):
            report.created += 1
        else:
            report.updated += 1

    if dry_run:
        db.rollback()
    else:
        db.commit()

    if report.unmatched:
        report.warnings.append(
            f"Не удалось сопоставить строк: {len(report.unmatched)}. "
            "Обычно помогает проставить у статьи ссылку на публикацию."
        )
    return report


def save_manual(
    db: Session,
    article: Article,
    values: dict[str, int],
    *,
    collected_at: date | datetime | None = None,
) -> bool:
    created = upsert_snapshot(
        db,
        article,
        values,
        collected_at=_day_start(collected_at or datetime.now(timezone.utc)),
        source=METRICS_SOURCE_MANUAL,
    )
    db.commit()
    return created


# ------------------------------------------------------------------ чтение срезов


@dataclass
class MetricsView:
    """Последний срез статьи и прирост к предыдущему."""

    latest: MetricsSnapshot | None = None
    previous: MetricsSnapshot | None = None

    @property
    def views(self) -> int:
        return self.latest.views if self.latest else 0

    @property
    def reads(self) -> int:
        return self.latest.reads if self.latest else 0

    @property
    def read_ratio(self) -> float | None:
        if not self.latest or not self.latest.views:
            return None
        return round(self.latest.reads / self.latest.views * 100, 1)

    def delta(self, field_name: str) -> int | None:
        if self.latest is None or self.previous is None:
            return None
        return getattr(self.latest, field_name) - getattr(self.previous, field_name)


def metrics_for(db: Session, article_ids: list[int]) -> dict[int, MetricsView]:
    """По каждой статье — последний срез и предыдущий (для прироста)."""
    if not article_ids:
        return {}

    snapshots = db.scalars(
        select(MetricsSnapshot)
        .where(MetricsSnapshot.article_id.in_(article_ids))
        .order_by(MetricsSnapshot.article_id, MetricsSnapshot.collected_at.desc())
    ).all()

    result: dict[int, MetricsView] = {}
    for snapshot in snapshots:
        view = result.setdefault(snapshot.article_id, MetricsView())
        if view.latest is None:
            view.latest = snapshot
        elif view.previous is None:
            view.previous = snapshot
    return result

"""Воронка, конверсии и «что зашло лучше».

Считаем ровно ту воронку, которую Артур описывал в правках:
показы → дочитывания → переходы → подписки → заявки → продажи.

Важная тонкость: показы и дочитывания берутся из **последнего среза** каждой статьи, а не
суммой всех срезов — иначе одна и та же статья, посчитанная за три недели, утроит показы.
"""
import math
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.content import Article, TrackingLink
from app.models.project import Example, Project
from app.models.tracking import STAGE_SALE, Deal, Subscriber
from app.services.metrics import metrics_for

# Во что превращается каждая ступень при подсчёте «очков» статьи.
# Веса подобраны так, чтобы деньги весили больше внимания: 1 продажа ≈ 3000 дочитываний.
# Артур может поспорить — тогда правим здесь, формула одна на весь проект.
SCORE_WEIGHTS = {"reads": 0.01, "subscribers": 3.0, "leads": 10.0, "sales": 30.0}

CTA_MARKER = "## Что дальше"


# Ориентиры конверсии между ступенями, ниже которых шаг считается узким местом.
# Единого порога быть не может: с показов в дочитывания уходят десятки процентов,
# а с дочитываний в переходы — единицы, и это нормально.
BENCHMARKS = {
    "views": 25.0,       # показы → дочитывания
    "reads": 2.0,        # дочитывания → переходы
    "clicks": 20.0,      # переходы → подписки
    "subscribers": 3.0,  # подписки → заявки
    "leads": 15.0,       # заявки → продажи
}


@dataclass
class Stage:
    key: str
    title: str
    note: str
    count: int
    conversion: float | None = None  # % перехода на следующую ступень
    # Сколько людей отвалилось при переходе на следующий шаг
    lost: int = 0

    @property
    def benchmark(self) -> float | None:
        return BENCHMARKS.get(self.key)

    @property
    def is_bottleneck(self) -> bool:
        """Шаг, на котором теряется больше, чем ожидаемо: с него и надо начинать чинить."""
        if self.conversion is None or not self.count:
            return False
        benchmark = self.benchmark
        return benchmark is not None and self.conversion < benchmark


@dataclass
class Segment:
    """Ступень воронки: геометрия считается здесь, шаблон только рисует."""

    stage: Stage
    points: str
    label_y: float
    width_top: float
    width_bottom: float
    y_top: float = 0.0
    y_bottom: float = 0.0


@dataclass
class FunnelShape:
    """Цельный контур воронки плюс линии-границы между ступенями.

    Цельный силуэт вместо набора отдельных трапеций: так это читается как воронка,
    а не как стопка кирпичей, и не требует кричащей заливки каждой ступени.
    """

    segments: list[Segment] = field(default_factory=list)
    outline: str = ""
    height: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.segments)

    def __len__(self) -> int:
        return len(self.segments)


@dataclass
class Funnel:
    stages: list[Stage] = field(default_factory=list)
    revenue: int = 0
    articles: int = 0
    published: int = 0

    @property
    def max_count(self) -> int:
        return max((stage.count for stage in self.stages), default=0) or 1

    def shape(self, *, height: float = 46.0, min_width: float = 14.0) -> FunnelShape:
        """Геометрия воронки: ширина ступени по логарифму числа людей.

        Прямая пропорция здесь не работает. Разброс достигает пяти порядков
        (24 400 показов против одной продажи), и при точной пропорции всё, что ниже
        переходов, схлопывается в неразличимую нить — форма перестаёт читаться как
        воронка. Логарифм сохраняет главное — монотонное сужение и то, на каком шаге
        обрыв, — оставляя нижние ступени видимыми. Точные числа и проценты стоят
        рядом со ступенями, так что сглаженная ширина никого не вводит в заблуждение.
        """
        top = self.stages[0].count if self.stages else 0
        if not top:
            return FunnelShape()

        scale = math.log1p(top)
        widths = [
            max(min_width, round(math.log1p(stage.count) / scale * 100, 2)) if scale else min_width
            for stage in self.stages
        ]
        segments: list[Segment] = []
        for i, stage in enumerate(self.stages):
            w_top = widths[i]
            w_bottom = widths[i + 1] if i + 1 < len(widths) else widths[i]
            y0, y1 = i * height, (i + 1) * height
            left_top, right_top = 50 - w_top / 2, 50 + w_top / 2
            left_bottom, right_bottom = 50 - w_bottom / 2, 50 + w_bottom / 2
            segments.append(
                Segment(
                    stage=stage,
                    points=(
                        f"{left_top},{y0} {right_top},{y0} "
                        f"{right_bottom},{y1} {left_bottom},{y1}"
                    ),
                    label_y=y0 + height / 2,
                    width_top=round(w_top, 2),
                    width_bottom=round(w_bottom, 2),
                    y_top=y0,
                    y_bottom=y1,
                )
            )

        # Контур целиком: вниз по левому краю, потом вверх по правому
        total = len(self.stages) * height
        left = " ".join(f"L {50 - w / 2:.2f} {i * height:.1f}" for i, w in enumerate(widths))
        last_left = f"L {50 - widths[-1] / 2:.2f} {total:.1f}"
        last_right = f"L {50 + widths[-1] / 2:.2f} {total:.1f}"
        right = " ".join(
            f"L {50 + w / 2:.2f} {i * height:.1f}" for i, w in reversed(list(enumerate(widths)))
        )
        outline = (
            f"M {50 - widths[0] / 2:.2f} 0 {left} {last_left} {last_right} {right} Z"
        )
        return FunnelShape(segments=segments, outline=outline, height=total)

    def get(self, key: str) -> Stage | None:
        return next((stage for stage in self.stages if stage.key == key), None)

    @property
    def ctr(self) -> float | None:
        """Переходы к показам — та самая «CTR статьи» из правок Артура."""
        views, clicks = self.get("views"), self.get("clicks")
        if not views or not views.count:
            return None
        return round(clicks.count / views.count * 100, 2)

    @property
    def revenue_per_article(self) -> int:
        return round(self.revenue / self.published) if self.published else 0


@dataclass
class ArticleStats:
    article: Article
    views: int = 0
    reads: int = 0
    clicks: int = 0
    subscribers: int = 0
    leads: int = 0
    sales: int = 0
    revenue: int = 0

    @property
    def score(self) -> float:
        return round(
            self.reads * SCORE_WEIGHTS["reads"]
            + self.subscribers * SCORE_WEIGHTS["subscribers"]
            + self.leads * SCORE_WEIGHTS["leads"]
            + self.sales * SCORE_WEIGHTS["sales"],
            1,
        )

    @property
    def read_ratio(self) -> float | None:
        return round(self.reads / self.views * 100, 1) if self.views else None

    @property
    def sub_conversion(self) -> float | None:
        """Подписки на дочитывание — главный показатель качества текста.

        Два знака после запятой не прихоть: это доли процента (пара подписок на тысячи
        дочитываний), и при округлении до одного знака все статьи выглядят как «0.0%».
        """
        return round(self.subscribers / self.reads * 100, 2) if self.reads else None


@dataclass
class PlatformStats:
    key: str
    title: str
    articles: int = 0
    views: int = 0
    reads: int = 0
    clicks: int = 0
    subscribers: int = 0

    @property
    def read_ratio(self) -> float | None:
        return round(self.reads / self.views * 100, 1) if self.views else None


def _counts(db: Session, project: Project) -> tuple[dict[int, int], dict[int, int], dict[int, tuple[int, int, int]]]:
    clicks = dict(
        db.execute(
            select(TrackingLink.article_id, func.sum(TrackingLink.clicks))
            .join(Article, Article.id == TrackingLink.article_id)
            .where(Article.project_id == project.id)
            .group_by(TrackingLink.article_id)
        ).all()
    )
    subscribers = dict(
        db.execute(
            select(Subscriber.article_id, func.count(Subscriber.id))
            .where(Subscriber.project_id == project.id, Subscriber.article_id.isnot(None))
            .group_by(Subscriber.article_id)
        ).all()
    )
    deals: dict[int, tuple[int, int, int]] = {}
    for article_id, stage, count, amount in db.execute(
        select(Deal.article_id, Deal.stage, func.count(Deal.id), func.coalesce(func.sum(Deal.amount), 0))
        .where(Deal.project_id == project.id, Deal.article_id.isnot(None))
        .group_by(Deal.article_id, Deal.stage)
    ).all():
        leads, sales, revenue = deals.get(article_id, (0, 0, 0))
        if stage == STAGE_SALE:
            deals[article_id] = (leads, sales + count, revenue + int(amount))
        else:
            deals[article_id] = (leads + count, sales, revenue)
    return clicks, subscribers, deals


def article_stats(db: Session, project: Project) -> list[ArticleStats]:
    articles = list(
        db.scalars(select(Article).where(Article.project_id == project.id).order_by(Article.id))
    )
    if not articles:
        return []

    views = metrics_for(db, [a.id for a in articles])
    clicks, subscribers, deals = _counts(db, project)

    stats: list[ArticleStats] = []
    for article in articles:
        metric = views.get(article.id)
        leads, sales, revenue = deals.get(article.id, (0, 0, 0))
        stats.append(
            ArticleStats(
                article=article,
                views=metric.views if metric else 0,
                reads=metric.reads if metric else 0,
                clicks=clicks.get(article.id, 0),
                subscribers=subscribers.get(article.id, 0),
                leads=leads,
                sales=sales,
                revenue=revenue,
            )
        )
    return stats


def funnel(db: Session, project: Project, stats: list[ArticleStats] | None = None) -> Funnel:
    stats = stats if stats is not None else article_stats(db, project)

    total_subscribers = db.scalar(
        select(func.count(Subscriber.id)).where(Subscriber.project_id == project.id)
    ) or 0
    leads_total, sales_total, revenue_total = 0, 0, 0
    for stage, count, amount in db.execute(
        select(Deal.stage, func.count(Deal.id), func.coalesce(func.sum(Deal.amount), 0))
        .where(Deal.project_id == project.id)
        .group_by(Deal.stage)
    ).all():
        if stage == STAGE_SALE:
            sales_total += count
            revenue_total += int(amount)
        else:
            leads_total += count

    stages = [
        Stage("views", "Показы", "статьи на площадках", sum(s.views for s in stats)),
        Stage("reads", "Дочитывания", "дочитали до конца", sum(s.reads for s in stats)),
        Stage("clicks", "Переходы", "кликнули на канал", sum(s.clicks for s in stats)),
        Stage("subscribers", "Подписки", "вступили в канал", total_subscribers),
        Stage("leads", "Заявки", "записались на диагностику", leads_total),
        Stage("sales", "Продажи", "оплатили услугу", sales_total),
    ]
    for i, stage in enumerate(stages[:-1]):
        nxt = stages[i + 1]
        stage.conversion = round(nxt.count / stage.count * 100, 1) if stage.count else None
        stage.lost = max(stage.count - nxt.count, 0)

    return Funnel(
        stages=stages,
        revenue=revenue_total,
        articles=len(stats),
        published=sum(1 for s in stats if s.article.published_at is not None),
    )


def top_articles(
    db: Session, project: Project, *, limit: int = 5, stats: list[ArticleStats] | None = None
) -> list[ArticleStats]:
    stats = stats if stats is not None else article_stats(db, project)
    ranked = [s for s in stats if s.score > 0]
    ranked.sort(key=lambda s: s.score, reverse=True)
    return ranked[:limit]


def platform_stats(
    db: Session, project: Project, stats: list[ArticleStats] | None = None
) -> list[PlatformStats]:
    from app.core.formats import FORMATS

    stats = stats if stats is not None else article_stats(db, project)
    buckets: dict[str, PlatformStats] = {}
    for item in stats:
        key = item.article.platform
        bucket = buckets.setdefault(
            key, PlatformStats(key=key, title=FORMATS[key].title if key in FORMATS else key)
        )
        bucket.articles += 1
        bucket.views += item.views
        bucket.reads += item.reads
        bucket.clicks += item.clicks
        bucket.subscribers += item.subscribers
    return sorted(buckets.values(), key=lambda b: b.views, reverse=True)


def recent_deals(db: Session, project: Project, limit: int = 12) -> list[Deal]:
    return list(
        db.scalars(
            select(Deal)
            .where(Deal.project_id == project.id)
            .order_by(Deal.day.desc(), Deal.id.desc())
            .limit(limit)
        )
    )


# ------------------------------------------------------------------ петля обучения


def strip_cta(body_md: str) -> str:
    """Убрать хвост со ссылками: в примере для модели он только мешает."""
    index = body_md.find(CTA_MARKER)
    return (body_md[:index] if index != -1 else body_md).strip()


def learning_examples(db: Session, project: Project, *, limit: int = 2) -> list[str]:
    """Тексты для few-shot: сначала статьи-победители, потом эталоны из настроек.

    Это и есть «система учится на лучших статьях» из витрины MVP. Там это было только
    обещанием: параметр `best_examples` существовал, но никогда не передавался.
    """
    winners = [
        strip_cta(item.article.body_md)
        for item in top_articles(db, project, limit=limit)
        if item.article.body_md
    ]
    if len(winners) >= limit:
        return winners[:limit]

    seeded = db.scalars(
        select(Example)
        .where(Example.project_id == project.id)
        .order_by(Example.is_winner.desc(), Example.id)
    ).all()
    return (winners + [e.body for e in seeded])[:limit]

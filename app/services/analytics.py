"""Воронка, конверсии и «что зашло лучше».

Считаем ровно ту воронку, которую Артур описывал в правках:
показы → дочитывания → переходы → подписки → заявки → продажи.

Важная тонкость: показы и дочитывания берутся из **последнего среза** каждой статьи, а не
суммой всех срезов — иначе одна и та же статья, посчитанная за три недели, утроит показы.
"""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.content import Article, TrackingLink
from app.models.project import Example, Project
from app.models.tracking import STAGE_SALE, Deal, MetricsSnapshot, Subscriber
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
    """Ступень: сколько людей на ней насчитали. Только счётчик, без выводов."""

    key: str
    title: str
    note: str
    count: int


@dataclass
class Step:
    """Переход между ступенями, посчитанный ТОЛЬКО по сопоставимым статьям.

    Главная тонкость проекта: ступени меряются разными приборами. Показы и
    дочитывания приходят выгрузкой площадки, переходы считает наш редирект
    `/r/<code>`, подписки — телеграм-бот. Приборы стоят не на всех статьях.

    Если сложить всё подряд и поделить одно на другое, получится ложь: дочитывания
    статьи без трекинг-ссылки попадут в знаменатель, а её переходы в числитель
    попасть не могут — их физически никто не считал. Именно так дашборд объявлял
    «узким местом» шаг дочитывания → переходы: 20 кликов одной статьи делились на
    10 080 дочитываний шести. Настоящая конверсия на измеренной статье — 2.3%,
    то есть выше ориентира.

    Поэтому каждый переход считается по статьям, где измерены ОБА конца, и честно
    сообщает, по скольким статьям он посчитан.
    """

    key: str
    from_title: str
    to_title: str
    action: str  # «дочитали до конца», «кликнули на канал»
    from_count: int
    to_count: int
    articles: int  # статей, попавших в расчёт
    total_articles: int
    # Шаг считается по проекту целиком (заявки и продажи вносятся руками),
    # поэтому оговорка про покрытие к нему не относится.
    project_wide: bool = False

    @property
    def benchmark(self) -> float | None:
        return BENCHMARKS.get(self.key)

    @property
    def measured(self) -> bool:
        """Есть ли вообще на чём считать: нашлась статья с обоими приборами."""
        return self.from_count > 0 and (self.project_wide or self.articles > 0)

    @property
    def partial(self) -> bool:
        """Прибор стоит не на всех статьях — цифру нельзя читать как общую."""
        return not self.project_wide and 0 < self.articles < self.total_articles

    @property
    def conversion(self) -> float | None:
        if not self.measured:
            return None
        return round(self.to_count / self.from_count * 100, 2)

    @property
    def lost(self) -> int:
        return max(self.from_count - self.to_count, 0)

    @property
    def ratio(self) -> float | None:
        """Во сколько раз конверсия отличается от ориентира.

        Это единственная величина, сравнимая между шагами: сами конверсии живут в
        разных порядках (41% и 2% — обе нормальные), а «доля от ожидаемого» у всех
        шагов измеряется одинаково. Её и рисуем.
        """
        conversion, benchmark = self.conversion, self.benchmark
        if conversion is None or not benchmark:
            return None
        return round(conversion / benchmark, 2)

    @property
    def is_bottleneck(self) -> bool:
        ratio = self.ratio
        return ratio is not None and ratio < 1


@dataclass
class Funnel:
    stages: list[Stage] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    revenue: int = 0
    articles: int = 0
    published: int = 0
    # Показы и переходы, посчитанные по одним и тем же статьям, — для честного CTR
    ctr_views: int = 0
    ctr_clicks: int = 0
    ctr_articles: int = 0

    def get(self, key: str) -> Stage | None:
        return next((stage for stage in self.stages if stage.key == key), None)

    def step(self, key: str) -> Step | None:
        return next((step for step in self.steps if step.key == key), None)

    @property
    def worst_step(self) -> Step | None:
        """Где чинить в первую очередь: сильнее всего отстаёт от ориентира.

        Раньше «узким местом» объявлялся первый шаг, не дотянувший до ориентира.
        Но шагов, не дотянувших до ориентира, может быть несколько, и «первый»
        не значит «худший» — сравнивать надо по отставанию, а не по порядку.
        """
        candidates = [s for s in self.steps if s.is_bottleneck]
        return min(candidates, key=lambda s: s.ratio) if candidates else None

    @property
    def ctr(self) -> float | None:
        """Переходы к показам — та самая «CTR статьи» из правок Артура.

        Считается только по статьям, где стоят оба прибора: иначе в знаменатель
        попадают показы статей, у которых переходы никто не считал.
        """
        if not self.ctr_views:
            return None
        return round(self.ctr_clicks / self.ctr_views * 100, 2)

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
    # Какие приборы стоят на этой статье: выгрузка площадки и трекинг-ссылка.
    # Без них ноль в колонке значит «не мерили», а не «никто не пришёл».
    has_metrics: bool = False
    has_links: bool = False

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
    def is_empty(self) -> bool:
        """По статье ещё нет ни одного измерения — она не опубликована или нет выгрузки.

        Такие строки на дашборде отделены от остальных: сплошные нули посреди
        таблицы читаются как «система не работает», хотя мерить просто нечего.
        """
        return not (
            self.views or self.reads or self.clicks or self.subscribers or self.leads or self.sales
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
                has_metrics=metric is not None,
                # Статья попадает в словарь кликов, если у неё есть хоть одна
                # трекинг-ссылка, — даже когда кликов по ней ноль.
                has_links=article.id in clicks,
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

    total_articles = len(stats)

    def by_articles(key, from_title, to_title, action, fits, left, right) -> Step:
        """Переход по статьям, где измерены оба его конца."""
        subset = [s for s in stats if fits(s)]
        return Step(
            key=key,
            from_title=from_title,
            to_title=to_title,
            action=action,
            from_count=sum(left(s) for s in subset),
            to_count=sum(right(s) for s in subset),
            articles=len(subset),
            total_articles=total_articles,
        )

    steps = [
        by_articles(
            "views", "Показы", "Дочитывания", "дочитали до конца",
            lambda s: s.has_metrics, lambda s: s.views, lambda s: s.reads,
        ),
        by_articles(
            "reads", "Дочитывания", "Переходы", "кликнули на канал",
            lambda s: s.has_metrics and s.has_links, lambda s: s.reads, lambda s: s.clicks,
        ),
        by_articles(
            "clicks", "Переходы", "Подписки", "вступили в канал",
            lambda s: s.has_links, lambda s: s.clicks, lambda s: s.subscribers,
        ),
        # Заявки и продажи вносятся руками по проекту целиком — делить их
        # по статьям нечем, поэтому шаг честно помечен как общий.
        Step(
            "subscribers", "Подписки", "Заявки", "записались на диагностику",
            total_subscribers, leads_total, total_articles, total_articles, project_wide=True,
        ),
        Step(
            "leads", "Заявки", "Продажи", "оплатили услугу",
            leads_total, sales_total, total_articles, total_articles, project_wide=True,
        ),
    ]

    ctr_subset = [s for s in stats if s.has_metrics and s.has_links]

    return Funnel(
        stages=stages,
        steps=steps,
        revenue=revenue_total,
        articles=total_articles,
        published=sum(1 for s in stats if s.article.published_at is not None),
        ctr_views=sum(s.views for s in ctr_subset),
        ctr_clicks=sum(s.clicks for s in ctr_subset),
        ctr_articles=len(ctr_subset),
    )


@dataclass
class TimelinePoint:
    day: date
    views: int


@dataclass
class TimelineEvent:
    """Событие ленты. Сумма отдаётся числом: форматирование живёт в web/filters.py,
    сервис про пробелы-разделители и знак рубля знать не должен."""

    day: date
    title: str
    note: str = ""
    is_money: bool = False
    amount: int = 0


@dataclass
class TimelinePlot:
    """Готовые координаты для SVG: шаблон только рисует, не считает."""

    line: str = ""
    dots: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    ticks: list[dict] = field(default_factory=list)
    grid: list[dict] = field(default_factory=list)


@dataclass
class Timeline:
    """Как это росло: накопительные показы по датам выгрузок плюс лента событий.

    Точки — именно даты замеров, а не дни. Статистику площадок выгружают руками,
    поэтому между выгрузками мы про показы ничего не знаем и рисовать там нечего.
    """

    points: list[TimelinePoint] = field(default_factory=list)
    events: list[TimelineEvent] = field(default_factory=list)

    def __bool__(self) -> bool:
        # По одной точке линию не построить — показывать нечего.
        return len(self.points) >= 2

    def plot(
        self, *, x0: float = 60.0, x1: float = 960.0, y_top: float = 40.0, y_base: float = 196.0
    ) -> TimelinePlot:
        if not self:
            return TimelinePlot()

        days = [p.day for p in self.points] + [e.day for e in self.events]
        first, last = min(days), max(days)
        span = (last - first).days or 1
        peak = max(p.views for p in self.points) or 1

        def x_of(day: date) -> float:
            return round(x0 + (day - first).days / span * (x1 - x0), 1)

        def y_of(views: int) -> float:
            return round(y_base - views / peak * (y_base - y_top), 1)

        dots = [
            {"x": x_of(p.day), "y": y_of(p.views), "views": p.views, "day": p.day}
            for p in self.points
        ]
        line = "M " + " L ".join(f"{d['x']} {d['y']}" for d in dots)

        # Круглая верхняя отметка сетки, чтобы подпись оси была человеческой
        step = 10 ** (len(str(peak)) - 1)
        grid = []
        value = 0
        while value <= peak:
            grid.append({"y": y_of(value), "label": value})
            value += step

        ticks = [
            {"x": x_of(day), "label": day.strftime("%d.%m")}
            for day in (first, last)
        ]
        events = [
            {
                "x": x_of(e.day),
                "title": e.title,
                "note": e.note,
                "is_money": e.is_money,
                "amount": e.amount,
            }
            for e in self.events
        ]
        return TimelinePlot(line=line, dots=dots, events=events, ticks=ticks, grid=grid)


def timeline(db: Session, project: Project) -> Timeline:
    """Накопительные показы по датам выгрузок + события (подписки, заявки, продажи)."""
    rows = db.execute(
        select(MetricsSnapshot.collected_at, MetricsSnapshot.article_id, MetricsSnapshot.views)
        .join(Article, Article.id == MetricsSnapshot.article_id)
        .where(Article.project_id == project.id)
        .order_by(MetricsSnapshot.collected_at)
    ).all()

    # На каждую дату — последнее известное значение по каждой статье, а не сумма всех
    # срезов: иначе статья, померенная три недели подряд, утроит показы проекта.
    latest: dict[int, int] = {}
    points: list[TimelinePoint] = []
    for collected_at, article_id, views in rows:
        day = collected_at.date() if hasattr(collected_at, "date") else collected_at
        latest[article_id] = views or 0
        total = sum(latest.values())
        if points and points[-1].day == day:
            points[-1].views = total
        else:
            points.append(TimelinePoint(day=day, views=total))

    events: list[TimelineEvent] = []

    subs_by_day: dict[date, int] = {}
    for (joined_at,) in db.execute(
        select(Subscriber.joined_at).where(
            Subscriber.project_id == project.id, Subscriber.joined_at.isnot(None)
        )
    ).all():
        day = joined_at.date() if hasattr(joined_at, "date") else joined_at
        subs_by_day[day] = subs_by_day.get(day, 0) + 1
    for day, count in subs_by_day.items():
        word = "подписка" if count % 10 == 1 and count % 100 != 11 else "подписки"
        events.append(TimelineEvent(day=day, title=f"{count} {word}", note="вступили в канал"))

    for deal in db.scalars(
        select(Deal).where(Deal.project_id == project.id).order_by(Deal.day)
    ):
        if deal.stage == STAGE_SALE:
            events.append(
                TimelineEvent(
                    day=deal.day,
                    title="продажа",
                    note=deal.service or "",
                    is_money=True,
                    amount=deal.amount,
                )
            )
        else:
            events.append(TimelineEvent(day=deal.day, title="заявка", note=deal.niche or ""))

    events.sort(key=lambda e: e.day)
    return Timeline(points=points, events=events)


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

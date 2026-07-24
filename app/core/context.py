"""Мост между ORM-моделями и чистым ядром генератора."""
from urllib.parse import urlencode, urlparse, urlunparse

from app.core.formats import KIND_POST
from app.core.generator import CaseSpec, VoiceSpec
from app.models.project import Project

CAMPAIGN = "baza-marketing"


def voice_spec(project: Project) -> VoiceSpec:
    voice = project.voice
    if voice is None:
        return VoiceSpec()
    return VoiceSpec(
        brand=voice.brand,
        who=voice.who,
        core_idea=voice.core_idea,
        audience=voice.audience,
        offer_cta=voice.offer_cta,
        persona=voice.persona,
        format_rules=list(voice.format_rules or []),
        avoid_map=dict(voice.avoid_map or {}),
        structure=list(voice.structure or []),
        hashtags=list(voice.hashtags or []),
    )


def case_specs(project: Project) -> list[CaseSpec]:
    return [CaseSpec(niche=c.niche, metric=c.metric) for c in project.cases]


def example_bodies(project: Project, limit: int = 2) -> list[str]:
    """Few-shot: сначала «победители» (петля обучения включится на этапе 4)."""
    ordered = sorted(project.examples, key=lambda e: (not e.is_winner, e.id))
    return [e.body for e in ordered[:limit]]


def add_utm(url: str, *, platform: str, kind: str, content: str) -> str:
    if not url:
        return url
    parts = urlparse(url)
    utm = urlencode(
        {
            "utm_source": platform,
            "utm_medium": "post" if kind == KIND_POST else "article",
            "utm_campaign": CAMPAIGN,
            "utm_content": content,
        }
    )
    query = f"{parts.query}&{utm}" if parts.query else utm
    return urlunparse(parts._replace(query=query))

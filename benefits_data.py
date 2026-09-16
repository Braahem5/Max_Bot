"""Загрузка и фильтрация демо-базы льгот.

Данные лежат в data/benefits.json: статичный, вручную собранный набор,
без обращений к внешним API. У бота нет сетевых зависимостей, кроме
самого MAX. Оговорки по источникам см. в "_meta.disclaimer" этого файла.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent / "data" / "benefits.json"


@dataclass(frozen=True)
class Benefit:
    id: str
    title: str
    category: str
    region: str
    summary: str
    description: str
    source: str
    how_to_apply: str
    documents: list[str]


def _load() -> tuple[dict[str, str], dict[str, str], list[Benefit]]:
    raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    categories = raw["_meta"]["categories"]
    regions = raw["_meta"]["regions"]
    benefits = [Benefit(**item) for item in raw["benefits"]]
    return categories, regions, benefits


CATEGORIES, REGIONS, _BENEFITS = _load()
_BY_ID = {b.id: b for b in _BENEFITS}


def get_benefit(benefit_id: str) -> Benefit | None:
    return _BY_ID.get(benefit_id)


def find_benefits(category: str, region: str) -> list[Benefit]:
    """Federal (region == "all") benefits for the category, plus any
    region-specific ones, when the user picked a real region."""
    return [
        b
        for b in _BENEFITS
        if b.category == category and (b.region == "all" or b.region == region)
    ]

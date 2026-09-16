"""Загрузка и фильтрация демо-базы льгот.

Данные лежат в data/benefits.json: статичный, вручную собранный набор,
без обращений к внешним API. У бота нет сетевых зависимостей, кроме
самого MAX. Оговорки по источникам см. в "_meta.disclaimer" этого файла.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    source_url: str | None = None
    application_url: str | None = None
    checked_at: str | None = None
    rules: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class BenefitCheck:
    """Результат предварительной проверки по ответам пользователя."""

    benefit: Benefit
    status: str
    reason: str | None = None


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


def classify_benefit(
    benefit: Benefit,
    answers: dict[str, str],
) -> BenefitCheck:
    """Классифицирует льготу без утверждения права на неё.

    Правила хранятся в JSON, поэтому добавление нового условия не требует
    изменения кода. Если у записи нет формализованных условий, она помечается
    как кандидат и всё равно сопровождается предупреждением в интерфейсе.
    """

    for rule in benefit.rules:
        field_name = rule["field"]
        expected = rule["equals"]
        value = answers.get(field_name)
        if value is None or value == "unknown":
            return BenefitCheck(
                benefit=benefit,
                status="check",
                reason=rule.get("missing_message", "нужно уточнить условие"),
            )
        if value != expected:
            return BenefitCheck(
                benefit=benefit,
                status="not_for_profile",
                reason=rule.get("mismatch_message", "условие не совпало"),
            )

    return BenefitCheck(
        benefit=benefit,
        status="candidate",
        reason="окончательное решение принимает ведомство",
    )


def check_benefits(
    category: str,
    region: str,
    answers: dict[str, str],
) -> list[BenefitCheck]:
    """Возвращает предварительный результат подбора по категории и региону."""

    return [
        classify_benefit(benefit, answers)
        for benefit in find_benefits(category, region)
    ]

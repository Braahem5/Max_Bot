"""Бот «Опора»: персональный маршрут по мерам поддержки в MAX.

Основной сценарий для демо:
    /start -> многодетная семья -> регион -> короткий опрос
    -> предварительный список мер -> официальная ссылка
    -> сохранить в «Мой план» -> напоминание.

Решение полностью детерминированное: правила хранятся в JSON, генеративный
ИИ не используется. Бот не выносит решение о праве на выплату и не заменяет
ведомство.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from maxapi import Bot, Dispatcher
from maxapi.filters.callback_payload import CallbackPayload
from maxapi.types import BotStarted, Command, MessageCreated
from maxapi.types.attachments.buttons import CallbackButton, LinkButton
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from benefits_data import (
    CATEGORIES,
    REGIONS,
    Benefit,
    BenefitCheck,
    check_benefits,
    find_benefits,
    get_benefit,
)
from storage import Storage

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("opora.max")

bot = Bot()
dp = Dispatcher()
storage = Storage()
MOSCOW = ZoneInfo("Europe/Moscow")


# ── Callback payloads ──────────────────────────────────────────────────────


class CategoryPayload(CallbackPayload):
    category: str


class RegionPayload(CallbackPayload):
    category: str
    region: str


class DetailPayload(CallbackPayload):
    benefit_id: str
    category: str
    region: str


class RestartPayload(CallbackPayload):
    pass


class PlanPayload(CallbackPayload):
    pass


class SavePlanPayload(CallbackPayload):
    benefit_id: str


class ReminderPayload(CallbackPayload):
    benefit_id: str
    days: str


class PlanItemPayload(CallbackPayload):
    benefit_id: str
    action: str


class FamilyChildrenPayload(CallbackPayload):
    region: str
    children_count: str


class FamilyYoungChildPayload(CallbackPayload):
    region: str
    children_count: str
    young_child: str


class FamilySchoolChildPayload(CallbackPayload):
    region: str
    children_count: str
    young_child: str
    school_child: str


class FamilyResidencePayload(CallbackPayload):
    region: str
    children_count: str
    young_child: str
    school_child: str
    residence: str


class FamilyCertificatePayload(CallbackPayload):
    region: str
    children_count: str
    young_child: str
    school_child: str
    residence: str
    certificate: str


class FamilyResultsPayload(CallbackPayload):
    region: str
    children_count: str
    young_child: str
    school_child: str
    residence: str
    certificate: str


class FamilyDetailPayload(CallbackPayload):
    benefit_id: str
    region: str
    children_count: str
    young_child: str
    school_child: str
    residence: str
    certificate: str


# ── Тексты и клавиатуры ────────────────────────────────────────────────────


def welcome_text(first_name: str | None) -> str:
    greeting = f"Привет, {first_name}! 👋" if first_name else "Привет! 👋"
    return (
        f"{greeting} Я — «Опора», персональный навигатор по мерам поддержки.\n\n"
        "Задам несколько коротких вопросов, покажу, что стоит проверить, "
        "какие документы подготовить и куда обратиться. "
        "Окончательное решение всегда принимает ведомство.\n"
        "🔒 Паспорт, СНИЛС и банковские данные не нужны.\n\n"
        "Выберите жизненную ситуацию:"
    )


def _button(text: str, payload: CallbackPayload) -> CallbackButton:
    return CallbackButton(text=text, payload=payload.pack())


def _link(text: str, url: str) -> LinkButton:
    return LinkButton(text=text, url=url)


def category_keyboard():
    builder = InlineKeyboardBuilder()
    for code, label in CATEGORIES.items():
        builder.row(_button(label, CategoryPayload(category=code)))
    builder.row(_button("📋 Мой план", PlanPayload()))
    return [builder.as_markup()]


def region_keyboard(category: str):
    builder = InlineKeyboardBuilder()
    for code, label in REGIONS.items():
        text = "🌍 " + label if code == "all" else label
        builder.row(_button(text, RegionPayload(category=category, region=code)))
    builder.row(_button("⬅ Начать заново", RestartPayload()))
    return [builder.as_markup()]


def results_keyboard(category: str, region: str, benefit_ids: list[str]):
    builder = InlineKeyboardBuilder()
    for benefit_id in benefit_ids:
        benefit = get_benefit(benefit_id)
        if benefit is not None:
            builder.row(
                _button(
                    f"ℹ️ {benefit.title}",
                    DetailPayload(
                        benefit_id=benefit_id,
                        category=category,
                        region=region,
                    ),
                )
            )
    builder.row(
        _button(
            "🔄 Обновить список",
            RegionPayload(category=category, region=region),
        )
    )
    builder.row(_button("📋 Мой план", PlanPayload()))
    builder.row(_button("⬅ Начать заново", RestartPayload()))
    return [builder.as_markup()]


def family_children_keyboard(region: str):
    builder = InlineKeyboardBuilder()
    for code, label in (
        ("3", "3 ребёнка"),
        ("4", "4 ребёнка"),
        ("5plus", "5 и более"),
    ):
        builder.row(
            _button(
                label,
                FamilyChildrenPayload(region=region, children_count=code),
            )
        )
    return [builder.as_markup()]


def family_young_child_keyboard(region: str, children_count: str):
    builder = InlineKeyboardBuilder()
    for code, label in (("yes", "Да"), ("no", "Нет"), ("unknown", "Не знаю")):
        builder.row(
            _button(
                label,
                FamilyYoungChildPayload(
                    region=region,
                    children_count=children_count,
                    young_child=code,
                ),
            )
        )
    return [builder.as_markup()]


def family_school_child_keyboard(
    region: str,
    children_count: str,
    young_child: str,
):
    builder = InlineKeyboardBuilder()
    for code, label in (("yes", "Да"), ("no", "Нет"), ("unknown", "Не знаю")):
        builder.row(
            _button(
                label,
                FamilySchoolChildPayload(
                    region=region,
                    children_count=children_count,
                    young_child=young_child,
                    school_child=code,
                ),
            )
        )
    return [builder.as_markup()]


def family_residence_keyboard(
    region: str,
    children_count: str,
    young_child: str,
    school_child: str,
):
    builder = InlineKeyboardBuilder()
    for code, label in (
        ("city", "Город, например Казань"),
        ("rural", "Село или посёлок"),
        ("unknown", "Не знаю"),
    ):
        builder.row(
            _button(
                label,
                FamilyResidencePayload(
                    region=region,
                    children_count=children_count,
                    young_child=young_child,
                    school_child=school_child,
                    residence=code,
                ),
            )
        )
    return [builder.as_markup()]


def family_certificate_keyboard(
    region: str,
    children_count: str,
    young_child: str,
    school_child: str,
    residence: str,
):
    builder = InlineKeyboardBuilder()
    for code, label in (
        ("yes", "Да, удостоверение есть"),
        ("no", "Нет, ещё не оформлял(а)"),
        ("unknown", "Не знаю"),
    ):
        builder.row(
            _button(
                label,
                FamilyCertificatePayload(
                    region=region,
                    children_count=children_count,
                    young_child=young_child,
                    school_child=school_child,
                    residence=residence,
                    certificate=code,
                ),
            )
        )
    return [builder.as_markup()]


def _family_profile(
    *,
    region: str,
    children_count: str,
    young_child: str,
    school_child: str,
    residence: str,
    certificate: str,
) -> dict[str, str]:
    return {
        "category": "multichild",
        "region": region,
        "children_count": children_count,
        "young_child": young_child,
        "school_child": school_child,
        "residence": residence,
        "certificate": certificate,
    }


def _family_results_payload(profile: dict[str, str]) -> FamilyResultsPayload:
    return FamilyResultsPayload(
        region=profile["region"],
        children_count=profile["children_count"],
        young_child=profile["young_child"],
        school_child=profile["school_child"],
        residence=profile["residence"],
        certificate=profile["certificate"],
    )


def _family_detail_payload(
    profile: dict[str, str],
    benefit_id: str,
) -> FamilyDetailPayload:
    return FamilyDetailPayload(
        benefit_id=benefit_id,
        region=profile["region"],
        children_count=profile["children_count"],
        young_child=profile["young_child"],
        school_child=profile["school_child"],
        residence=profile["residence"],
        certificate=profile["certificate"],
    )


def _family_profile_text(profile: dict[str, str]) -> str:
    children = {
        "3": "3 детей",
        "4": "4 детей",
        "5plus": "5 и более детей",
    }.get(profile["children_count"], "3 и более детей")
    young = {"yes": "да", "no": "нет", "unknown": "не знаю"}[
        profile["young_child"]
    ]
    school = {"yes": "да", "no": "нет", "unknown": "не знаю"}[
        profile["school_child"]
    ]
    residence = {
        "city": "город",
        "rural": "село/посёлок",
        "unknown": "не знаю",
    }[profile["residence"]]
    certificate = {
        "yes": "есть",
        "no": "ещё нет",
        "unknown": "не знаю",
    }[profile["certificate"]]
    return (
        f"{children}; ребёнок до 6 лет — {young}; "
        f"школьник/студент СПО до 18 лет — {school}; "
        f"место жительства — {residence}; "
        f"удостоверение — {certificate}"
    )


def family_results_keyboard(
    profile: dict[str, str],
    checks: list[BenefitCheck],
):
    builder = InlineKeyboardBuilder()
    for check in checks:
        if check.status == "not_for_profile":
            continue
        prefix = "✅" if check.status == "candidate" else "🔎"
        builder.row(
            _button(
                f"{prefix} {check.benefit.title}",
                _family_detail_payload(profile, check.benefit.id),
            )
        )
    builder.row(_button("🔄 Пересчитать", _family_results_payload(profile)))
    builder.row(_button("📋 Мой план", PlanPayload()))
    builder.row(_button("⬅ Начать заново", RestartPayload()))
    return [builder.as_markup()]


def detail_keyboard(benefit: Benefit, back_payload: CallbackPayload):
    builder = InlineKeyboardBuilder()
    if benefit.application_url:
        builder.row(_link("🌐 Перейти к оформлению", benefit.application_url))
    if benefit.source_url:
        builder.row(_link("📚 Официальный источник", benefit.source_url))
    builder.row(
        _button("⭐ Сохранить в мой план", SavePlanPayload(benefit_id=benefit.id))
    )
    builder.row(
        _button(
            "⏰ Напомнить через 7 дней",
            ReminderPayload(benefit_id=benefit.id, days="7"),
        )
    )
    builder.row(
        _button(
            "⏰ Напомнить через 30 дней",
            ReminderPayload(benefit_id=benefit.id, days="30"),
        )
    )
    builder.row(_button("⬅ Назад", back_payload))
    builder.row(_button("⬅ Начать заново", RestartPayload()))
    return [builder.as_markup()]


def benefit_card_text(benefit: Benefit) -> str:
    documents_list = "\n".join(
        f"  {i}. {doc}" for i, doc in enumerate(benefit.documents, 1)
    )
    source_url = f"\n{benefit.source_url}" if benefit.source_url else ""
    application_url = (
        f"\n{benefit.application_url}" if benefit.application_url else ""
    )
    checked_at = (
        f"\n🕒 Проверено по источнику: {benefit.checked_at}"
        if benefit.checked_at
        else ""
    )
    return (
        f"📌 {benefit.title}\n\n"
        f"{benefit.description}\n\n"
        f"📚 Источник:\n{benefit.source}{source_url}{checked_at}\n\n"
        f"✅ Как оформить:\n{benefit.how_to_apply}{application_url}\n\n"
        "📋 Какие документы могут понадобиться:\n"
        f"{documents_list}\n\n"
        "⚠️ Это предварительная навигация по открытым данным. "
        "Окончательное право и комплект документов подтверждает ведомство."
    )


def _plan_view(user_id: int):
    rows = storage.list_plan(user_id)
    if not rows:
        builder = InlineKeyboardBuilder()
        builder.row(_button("⬅ К категориям", RestartPayload()))
        return (
            "📋 В вашем плане пока нет мер поддержки.\n\n"
            "Откройте карточку льготы и нажмите «Сохранить в мой план».",
            [builder.as_markup()],
        )

    status_labels = {
        "new": "🟡 не начато",
        "done": "✅ выполнено",
    }
    lines = ["📋 Ваш план действий:", ""]
    builder = InlineKeyboardBuilder()
    for row in rows:
        status = status_labels.get(row["status"], "🟡 не начато")
        lines.append(f"• {row['title']} — {status}")
        builder.row(
            _button(
                f"🔎 Открыть: {row['title']}",
                PlanItemPayload(benefit_id=row["benefit_id"], action="open"),
            )
        )
        if row["status"] != "done":
            builder.row(
                _button(
                    "✅ Отметить выполненным",
                    PlanItemPayload(
                        benefit_id=row["benefit_id"],
                        action="done",
                    ),
                )
            )
    builder.row(_button("⬅ К категориям", RestartPayload()))
    return "\n".join(lines), [builder.as_markup()]


# ── Общие хэндлеры ─────────────────────────────────────────────────────────


@dp.bot_started()
async def bot_started(event: BotStarted):
    await event.bot.send_message(
        chat_id=event.chat_id,
        text=welcome_text(event.user.first_name),
        attachments=category_keyboard(),
    )


@dp.message_created(Command("start"))
async def on_start(event: MessageCreated):
    first_name = event.message.sender.first_name if event.message.sender else None
    await event.message.answer(
        text=welcome_text(first_name),
        attachments=category_keyboard(),
    )


@dp.message_callback(RestartPayload.filter())
async def on_restart(event: MessageCallback):
    await event.answer()
    assert event.message is not None
    await event.message.answer(
        text=welcome_text(event.callback.user.first_name),
        attachments=category_keyboard(),
    )


@dp.message_callback(PlanPayload.filter())
async def on_plan(event: MessageCallback):
    await event.answer()
    assert event.message is not None
    _, user_id = event.get_ids()
    text, attachments = _plan_view(user_id)
    await event.message.answer(text=text, attachments=attachments)


async def _reply_stale_selection(event: MessageCallback) -> None:
    assert event.message is not None
    await event.message.answer(
        text="Этот вариант больше не в базе. Начнём заново:",
        attachments=category_keyboard(),
    )


@dp.message_callback(CategoryPayload.filter())
async def on_category(event: MessageCallback, payload: CategoryPayload):
    await event.answer()
    assert event.message is not None
    category_label = CATEGORIES.get(payload.category)
    if category_label is None:
        await _reply_stale_selection(event)
        return
    await event.message.answer(
        text=(
            f"Категория: {category_label}\n\n"
            "Выберите регион: часть мер поддержки региональные."
        ),
        attachments=region_keyboard(payload.category),
    )


@dp.message_callback(RegionPayload.filter())
async def on_region(event: MessageCallback, payload: RegionPayload):
    await event.answer()
    assert event.message is not None

    category_label = CATEGORIES.get(payload.category)
    region_label = REGIONS.get(payload.region)
    if category_label is None or region_label is None:
        await _reply_stale_selection(event)
        return

    if payload.category == "multichild":
        await event.message.answer(
            text=(
                f"Регион: {region_label}\n\n"
                "Сделаем подбор точнее. Сколько детей сейчас в семье?"
            ),
            attachments=family_children_keyboard(payload.region),
        )
        return

    matches = find_benefits(payload.category, payload.region)
    if not matches:
        await event.message.answer(
            text=(
                f"По категории «{category_label}» ({region_label}) в демо-базе "
                "пока ничего не нашлось. Это учебная база хакатона, в ней "
                "не все меры поддержки России. Попробуйте другую категорию."
            ),
            attachments=region_keyboard(payload.category),
        )
        return

    lines = [
        f"Категория: {category_label} · Регион: {region_label}",
        "",
        (
            f"Нашёл {len(matches)} мер поддержки. "
            "Откройте карточку, чтобы увидеть условия и следующий шаг:"
        ),
    ]
    for benefit in matches:
        lines.append(f"\n• {benefit.title}\n  {benefit.summary}")

    await event.message.answer(
        text="\n".join(lines),
        attachments=results_keyboard(
            payload.category,
            payload.region,
            [benefit.id for benefit in matches],
        ),
    )


@dp.message_callback(DetailPayload.filter())
async def on_detail(event: MessageCallback, payload: DetailPayload):
    await event.answer()
    assert event.message is not None
    benefit = get_benefit(payload.benefit_id)
    if benefit is None:
        await event.message.answer(
            text="Эта мера поддержки больше не найдена в базе.",
            attachments=category_keyboard(),
        )
        return

    await event.message.answer(
        text=benefit_card_text(benefit),
        attachments=detail_keyboard(
            benefit,
            RegionPayload(
                category=payload.category,
                region=payload.region,
            ),
        ),
    )


# ── Персональный сценарий многодетной семьи ────────────────────────────────


@dp.message_callback(FamilyChildrenPayload.filter())
async def on_family_children(
    event: MessageCallback,
    payload: FamilyChildrenPayload,
):
    await event.answer()
    assert event.message is not None
    await event.message.answer(
        text="Есть ли в семье ребёнок младше 6 лет?",
        attachments=family_young_child_keyboard(
            payload.region,
            payload.children_count,
        ),
    )


@dp.message_callback(FamilyYoungChildPayload.filter())
async def on_family_young_child(
    event: MessageCallback,
    payload: FamilyYoungChildPayload,
):
    await event.answer()
    assert event.message is not None
    await event.message.answer(
        text="Есть ли ребёнок, который учится в школе или колледже и младше 18 лет?",
        attachments=family_school_child_keyboard(
            payload.region,
            payload.children_count,
            payload.young_child,
        ),
    )


@dp.message_callback(FamilySchoolChildPayload.filter())
async def on_family_school_child(
    event: MessageCallback,
    payload: FamilySchoolChildPayload,
):
    await event.answer()
    assert event.message is not None
    await event.message.answer(
        text="Где семья постоянно проживает?",
        attachments=family_residence_keyboard(
            payload.region,
            payload.children_count,
            payload.young_child,
            payload.school_child,
        ),
    )


@dp.message_callback(FamilyResidencePayload.filter())
async def on_family_residence(
    event: MessageCallback,
    payload: FamilyResidencePayload,
):
    await event.answer()
    assert event.message is not None
    await event.message.answer(
        text="У вас уже есть удостоверение многодетной семьи?",
        attachments=family_certificate_keyboard(
            payload.region,
            payload.children_count,
            payload.young_child,
            payload.school_child,
            payload.residence,
        ),
    )


async def _send_family_results(
    event: MessageCallback,
    profile: dict[str, str],
) -> None:
    assert event.message is not None
    checks = check_benefits(
        category="multichild",
        region=profile["region"],
        answers=profile,
    )
    candidates = [check for check in checks if check.status == "candidate"]
    review = [check for check in checks if check.status == "check"]
    hidden = [check for check in checks if check.status == "not_for_profile"]

    lines = [
        "🎯 Ваш предварительный маршрут",
        "",
        f"Профиль: {_family_profile_text(profile)}",
        "",
    ]
    if candidates:
        lines.append(f"✅ Стоит проверить в первую очередь ({len(candidates)}):")
        for check in candidates:
            lines.append(
                f"\n• {check.benefit.title}\n  {check.benefit.summary}"
            )
    if review:
        lines.append(f"\n🔎 Условия нужно уточнить ({len(review)}):")
        for check in review:
            lines.append(
                f"\n• {check.benefit.title}\n"
                f"  {check.benefit.summary}\n"
                f"  Причина: {check.reason}"
            )
    if hidden:
        lines.append(
            "\nℹ️ Часть мер не показана: по вашим ответам "
            "их ключевые условия не совпали."
        )
    if not candidates and not review:
        lines.append(
            "По введённым данным готовых совпадений нет. "
            "Проверьте условия в официальных сервисах."
        )
    lines.extend(
        [
            "",
            "Откройте карточку, чтобы сохранить меру в план, "
            "перейти к официальному сервису или поставить напоминание.",
            "",
            "⚠️ Подбор предварительный и не является решением ведомства.",
        ]
    )
    await event.message.answer(
        text="\n".join(lines),
        attachments=family_results_keyboard(profile, checks),
    )


@dp.message_callback(FamilyCertificatePayload.filter())
async def on_family_certificate(
    event: MessageCallback,
    payload: FamilyCertificatePayload,
):
    await event.answer()
    chat_id, user_id = event.get_ids()
    if chat_id is None:
        return
    profile = _family_profile(
        region=payload.region,
        children_count=payload.children_count,
        young_child=payload.young_child,
        school_child=payload.school_child,
        residence=payload.residence,
        certificate=payload.certificate,
    )
    storage.save_profile(user_id=user_id, chat_id=chat_id, answers=profile)
    await _send_family_results(event, profile)


@dp.message_callback(FamilyResultsPayload.filter())
async def on_family_results(
    event: MessageCallback,
    payload: FamilyResultsPayload,
):
    await event.answer()
    chat_id, user_id = event.get_ids()
    if chat_id is None:
        return
    profile = _family_profile(
        region=payload.region,
        children_count=payload.children_count,
        young_child=payload.young_child,
        school_child=payload.school_child,
        residence=payload.residence,
        certificate=payload.certificate,
    )
    storage.save_profile(user_id=user_id, chat_id=chat_id, answers=profile)
    await _send_family_results(event, profile)


@dp.message_callback(FamilyDetailPayload.filter())
async def on_family_detail(
    event: MessageCallback,
    payload: FamilyDetailPayload,
):
    await event.answer()
    assert event.message is not None
    benefit = get_benefit(payload.benefit_id)
    if benefit is None:
        await event.message.answer(
            text="Эта мера поддержки больше не найдена в базе.",
            attachments=category_keyboard(),
        )
        return
    profile = _family_profile(
        region=payload.region,
        children_count=payload.children_count,
        young_child=payload.young_child,
        school_child=payload.school_child,
        residence=payload.residence,
        certificate=payload.certificate,
    )
    await event.message.answer(
        text=benefit_card_text(benefit),
        attachments=detail_keyboard(
            benefit,
            _family_results_payload(profile),
        ),
    )


# ── План, статусы и напоминания ────────────────────────────────────────────


@dp.message_callback(SavePlanPayload.filter())
async def on_save_plan(event: MessageCallback, payload: SavePlanPayload):
    await event.answer()
    assert event.message is not None
    chat_id, user_id = event.get_ids()
    benefit = get_benefit(payload.benefit_id)
    if chat_id is None or benefit is None:
        return
    storage.save_plan(
        user_id=user_id,
        chat_id=chat_id,
        benefit_id=benefit.id,
        title=benefit.title,
        category=benefit.category,
        region=benefit.region,
    )
    builder = InlineKeyboardBuilder()
    builder.row(_button("📋 Открыть мой план", PlanPayload()))
    await event.message.answer(
        text=(
            f"⭐ «{benefit.title}» добавлена в ваш план.\n\n"
            "Откройте «Мой план», чтобы отметить шаг выполненным "
            "или посмотреть карточку снова."
        ),
        attachments=[builder.as_markup()],
    )


@dp.message_callback(ReminderPayload.filter())
async def on_reminder(event: MessageCallback, payload: ReminderPayload):
    await event.answer()
    assert event.message is not None
    chat_id, user_id = event.get_ids()
    benefit = get_benefit(payload.benefit_id)
    if chat_id is None or benefit is None:
        return
    days = int(payload.days)
    storage.save_plan(
        user_id=user_id,
        chat_id=chat_id,
        benefit_id=benefit.id,
        title=benefit.title,
        category=benefit.category,
        region=benefit.region,
    )
    due_at = int(
        (datetime.now(timezone.utc) + timedelta(days=days)).timestamp()
    )
    storage.set_reminder(
        user_id=user_id,
        chat_id=chat_id,
        benefit_id=benefit.id,
        reminder_at=due_at,
    )
    due_label = datetime.fromtimestamp(due_at, tz=MOSCOW).strftime("%d.%m.%Y")
    builder = InlineKeyboardBuilder()
    builder.row(_button("📋 Открыть мой план", PlanPayload()))
    await event.message.answer(
        text=(
            f"⏰ Напоминание установлено на {due_label}.\n"
            f"Мера: «{benefit.title}».\n\n"
            "Изменить или отметить шаг можно в разделе «Мой план»."
        ),
        attachments=[builder.as_markup()],
    )


@dp.message_callback(PlanItemPayload.filter())
async def on_plan_item(event: MessageCallback, payload: PlanItemPayload):
    await event.answer()
    assert event.message is not None
    _, user_id = event.get_ids()
    benefit = get_benefit(payload.benefit_id)
    if benefit is None:
        await event.message.answer("Эта мера больше не найдена в базе.")
        return

    if payload.action == "done":
        storage.set_status(
            user_id=user_id,
            benefit_id=payload.benefit_id,
            status="done",
        )
        text, attachments = _plan_view(user_id)
        await event.message.answer(
            text="✅ Шаг отмечен как выполненный.\n\n" + text,
            attachments=attachments,
        )
        return

    await event.message.answer(
        text=benefit_card_text(benefit),
        attachments=detail_keyboard(benefit, PlanPayload()),
    )


async def reminder_worker() -> None:
    """Отправляет due-напоминания из SQLite в фоне процесса бота."""

    while True:
        for reminder in storage.due_reminders(int(time.time())):
            benefit = get_benefit(reminder["benefit_id"])
            title = benefit.title if benefit else "сохранённая мера поддержки"
            try:
                await bot.send_message(
                    chat_id=reminder["chat_id"],
                    text=(
                        "⏰ Напоминание от «Опоры»\n\n"
                        f"Проверьте следующий шаг по мере: «{title}».\n"
                        "Откройте «Мой план», чтобы продолжить."
                    ),
                )
                storage.mark_reminder_sent(reminder["id"])
            except Exception:
                log.exception(
                    "Не удалось отправить напоминание %s",
                    reminder["id"],
                )
        await asyncio.sleep(60)


async def main() -> None:
    log.info("«Опора» запускается в MAX...")
    try:
        me = await bot.get_me()
        log.info("Подключено к MAX API как %s (id %s)", me.full_name, me.user_id)
    except Exception:
        log.exception(
            "Не удалось получить профиль бота через bot.get_me(). "
            "Проверьте MAX_BOT_TOKEN. Продолжаю запуск."
        )

    reminder_task = asyncio.create_task(reminder_worker())
    try:
        await dp.start_polling(bot)
    finally:
        reminder_task.cancel()
        with suppress(asyncio.CancelledError):
            await reminder_task


if __name__ == "__main__":
    asyncio.run(main())

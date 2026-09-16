"""Бот-помощник по подбору льгот и мер поддержки. MAX-хакатон 2026.

Диалог полностью кнопочный (никакого free-text парсинга):
    /start -> выбор категории -> выбор региона -> список подходящих льгот
    -> карточка льготы с описанием, порядком оформления и списком
    документов.

Все данные о льготах статичны и лежат в data/benefits.json, никакой
интеграции с Госуслугами нет, только собственная демо-база (см. README).
Состояние диалога не хранится нигде: каждый шаг кодируется прямо в
payload кнопки, поэтому бот переживает перезапуск без потери контекста
пользователя.

Есть две точки живой интеграции с самим MAX API (не с базой льгот). При
старте bot.get_me() подтверждает, что токен валиден и бот подключён
(см. main()). Приветствие персонализируется именем из профиля
пользователя, уже пришедшим во входящем событии (BotStarted.user,
MessageCreated.message.sender, Callback.user). Обе точки отказоустойчивы:
сбой не останавливает бота и не блокирует основной диалог.

Запуск:
    export MAX_BOT_TOKEN="токен-от-MasterBot"
    python benefits_bot.py
"""

from __future__ import annotations

import asyncio
import logging

from maxapi import Bot, Dispatcher
from maxapi.types import BotStarted, Command, MessageCreated
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.attachments.buttons import CallbackButton
from maxapi.filters.callback_payload import CallbackPayload
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from benefits_data import CATEGORIES, REGIONS, find_benefits, get_benefit

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("benefits.max")

bot = Bot()  # читает токен из переменной окружения MAX_BOT_TOKEN
dp = Dispatcher()


def welcome_text(first_name: str | None) -> str:
    """Приветствие, персонализированное именем из профиля MAX пользователя
    (это уже часть входящего события; API-подтверждение личности бота
    самого себя происходит отдельно, при старте, см. main())."""
    greeting = f"Привет, {first_name}! 👋" if first_name else "Привет! 👋"
    return (
        f"{greeting} Я помогу подобрать льготы и меры поддержки, на которые "
        "вы можете претендовать.\n\n"
        "Отвечу на пару вопросов кнопками и покажу список того, что вам "
        "положено, с описанием, порядком оформления и списком документов.\n\n"
        "Для начала: кто вы?"
    )


# ── Полезные нагрузки кнопок (шаги диалога кодируются прямо в кнопке) ───────

class CategoryPayload(CallbackPayload):
    category: str


class RegionPayload(CallbackPayload):
    category: str
    region: str


class DetailPayload(CallbackPayload):
    benefit_id: str


class RestartPayload(CallbackPayload):
    pass


# ── Клавиатуры ────────────────────────────────────────────────────────────

def _button(text: str, payload: CallbackPayload) -> CallbackButton:
    return CallbackButton(text=text, payload=payload.pack())


def category_keyboard():
    builder = InlineKeyboardBuilder()
    for code, label in CATEGORIES.items():
        builder.row(_button(label, CategoryPayload(category=code)))
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
        builder.row(_button(f"ℹ️ {benefit.title}", DetailPayload(benefit_id=benefit_id)))
    builder.row(_button("🔄 Обновить список", RegionPayload(category=category, region=region)))
    builder.row(_button("⬅ Начать заново", RestartPayload()))
    return [builder.as_markup()]


def detail_keyboard(category: str, region: str):
    builder = InlineKeyboardBuilder()
    builder.row(_button("⬅ Назад к списку", RegionPayload(category=category, region=region)))
    builder.row(_button("⬅ Начать заново", RestartPayload()))
    return [builder.as_markup()]


# ── Хэндлеры ─────────────────────────────────────────────────────────────

@dp.bot_started()
async def bot_started(event: BotStarted):
    assert event.bot is not None
    await event.bot.send_message(
        chat_id=event.chat_id,
        text=welcome_text(event.user.first_name),
        attachments=category_keyboard(),
    )


@dp.message_created(Command("start"))
async def on_start(event: MessageCreated):
    first_name = event.message.sender.first_name if event.message.sender else None
    await event.message.answer(text=welcome_text(first_name), attachments=category_keyboard())


@dp.message_callback(RestartPayload.filter())
async def on_restart(event: MessageCallback, payload: RestartPayload):
    await event.answer()
    assert event.message is not None
    await event.message.answer(
        text=welcome_text(event.callback.user.first_name),
        attachments=category_keyboard(),
    )


async def _reply_stale_selection(event: MessageCallback) -> None:
    """Нажатая кнопка ссылается на категорию/регион, которых больше нет в
    базе данных (это возможно, только если benefits.json поменяли, пока
    у пользователя на экране осталась старая клавиатура, например при
    правке данных во время демо). Вместо тихого KeyError отвечаем и
    предлагаем начать заново."""
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
        text=f"Категория: {category_label}\n\nТеперь укажите регион: часть льгот региональные.",
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

    matches = find_benefits(payload.category, payload.region)

    if not matches:
        await event.message.answer(
            text=(
                f"По категории «{category_label}» ({region_label}) в демо-базе пока "
                "ничего не нашлось. Это учебная база хакатона, в ней не все льготы "
                "России. Попробуйте другую категорию."
            ),
            attachments=region_keyboard(payload.category),
        )
        return

    lines = [f"Категория: {category_label} · Регион: {region_label}", ""]
    lines.append(f"Нашёл {len(matches)} подходящих льгот. Нажмите на название, чтобы узнать подробности и как оформить:")
    for b in matches:
        lines.append(f"\n• {b.title}\n  {b.summary}")

    await event.message.answer(
        text="\n".join(lines),
        attachments=results_keyboard(payload.category, payload.region, [b.id for b in matches]),
    )


@dp.message_callback(DetailPayload.filter())
async def on_detail(event: MessageCallback, payload: DetailPayload):
    await event.answer()
    assert event.message is not None

    benefit = get_benefit(payload.benefit_id)
    if benefit is None:
        await event.message.answer(text="Эта льгота больше не найдена в базе.", attachments=category_keyboard())
        return

    documents_list = "\n".join(f"  {i}. {doc}" for i, doc in enumerate(benefit.documents, 1))
    text = (
        f"📌 {benefit.title}\n\n"
        f"{benefit.description}\n\n"
        f"📚 Источник:\n{benefit.source}\n\n"
        f"✅ Как оформить:\n{benefit.how_to_apply}\n\n"
        f"📋 Какие документы понадобятся:\n{documents_list}"
    )
    await event.message.answer(text=text, attachments=detail_keyboard(benefit.category, benefit.region))


async def main() -> None:
    log.info("Бот-помощник по льготам запускается (long polling)...")
    try:
        me = await bot.get_me()
        log.info("Подключено к MAX API как %s (id %s)", me.full_name, me.user_id)
    except Exception:
        log.exception(
            "Не удалось получить профиль бота через bot.get_me(), "
            "проверьте MAX_BOT_TOKEN. Продолжаю запуск."
        )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

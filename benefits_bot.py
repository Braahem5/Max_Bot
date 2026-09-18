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

Есть две точки живой интеграции с самим MAX API (не с базой льгот).
Первая: при старте dp.start_polling() вызывает bot.get_me() и подтверждает,
что токен валиден и бот подключён. Если токен не задан или MAX его
отклонил, бот сразу завершается с понятной ошибкой в логе. При временных
сбоях сети или MAX (сеть, таймаут, ответы 5xx и 429) main() повторяет
запуск до STARTUP_ATTEMPTS раз подряд, любая другая ошибка (включая
ответы 400, 403 и 404) завершает бота сразу. Вторая точка: приветствие
персонализируется именем из профиля пользователя, уже пришедшим во
входящем событии (BotStarted.user, MessageCreated.message.sender,
Callback.user).

Дополнительно, если задать MAX_DELETE_WEBHOOK=1 (подходят также true и yes),
main() перед стартом удаляет подписки на вебхук: пока они есть, MAX не
отдаёт обновления через polling. По умолчанию удаление выключено,
диспетчер только предупреждает в логе, потому что тот же токен может
обслуживать другое развёртывание.

Запуск (переменные можно задать и в локальном .env, он подхватывается
автоматически):
    export MAX_BOT_TOKEN="токен-от-MasterBot"
    python benefits_bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiohttp import ClientError
from dotenv import load_dotenv
from maxapi import Bot, Dispatcher
from maxapi.enums.chat_type import ChatType
from maxapi.exceptions import InvalidToken, MaxApiError, MaxConnection
from maxapi.types import BotStarted, Command, MessageCreated
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.attachments.buttons import CallbackButton
from maxapi.filters.callback_payload import CallbackPayload
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from benefits_data import CATEGORIES, COLLECTED_AT, REGIONS, find_benefits, get_benefit

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("benefits.max")

# Подхватываем .env, лежащий рядом с этим файлом, независимо от того, из
# какой папки запущен бот. Уже заданные переменные окружения он не
# перезаписывает, поэтому в Docker с env_file ничего не меняется.
load_dotenv(Path(__file__).resolve().parent / ".env")

_token = (os.environ.get("MAX_BOT_TOKEN") or "").strip()
if not _token:
    # Bot() ниже упал бы на импорте с сырым traceback, а не с понятным логом.
    log.error("Не задан MAX_BOT_TOKEN: укажите токен от MasterBot в переменной окружения или в .env.")
    raise SystemExit(1)

# Токен передаём уже без пробелов и перевода строки по краям (частая
# история при копировании или в env_file), иначе они попали бы в заголовок.
bot = Bot(token=_token)
dp = Dispatcher()

STARTUP_ATTEMPTS = 5  # подряд идущих неудачных запусков при временных сбоях
STARTUP_RETRY_DELAY = 5  # секунд между попытками


def _is_retryable(exc: Exception) -> bool:
    """Сбой стоит переждать и повторить запуск: сеть, таймаут, ошибка сервера
    MAX (5xx) или лимит запросов (429). Остальные ответы MAX (400, 403, 404)
    и любые ошибки в коде или настройках от повтора не исчезнут."""
    if isinstance(exc, MaxApiError):
        return exc.code >= 500 or exc.code == 429
    return isinstance(exc, (MaxConnection, ClientError, TimeoutError))


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def welcome_text(first_name: str | None) -> str:
    """Приветствие, персонализированное именем из профиля MAX пользователя
    (это уже часть входящего события; проверка токена и личности самого
    бота происходит отдельно, при старте, см. main())."""
    greeting = f"Привет, {first_name}! 👋" if first_name else "Привет! 👋"
    return (
        f"{greeting} Я помогу подобрать льготы и меры поддержки, на которые "
        "вы можете претендовать.\n\n"
        "Отвечу на пару вопросов кнопками и покажу список того, что вам "
        "положено, с описанием, порядком оформления и списком документов.\n\n"
        "Для начала: кто вы?"
    )


def benefits_count_phrase(n: int) -> str:
    """«1 подходящую льготу», «3 подходящие льготы», «7 подходящих льгот»:
    число и согласованные с ним слова в винительном падеже."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} подходящую льготу"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} подходящие льготы"
    return f"{n} подходящих льгот"


# ── Полезные нагрузки кнопок (шаги диалога кодируются прямо в кнопке) ───────

class CategoryPayload(CallbackPayload):
    category: str


class RegionPayload(CallbackPayload):
    category: str
    region: str


class DetailPayload(CallbackPayload):
    # category и region нужны только для кнопки «Назад к списку»: у самой
    # льготы регион может быть "all", а вернуться надо к выбору пользователя.
    benefit_id: str
    category: str
    region: str


class LegacyDetailPayload(CallbackPayload, prefix="DetailPayload"):
    """Старый формат кнопки льготы (только benefit_id). Такие кнопки остались
    в сообщениях, отправленных до появления category и region в DetailPayload.
    Число полей не совпадает, поэтому DetailPayload их не распознаёт."""

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
        builder.row(
            _button(
                f"ℹ️ {benefit.title}",
                DetailPayload(benefit_id=benefit_id, category=category, region=region),
            )
        )
    builder.row(_button("⬅ Начать заново", RestartPayload()))
    return [builder.as_markup()]


def detail_keyboard(category: str, region: str):
    builder = InlineKeyboardBuilder()
    builder.row(_button("⬅ Назад к списку", RegionPayload(category=category, region=region)))
    builder.row(_button("⬅ Начать заново", RestartPayload()))
    return [builder.as_markup()]


# ── Вспомогательные функции для ответов на нажатия ───────────────────────

async def _ack(event: MessageCallback, client: Bot) -> None:
    """Подтверждает нажатие кнопки, чтобы у пользователя не висел индикатор
    загрузки. Отправляем только короткое уведомление: голый event.answer()
    в maxapi 1.0.0 заодно пересылает исходное сообщение с text=None и его
    вложениями. Сбой подтверждения не должен блокировать сам ответ."""
    try:
        await client.send_callback(callback_id=event.callback.callback_id, notification="Секунду…")
    except Exception:
        log.warning("Не удалось подтвердить нажатие кнопки", exc_info=True)


async def _send(event: MessageCallback, client: Bot, text: str, attachments) -> None:
    """Отправляет ответ на нажатие новым сообщением. Если исходное сообщение
    с кнопкой уже удалено (event.message is None), остаётся только личка
    пользователю. Её могут не принять (например, диалог с ботом ни разу не
    открывали), тогда фиксируем это в логе и не роняем хэндлер."""
    if event.message is not None:
        await event.message.answer(text=text, attachments=attachments)
        return
    try:
        await client.send_message(user_id=event.callback.user.user_id, text=text, attachments=attachments)
    except Exception:
        log.warning("Исходное сообщение удалено, а в личку ответить не удалось", exc_info=True)


async def _reply(event: MessageCallback, text: str, attachments) -> None:
    """Отвечает на нажатие кнопки: подтверждение нажатия и само сообщение
    уходят параллельно, чтобы подтверждение не добавляло задержку к ответу."""
    client = event.bot or bot
    await asyncio.gather(
        _ack(event, client),
        _send(event, client, text, attachments),
    )


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


@dp.message_created()
async def on_other_message(event: MessageCreated):
    """Любое сообщение, кроме /start: диалог кнопочный, свободный текст бот
    не разбирает. Вместо молчания объясняем это и возвращаем кнопки. Хэндлер
    стоит после /start: диспетчер выполняет первый подходящий. В группах и
    на сообщения других ботов не отвечаем, чтобы не засорять чат."""
    if event.message.recipient.chat_type != ChatType.DIALOG:
        return
    if event.message.sender is not None and event.message.sender.is_bot:
        return
    await event.message.answer(
        text="Я отвечаю только на кнопки и не разбираю обычный текст. Выберите, кто вы, и я подберу льготы:",
        attachments=category_keyboard(),
    )


@dp.message_callback(RestartPayload.filter())
async def on_restart(event: MessageCallback, payload: RestartPayload):
    await _reply(event, welcome_text(event.callback.user.first_name), category_keyboard())


async def _reply_stale_selection(event: MessageCallback) -> None:
    """Нажатая кнопка ссылается на категорию/регион, которых больше нет в
    базе данных (это возможно, только если benefits.json поменяли, пока
    у пользователя на экране осталась старая клавиатура, например при
    правке данных во время демо). Вместо тихого KeyError отвечаем и
    предлагаем начать заново."""
    await _reply(event, "Этот вариант больше не в базе. Начнём заново:", category_keyboard())


@dp.message_callback(CategoryPayload.filter())
async def on_category(event: MessageCallback, payload: CategoryPayload):
    category_label = CATEGORIES.get(payload.category)
    if category_label is None:
        await _reply_stale_selection(event)
        return
    await _reply(
        event,
        f"Категория: {category_label}\n\nТеперь укажите регион: часть льгот региональные.",
        region_keyboard(payload.category),
    )


@dp.message_callback(RegionPayload.filter())
async def on_region(event: MessageCallback, payload: RegionPayload):
    category_label = CATEGORIES.get(payload.category)
    region_label = REGIONS.get(payload.region)
    if category_label is None or region_label is None:
        await _reply_stale_selection(event)
        return

    matches = find_benefits(payload.category, payload.region)

    if not matches:
        await _reply(
            event,
            f"По категории «{category_label}» ({region_label}) в демо-базе пока "
            "ничего не нашлось. Это учебная база хакатона, в ней не все льготы "
            "России. Попробуйте другую категорию.",
            region_keyboard(payload.category),
        )
        return

    lines = [f"Категория: {category_label} · Регион: {region_label}", ""]
    lines.append(f"Нашёл {benefits_count_phrase(len(matches))}. Нажмите на название, чтобы узнать подробности и как оформить:")
    for b in matches:
        lines.append(f"\n• {b.title}\n  {b.summary}")

    await _reply(
        event,
        "\n".join(lines),
        results_keyboard(payload.category, payload.region, [b.id for b in matches]),
    )


@dp.message_callback(DetailPayload.filter())
async def on_detail(event: MessageCallback, payload: DetailPayload):
    await _show_benefit(event, payload.benefit_id, payload.category, payload.region)


@dp.message_callback(LegacyDetailPayload.filter())
async def on_legacy_detail(event: MessageCallback, payload: LegacyDetailPayload):
    # Выбор пользователя в старой кнопке не сохранён, поэтому «Назад» ведёт
    # к списку по категории и региону самой льготы.
    benefit = get_benefit(payload.benefit_id)
    category, region = (benefit.category, benefit.region) if benefit else ("", "")
    await _show_benefit(event, payload.benefit_id, category, region)


async def _show_benefit(event: MessageCallback, benefit_id: str, category: str, region: str) -> None:
    benefit = get_benefit(benefit_id)
    if benefit is None:
        await _reply(event, "Эта льгота больше не найдена в базе.", category_keyboard())
        return

    documents_list = "\n".join(f"  {i}. {doc}" for i, doc in enumerate(benefit.documents, 1))
    text = (
        f"📌 {benefit.title}\n\n"
        f"{benefit.description}\n\n"
        f"📚 Источник:\n{benefit.source}\n\n"
        f"✅ Как оформить:\n{benefit.how_to_apply}\n\n"
        f"📋 Какие документы понадобятся:\n{documents_list}\n\n"
        f"🕒 Данные собраны {COLLECTED_AT}. Суммы и условия могут измениться: перед подачей "
        "заявления уточните их в органе соцзащиты, СФР или на Госуслугах."
    )
    await _reply(event, text, detail_keyboard(category, region))


async def main() -> None:
    log.info("Бот-помощник по льготам запускается (long polling)...")
    attempt = 0
    try:
        while True:
            try:
                if _env_flag("MAX_DELETE_WEBHOOK"):
                    # Пока у бота есть подписка на вебхук (например, если этот
                    # токен раньше использовали с вебхуком), MAX не отдаёт
                    # обновления через polling. Удаляем подписки только по явной просьбе:
                    # тот же токен может обслуживать другое развёртывание.
                    await bot.delete_webhook()
                # Внутри start_polling диспетчер сам вызывает bot.get_me(),
                # пишет в лог имя бота и предупреждает о найденных вебхуках.
                await dp.start_polling(bot)
                return
            except InvalidToken:
                log.error("MAX отклонил токен. Проверьте MAX_BOT_TOKEN.")
                raise SystemExit(1)
            except Exception as exc:
                if not _is_retryable(exc):
                    log.exception("Не удалось запустить бота")
                    raise SystemExit(1)
                # Временный сбой: пробуем ещё раз, а не роняем контейнер сразу.
                # После старта диспетчер сам переживает сбои внутри polling, так
                # что счётчик нужен только для самого запуска.
                attempt += 1
                log.exception("Не удалось запустить бота (попытка %d из %d)", attempt, STARTUP_ATTEMPTS)
                if attempt >= STARTUP_ATTEMPTS:
                    raise SystemExit(1)
                await asyncio.sleep(STARTUP_RETRY_DELAY)
    finally:
        await bot.close_session()


if __name__ == "__main__":
    asyncio.run(main())

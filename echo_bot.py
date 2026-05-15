import asyncio
import logging
import os
import tempfile
from dotenv import load_dotenv

from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated
from maxapi.types.attachments.audio import Audio
from maxapi.types.attachments.video import Video
from maxapi.types.attachments.image import Image
from maxapi.types.attachments.file import File
from maxapi.types.attachments.sticker import Sticker
from maxapi.types.attachments.location import Location
from maxapi.types.attachments.contact import Contact
from maxapi.types.input_media import InputMediaBuffer
from maxapi.webhook.aiohttp import AiohttpMaxWebhook

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WEBHOOK_URL  = os.environ["WEBHOOK_URL"]
WEBHOOK_PATH = "/webhook"
HOST         = "0.0.0.0"
PORT         = 8081

bot = Bot()
dp  = Dispatcher()


# ── Вспомогательные функции для вложений ─────────────────────────────────────

async def download_and_reupload(att: Audio | Video | File) -> InputMediaBuffer | None:
    """
    Скачивает файл с серверов MAX через авторизованную сессию бота
    и оборачивает в InputMediaBuffer для повторной загрузки.
    Обязательно для Audio — MAX возвращает 500 при отправке аудио по токену.
    """
    payload = att.payload
    url = getattr(payload, "url", None) if payload else None
    if not url:
        logger.warning("%s не имеет URL — невозможно загрузить повторно", type(att).__name__)
        return None

    logger.info("Скачиваем %s для повторной загрузки...", type(att).__name__)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = await bot.download_file(url=url, destination=tmp)
            data = path.read_bytes()
        # InputMediaBuffer автоматически определяет тип файла (аудио/видео/изображение) по байтам
        return InputMediaBuffer(buffer=data)
    except Exception as e:
        logger.error("Ошибка при повторной загрузке %s: %s", type(att).__name__, e)
        return None


async def prepare_attachment(
    att: Audio | Video | Image | File,
) -> Audio | Video | Image | File | InputMediaBuffer | None:
    """
    Подготавливает медиавложение для пересылки.

    Аудио: MAX возвращает 500 при пересылке по токену — всегда скачиваем и загружаем заново.
    Изображение/Видео/Файл: пересылаем по токену напрямую (быстро, без скачивания).
    """
    if isinstance(att, Audio):
        # Токен аудио нельзя использовать повторно для отправки — MAX вернёт internal.error 500.
        # Нужно скачать и загрузить заново каждый раз.
        return await download_and_reupload(att)

    # Для всех остальных типов пересылаем по существующему токену
    payload = att.payload
    token   = getattr(payload, "token", None) if payload else None

    if token:
        logger.info("Пересылаем %s по токену", type(att).__name__)
        return att

    # Запасной вариант: токен отсутствует — скачиваем и загружаем заново
    logger.info("У %s нет токена — переходим к повторной загрузке", type(att).__name__)
    return await download_and_reupload(att)


def describe_attachment(att) -> str:
    """Возвращает текстовое описание типа вложения на русском языке."""
    if isinstance(att, Audio):
        if att.transcription:
            return f"🎤 Голосовое\nРасшифровка: «{att.transcription}»"
        return "🎤 Голосовое сообщение"
    elif isinstance(att, Video):
        return f"🎥 Видео ({att.duration} сек.)" if att.duration else "🎥 Видео"
    elif isinstance(att, Image):
        return "🖼 Фото"
    elif isinstance(att, File):
        name = att.filename or "файл"
        return f"📎 {name} ({att.size // 1024} КБ)" if att.size else f"📎 {name}"
    elif isinstance(att, Sticker):
        return "🩹 Стикер"
    elif isinstance(att, Location):
        return f"📍 Локация: {att.latitude}, {att.longitude}"
    elif isinstance(att, Contact):
        return "👤 Контакт"
    return f"📦 {type(att).__name__}"


# ── Обработчик эхо ────────────────────────────────────────────────────────────

@dp.message_created()
async def echo(event: MessageCreated):
    msg  = event.message
    body = msg.body
    text            = body.text        if body else None
    raw_attachments = body.attachments if body else []
    raw_attachments = raw_attachments or []

    resendable       = []
    text_only_labels = []

    for att in raw_attachments:
        if isinstance(att, (Audio, Video, Image, File)):
            prepared = await prepare_attachment(att)
            if prepared is not None:
                resendable.append(prepared)
        else:
            # Стикеры, локации, контакты — описываем текстом
            text_only_labels.append(describe_attachment(att))

    if resendable:
        # Отправляем медиа вместе с оригинальной подписью одним сообщением
        parts   = ([text] if text else []) + text_only_labels
        caption = "\n\n".join(parts) if parts else None
        await msg.answer(text=caption, attachments=resendable)

    elif text_only_labels:
        parts = ([f"📝 Эхо: {text}"] if text else []) + text_only_labels
        await msg.answer(text="\n\n".join(parts))

    elif text:
        await msg.answer(text=f"📝 Эхо: {text}")


# ── Точка входа ───────────────────────────────────────────────────────────────

async def main():
    full_url = WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH

    # Шаг 1 — удаляем старый вебхук, чтобы начать чисто
    try:
        await bot.delete_webhook()
        logger.info("Старый вебхук удалён")
    except Exception as e:
        logger.warning("Не удалось удалить старый вебхук: %s", e)

    # Шаг 2 — регистрируем наш URL вебхука в MAX
    try:
        result = await bot.subscribe_webhook(url=full_url)
        logger.info("Вебхук зарегистрирован: %s  →  результат: %s", full_url, result)
    except Exception as e:
        logger.error("ОШИБКА регистрации вебхука: %s", e)
        logger.error("Бот не может получать обновления. Проверьте WEBHOOK_URL.")
        return

    # Шаг 3 — запускаем локальный HTTP-сервер, на который MAX будет отправлять обновления
    webhook = AiohttpMaxWebhook(dp=dp, bot=bot)
    logger.info("Запускаем сервер на %s:%s%s", HOST, PORT, WEBHOOK_PATH)
    await webhook.run(host=HOST, port=PORT, path=WEBHOOK_PATH)


if __name__ == "__main__":
    asyncio.run(main())
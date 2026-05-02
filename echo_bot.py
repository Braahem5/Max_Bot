#echo_bot.py - A simple MAX bot 
import asyncio
import logging

from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated
from maxapi.types.attachments.audio import Audio
from maxapi.types.attachments.video import Video
from maxapi.types.attachments.image import Image
from maxapi.types.attachments.file import File
from maxapi.types.attachments.sticker import Sticker
from maxapi.types.attachments.location import Location
from maxapi.types.attachments.contact import Contact

logging.basicConfig(level=logging.INFO)

# Bot token is read from the MAX_BOT_TOKEN environment variable automatically.
bot = Bot()
dp = Dispatcher()


def describe_attachment(att) -> str:
    """Return a human-readable Russian label for any attachment type."""
    if isinstance(att, Audio):
        # Audio attachments may include an auto-generated transcription
        if att.transcription:
            return f"🎤 Голосовое сообщение\nРасшифровка: «{att.transcription}»"
        return "🎤 Голосовое сообщение"

    elif isinstance(att, Video):
        # Video includes regular clips and round video messages (circles)
        if att.duration:
            return f"🎥 Видео ({att.duration} сек.)"
        return "🎥 Видео"

    elif isinstance(att, Image):
        return "🖼 Фото"

    elif isinstance(att, File):
        # Show filename and size if available
        name = att.filename or "файл"
        if att.size:
            kb = att.size // 1024
            return f"📎 Файл: {name} ({kb} КБ)"
        return f"📎 Файл: {name}"

    elif isinstance(att, Sticker):
        return "🩹 Стикер"

    elif isinstance(att, Location):
        return f"📍 Локация: {att.latitude}, {att.longitude}"

    elif isinstance(att, Contact):
        return "👤 Контакт"

    else:
        # Fallback for any unknown or future attachment types
        return f"📦 Вложение ({type(att).__name__})"


@dp.message_created()
async def echo(event: MessageCreated):
    msg = event.message
    text = msg.body.text if msg.body else None

    # Attachments live inside msg.body, not directly on msg
    attachments = msg.body.attachments if msg.body else []
    attachments = attachments or []

    # Separate attachments into:
    # - resendable: media that can be forwarded back using their token
    # - labels: Russian text descriptions shown in the reply
    resendable = []
    labels = []

    for att in attachments:
        labels.append(describe_attachment(att))

        # Audio, Video, Image and File carry a token in their payload.
        # We pass them directly to answer() and the library handles the rest.
        # Stickers, Locations and Contacts are described in text only.
        if isinstance(att, (Audio, Video, Image, File)):
            resendable.append(att)

    # Build the reply text
    parts = []
    if text:
        parts.append(f"📝 Эхо: {text}")
    if labels:
        parts.append("Получил:\n" + "\n".join(f"  • {l}" for l in labels))

    reply_text = "\n\n".join(parts) if parts else None

    # Use msg.answer() instead of event.bot.send_message() to avoid
    # Pylance's "send_message is not a known attribute of None" warning,
    # since event.bot is typed as Bot | None in the library's type hints.
    if reply_text:
        await msg.answer(text=reply_text)

    # Resend media attachments separately
    if resendable:
        await msg.answer(attachments=resendable)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
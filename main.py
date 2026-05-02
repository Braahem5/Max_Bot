# main.py
import asyncio
import logging

from maxapi import Bot, Dispatcher, F
from maxapi.types import BotStarted, MessageCreated, Command
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.attachments.buttons import CallbackButton, LinkButton, ClipboardButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from maxapi.filters.callback_payload import CallbackPayload

logging.basicConfig(level=logging.INFO)

# The token is provided via the MAX_BOT_TOKEN environment variable
bot = Bot()
dp = Dispatcher()


class HelloPayload(CallbackPayload):
    """Payload for the 'Hello' button. prefix is set automatically from the class name."""
    pass


class AboutPayload(CallbackPayload):
    """Payload for the 'About' button. prefix is set automatically from the class name."""
    pass


def main_keyboard() -> list:
    """Build and return the main inline keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="👋 Привет", payload=HelloPayload().pack()),
        CallbackButton(text="ℹ️ О боте", payload=AboutPayload().pack()),
    )
    builder.row(
        LinkButton(text="🌐 MAX Мессенджер", url="https://max.ru"),
        ClipboardButton(text="📋 Скопировать код", payload="my-secret-code"),
    )
    return [builder.as_markup()]


# ── Bot started (user presses "Start" for the first time) ────────────────────

@dp.bot_started()
async def on_bot_started(event: BotStarted):
    # BotStarted has no answer() method, so we must use event.bot directly.
    # assert guarantees to Pylance that bot is not None here.
    assert event.bot is not None
    await event.bot.send_message(
        chat_id=event.chat_id,
        text="Привет! 👋\n\nОтправь /start чтобы увидеть меню.",
    )


@dp.message_created(Command("start"))
async def on_start(event: MessageCreated):
    await event.message.answer(
        text="Выбери действие:",
        attachments=main_keyboard(),
    )


@dp.message_callback(HelloPayload.filter())
async def on_hello(event: MessageCallback):
    # answer() lives on MessageCallback itself, not on event.callback (Callback model)
    await event.answer()

    # event.message is typed as Message | None, so we guard before using it
    assert event.message is not None
    await event.message.answer(
        text="Привет! 😊 Рад тебя видеть!",
        attachments=main_keyboard(),
    )



@dp.message_callback(AboutPayload.filter())
async def on_about(event: MessageCallback):
    # answer() lives on MessageCallback itself, not on event.callback (Callback model)
    await event.answer()

    # event.message is typed as Message | None, so we guard before using it
    assert event.message is not None
    await event.message.answer(
        text=(
            "ℹ️ О боте\n\n"
            "Это минимальный демо-бот на библиотеке maxapi.\n"
            "Репозиторий: github.com/love-apples/maxapi"
        ),
        attachments=main_keyboard(),
    )


@dp.message_created(F.message.body.text)
async def on_echo(event: MessageCreated):
    # The filter F.message.body.text guarantees body and text exist at runtime,
    # but Pylance doesn't know that — both are typed as Optional.
    # assert tells Pylance they are safe to access here.
    assert event.message.body is not None
    assert event.message.body.text is not None

    await event.message.answer(
        text=f"Ты написал: «{event.message.body.text}»\n\nВот меню:",
        attachments=main_keyboard(),
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
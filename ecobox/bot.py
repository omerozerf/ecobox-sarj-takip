"""Telegram front-end: who may talk to the bot there, and how replies are delivered."""

import logging
from typing import Any

from ecobox import texts
from ecobox.commands import TELEGRAM, CommandHandler, Conversation
from ecobox.telegram import TelegramClient

log = logging.getLogger(__name__)

TELEGRAM_COMMAND_PREFIX = "/"


class TelegramOutbox:
    command_prefix = TELEGRAM_COMMAND_PREFIX

    def __init__(self, telegram: TelegramClient):
        self._telegram = telegram

    def send(self, chat_id: int | str, text: str) -> None:
        self._telegram.send_message(int(chat_id), text)


class TelegramBot:
    def __init__(self, telegram: TelegramClient, commands: CommandHandler,
                 chat_id: int | None):
        self._telegram = telegram
        self._commands = commands
        self._chat_id = chat_id

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not message or "text" not in message:
            return
        chat_id = message["chat"]["id"]
        if self._chat_id is None:
            self._telegram.send_message(chat_id, texts.onboarding(chat_id))
            return
        if chat_id != self._chat_id:
            log.warning("refused message from unauthorised chat %s", chat_id)
            self._telegram.send_message(chat_id, texts.REFUSED)
            return
        conversation = Conversation(TELEGRAM, chat_id, TELEGRAM_COMMAND_PREFIX)
        self._telegram.send_message(chat_id, self._commands.reply(message["text"], conversation))

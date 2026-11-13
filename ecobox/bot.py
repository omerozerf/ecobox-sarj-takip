"""Telegram front-end: who may talk to the bot there, and how replies are delivered."""

import logging
from typing import Any

from ecobox import texts
from ecobox.commands import TELEGRAM, CommandHandler, Conversation
from ecobox.replies import Reply
from ecobox.telegram import TelegramClient, TelegramError

log = logging.getLogger(__name__)

TELEGRAM_COMMAND_PREFIX = "/"


class TelegramOutbox:
    command_prefix = TELEGRAM_COMMAND_PREFIX

    def __init__(self, telegram: TelegramClient):
        self._telegram = telegram

    def send(self, chat_id: int | str, text: str) -> None:
        self._telegram.send_message(int(chat_id), text)

    def send_reply(self, chat_id: int | str, reply: Reply) -> None:
        self.send(chat_id, reply.text)
        for image in reply.images:
            try:
                self._telegram.send_photo(int(chat_id), image.data, image.filename,
                                          image.caption)
            except TelegramError as exc:
                log.warning("Telegram photo failed: %s", exc)
                self.send(chat_id, texts.image_failed(missing_scope=False))
                return


class TelegramBot:
    def __init__(self, telegram: TelegramClient, commands: CommandHandler,
                 chat_id: int | None):
        self._telegram = telegram
        self._outbox = TelegramOutbox(telegram)
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
        self._outbox.send_reply(chat_id, self._commands.reply(message["text"], conversation))

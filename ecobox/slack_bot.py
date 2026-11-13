"""Slack front-end: a private channel holding only the owner and the bot.

On first start the bot creates the channel, invites the owner and posts the help text; the
channel id and the newest processed message ts live in the database, so a restart neither
recreates the channel nor replays old messages. Commands are plain words (`durum`), because
the Slack client swallows anything starting with `/` as a slash command.
"""

import logging
from typing import Any

from ecobox import texts
from ecobox.clock import Clock
from ecobox.commands import SLACK, CommandHandler, Conversation
from ecobox.replies import Reply
from ecobox.slack import SlackClient, SlackError, ts_key, unescape
from ecobox.storage import Storage

log = logging.getLogger(__name__)

SLACK_COMMAND_PREFIX = ""
STATE_CHANNEL_ID = "slack.channel_id"
STATE_READY = "slack.bootstrapped"
"""Set once the owner is invited and the help text is posted."""
STATE_LAST_TS = "slack.last_ts"


class SlackSetupError(RuntimeError):
    """The channel cannot be set up without the owner's intervention."""


class SlackOutbox:
    command_prefix = SLACK_COMMAND_PREFIX

    def __init__(self, slack: SlackClient):
        self._slack = slack

    def send(self, chat_id: int | str, text: str) -> None:
        self._slack.post_message(str(chat_id), text)

    def send_reply(self, chat_id: int | str, reply: Reply) -> None:
        """A failed image upload is reported in the channel instead of raised: raising would
        stop the transport (missing_scope is fatal) or replay the command after a restart."""
        if reply.text:  # a chart reply is its files alone
            self.send(chat_id, reply.text)
        for image in reply.images:
            try:
                title, _, comment = image.caption.partition("\n")
                self._slack.upload_file(str(chat_id), image.data, image.filename, title,
                                        comment)
            except SlackError as exc:
                log.warning("Slack image upload failed: %s", exc)
                self.send(chat_id, texts.image_failed(missing_scope=exc.error == "missing_scope"))
                return


class SlackBot:
    def __init__(self, slack: SlackClient, commands: CommandHandler, storage: Storage,
                 clock: Clock, owner_user_id: str, channel_name: str):
        self._slack = slack
        self._outbox = SlackOutbox(slack)
        self._commands = commands
        self._storage = storage
        self._clock = clock
        self._owner = owner_user_id
        self._channel_name = channel_name
        self._channel_id: str | None = None

    # ----- channel bootstrap ----------------------------------------------------------

    def ensure_channel(self) -> str:
        """Create → store → invite → help; each step is skipped once done, across restarts."""
        if self._channel_id is not None:
            return self._channel_id
        channel_id = self._storage.get_state(STATE_CHANNEL_ID)
        if channel_id is None:
            channel_id = self._create_channel()
        if self._storage.get_state(STATE_READY) is None:
            self._invite_owner(channel_id)
            help_ts = self._slack.post_message(channel_id, texts.help_text(SLACK_COMMAND_PREFIX))
            if self._storage.get_state(STATE_LAST_TS) is None:
                self._set(STATE_LAST_TS, help_ts)
            self._set(STATE_READY, "1")
            log.info("Slack channel %s is ready", channel_id)
        self._channel_id = channel_id
        return channel_id

    def _create_channel(self) -> str:
        try:
            channel_id = self._slack.create_private_channel(self._channel_name)
        except SlackError as exc:
            if exc.error == "name_taken":
                raise SlackSetupError(
                    f"Slack channel name '{self._channel_name}' is already taken and no channel "
                    "id is stored; choose a free SLACK_CHANNEL_NAME and restart") from None
            raise
        # Stored before the invite so that a failure further on can never lead to a second
        # create (which would only hit name_taken).
        self._set(STATE_CHANNEL_ID, channel_id)
        log.info("created private Slack channel #%s (%s)", self._channel_name, channel_id)
        return channel_id

    def _invite_owner(self, channel_id: str) -> None:
        try:
            self._slack.invite(channel_id, self._owner)
        except SlackError as exc:
            if exc.error != "already_in_channel":
                raise

    # ----- incoming messages ----------------------------------------------------------

    def poll_once(self) -> int:
        """Serve the owner's new messages; returns how many commands were answered."""
        channel_id = self.ensure_channel()
        last_ts = self._storage.get_state(STATE_LAST_TS)
        if last_ts is None:  # never replay history we have no record of handling
            last_ts = f"{self._clock.now().timestamp():.6f}"
            self._set(STATE_LAST_TS, last_ts)
        conversation = Conversation(SLACK, channel_id, SLACK_COMMAND_PREFIX)
        answered = 0
        for message in self._slack.history(channel_id, last_ts):
            if ts_key(message["ts"]) <= ts_key(last_ts):
                continue
            if self._is_owner_command(message):
                reply = self._reply(message, conversation)
                if reply is not None:
                    self._outbox.send_reply(channel_id, reply)
                    answered += 1
            last_ts = message["ts"]
            self._set(STATE_LAST_TS, last_ts)
        return answered

    def _is_owner_command(self, message: dict[str, Any]) -> bool:
        """Plain messages typed by the owner; bot posts and system subtypes are skipped."""
        return ("subtype" not in message and "bot_id" not in message
                and message.get("user") == self._owner
                and bool(message.get("text", "").strip()))

    def _reply(self, message: dict[str, Any], conversation: Conversation) -> Reply | None:
        try:
            return self._commands.reply(unescape(message["text"]), conversation)
        except Exception:  # a bad command must not block the messages after it
            log.exception("handling Slack message %s failed", message["ts"])
            return None

    def _set(self, key: str, value: str) -> None:
        self._storage.set_state(key, value, self._clock.now())

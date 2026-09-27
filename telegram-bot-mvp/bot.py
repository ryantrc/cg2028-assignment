"""Telegram polling entry point for the STM32 fall-alert MVP."""

from __future__ import annotations

import asyncio
import logging

from bot_core import BotService, BotStateStore, SerialMonitor
from config import load_config


LOGGER = logging.getLogger(__name__)


def build_application(config, service: BotService):
    try:
        from telegram import Update
        from telegram.ext import Application, CommandHandler
    except ImportError as error:
        raise RuntimeError(
            "Telegram dependencies are missing. Install requirements.txt before running the bot."
        ) from error

    async def post_init(application) -> None:
        service.attach_bot(application.bot, asyncio.get_running_loop())
        service.start()

    async def post_shutdown(application) -> None:
        service.stop()

    application = (
        Application.builder()
        .token(config.telegram_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", service.command_start))
    application.add_handler(CommandHandler("subscribe", service.command_subscribe))
    application.add_handler(CommandHandler("unsubscribe", service.command_unsubscribe))
    application.add_handler(CommandHandler("status", service.command_status))
    application.add_handler(CommandHandler("latest", service.command_latest))
    return application


def main() -> int:
    config = load_config()
    level = getattr(logging, config.log_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    state = BotStateStore(config.bot_database_path)
    monitor = None
    service = None
    application = None
    try:
        # The monitor owns the UART handle for the entire process lifetime.
        service = BotService(config, state, monitor=None)  # replaced below
        monitor = SerialMonitor(config, state, service._schedule_delivery)
        service.monitor = monitor
        application = build_application(config, service)
        application.run_polling()
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        if service is not None:
            service.stop()
        state.close()


if __name__ == "__main__":
    raise SystemExit(main())


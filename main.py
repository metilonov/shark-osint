import argparse
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.token import TokenValidationError

from bot.handlers import create_router
from bot.middleware import AdminOnlyMiddleware
from config import Settings
from core.queue import ScanQueue
from core.scanner import Scanner
from database.storage import Storage


async def cleanup_loop(storage):
    while True:
        await asyncio.sleep(3600)
        try:
            await storage.cleanup()
        except Exception as error:
            logging.error("Retention cleanup failed: %s", type(error).__name__)


async def run(settings):
    settings.validate_bot()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    bot = Bot(settings.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    storage = Storage(settings.data_dir / "shark.sqlite3", settings.retention_days)
    queue = ScanQueue(Scanner(settings), storage, settings.cooldown_seconds)
    dp = Dispatcher()
    middleware = AdminOnlyMiddleware(settings.admin_id)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.include_router(create_router(settings, storage, queue))
    cleaner = None
    try:
        await storage.open()
        me = await bot.get_me()
        logging.info("Telegram authentication verified for bot id %s", me.id)
        await queue.start()
        cleaner = asyncio.create_task(cleanup_loop(storage))
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"], close_bot_session=False)
    finally:
        await queue.close()
        if cleaner:
            cleaner.cancel()
            await asyncio.gather(cleaner, return_exceptions=True)
        await bot.session.close()
        await storage.close()


def main():
    parser = argparse.ArgumentParser(description="Shark OSINT Telegram bot")
    parser.add_argument("--check", action="store_true", help="Проверка импортов, БД, отчётов и движков без Telegram и сетевых сканов")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # aiogram and HTTP libraries may include a token-bearing request URL in debug logs.
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    try:
        settings = Settings.load()
        if args.check:
            from tools.self_check import check
            asyncio.run(check(settings))
        else:
            asyncio.run(run(settings))
    except (ValueError, TokenValidationError, TelegramAPIError, OSError) as error:
        # No exception repr: network errors can contain the Telegram token.
        if isinstance(error, ValueError) and not settings_token_present():
            print("Запуск не выполнен: заполните BOT_TOKEN и ADMIN_TELEGRAM_ID в .env. См. README.md.", file=sys.stderr)
        else:
            print(f"Запуск не выполнен ({type(error).__name__}). Проверьте .env, доступ к сети и зависимости.", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        pass


def settings_token_present():
    import os
    return bool(os.getenv("BOT_TOKEN"))


if __name__ == "__main__":
    main()

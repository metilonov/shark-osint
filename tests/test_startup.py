from dataclasses import replace
import asyncio
import os
import sys

from aiogram import Bot
from aiogram.methods import GetMe
import pytest

import main
from tests.test_bot import TelegramMockSession
from config import ROOT


async def test_application_startup_and_shutdown_mock_telegram(settings, monkeypatch):
    session = TelegramMockSession()
    monkeypatch.setattr(main, "Bot", lambda token, default: Bot(token, default=default, session=session))
    polled = []
    async def poll(dispatcher, bot, **kwargs):
        polled.append(kwargs["allowed_updates"])
    monkeypatch.setattr(main.Dispatcher, "start_polling", poll)
    await main.run(replace(settings, token="123456:TEST_TOKEN_FOR_MOCK_ONLY"))
    assert any(isinstance(call, GetMe) for call in session.calls)
    assert polled == [["message", "callback_query"]]


async def test_startup_without_token_is_clear_and_no_database(settings):
    with pytest.raises(ValueError, match="BOT_TOKEN"):
        await main.run(settings)
    assert not (settings.data_dir / "shark.sqlite3").exists()


async def test_bad_token_does_not_open_database(settings):
    from aiogram.utils.token import TokenValidationError
    with pytest.raises(TokenValidationError):
        await main.run(replace(settings, token="invalid"))
    assert not (settings.data_dir / "shark.sqlite3").exists()


async def test_entrypoint_invalid_token_exits_without_traceback():
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(ROOT / "main.py"), cwd=ROOT,
        env={**os.environ, "BOT_TOKEN": "invalid", "ADMIN_TELEGRAM_ID": "1001"},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
    assert process.returncode == 1
    assert b"Traceback" not in stderr
    assert "Запуск не выполнен" in stderr.decode()

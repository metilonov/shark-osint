import asyncio
from datetime import datetime, timezone
import itertools
import json

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetMe, SendDocument, SendMessage
from aiogram.types import Message, Update, User
import pytest
import pytest_asyncio

from bot.handlers import create_router
from bot.middleware import AdminOnlyMiddleware
from core.queue import ScanQueue
from tests.test_scanner import fake_scanner


class TelegramMockSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.ids = itertools.count(1)
        self.final = asyncio.Event()

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return User(id=123456, is_bot=True, first_name="Shark", username="shark_test_bot")
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, (SendMessage, EditMessageText, SendDocument)):
            text = getattr(method, "text", "")
            if "РЕЗУЛЬТАТЫ" in text:
                self.final.set()
            return Message(message_id=next(self.ids), date=datetime.now(timezone.utc),
                           chat={"id": int(method.chat_id), "type": "private"}, text=text).as_(bot)
        raise AssertionError(f"Unexpected Telegram method: {type(method).__name__}")


@pytest_asyncio.fixture
async def app(settings, storage, observations):
    session = TelegramMockSession()
    bot = Bot("123456:TEST_TOKEN_FOR_MOCK_ONLY", session=session, default=DefaultBotProperties(parse_mode="HTML"))
    queue = ScanQueue(fake_scanner(settings, observations[:2]), storage, 10)
    await queue.start()
    dp = Dispatcher()
    access = AdminOnlyMiddleware(settings.admin_id)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_router(create_router(settings, storage, queue))
    ids = itertools.count(1)
    async def send(text=None, callback=None, uid=1001, chat_type="private"):
        user = {"id": uid, "is_bot": False, "first_name": "Tester"}
        message = {"message_id": next(ids), "date": 1, "chat": {"id": uid, "type": chat_type}, "from": user, "text": text or "Menu"}
        if text and text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        if callback is not None:
            update = {"update_id": next(ids), "callback_query": {"id": str(next(ids)), "from": user,
                      "chat_instance": "test", "message": message, "data": callback}}
        else:
            update = {"update_id": next(ids), "message": message}
        await dp.feed_update(bot, Update.model_validate(update))
    yield send, session, queue
    await queue.close()
    await dp.fsm.close()
    await bot.session.close()


async def test_start_and_all_menu_buttons(app):
    send, session, queue = app
    await send(text="/start")
    message = session.calls[-1]
    assert "SHARK OSINT" in message.text
    assert len(message.reply_markup.inline_keyboard) == 5
    for action in ("settings", "about", "history", "reports", "menu", "search"):
        await send(callback=action)
    assert any("Введите один ваш никнейм" in (getattr(call, "text", "") or "") for call in session.calls)


async def test_search_report_profile_download_flow(app, storage):
    send, session, queue = app
    await send(callback="search")
    await send(text="bad name\nsecond")
    assert not await storage.history(1001)
    await send(text="Owner")
    await asyncio.wait_for(session.final.wait(), 5)
    rows = await storage.history(1001)
    assert len(rows) == 1
    sid = rows[0]["id"]
    assert json.loads((await storage.get_search(sid, 1001))["result_json"])["statistics"]["profiles"] == 1
    for action in (f"result:{sid}", f"profiles:{sid}:0", f"file:{sid}:html", f"file:{sid}:json", f"file:{sid}:pdf", "history", "reports", "search"):
        await send(callback=action)
    documents = [call for call in session.calls if isinstance(call, SendDocument)]
    assert len(documents) == 3
    assert {call.document.filename.rsplit('.', 1)[1] for call in documents} == {"html", "json", "pdf"}


async def test_unauthorized_and_group_access_blocked(app, storage):
    send, session, queue = app
    await send(text="/start", uid=9999)
    assert session.calls[-1].text == "SHARK OSINT · Доступ закрыт."
    await send(callback="search", uid=9999)
    assert session.calls[-1].show_alert
    count = len(session.calls)
    await send(text="/start", uid=1001, chat_type="group")
    assert len(session.calls) == count
    assert not await storage.history(9999)


async def test_tampered_foreign_callbacks_cannot_download(app, storage):
    send, session, queue = app
    sid = await storage.create_search(2222, "OtherOwner")
    for action in (f"result:{sid}", f"profiles:{sid}:0", f"file:{sid}:json", "file:invalid:exe", "profiles:invalid:bad", "stale_callback"):
        await send(callback=action)
    assert not any(isinstance(call, SendDocument) for call in session.calls)
    assert all(isinstance(call, AnswerCallbackQuery) for call in session.calls)


async def test_cancel_button_and_command(app):
    send, session, queue = app
    await send(callback="cancel:missing")
    assert session.calls[-1].show_alert
    await send(text="/cancel")
    assert "Активной проверки нет" in session.calls[-1].text


@pytest.mark.parametrize("mode", ["button", "command"])
async def test_active_search_cancel_from_telegram(app, settings, storage, mode):
    send, session, queue = app
    queue.scanner = fake_scanner(settings, block=True)
    await send(callback="search")
    await send(text="Owner")
    sid = queue.jobs[1001].sid
    if mode == "button":
        await send(callback=f"cancel:{sid}")
    else:
        await send(text="/cancel")
    await asyncio.wait_for(session.final.wait(), 5)
    assert (await storage.get_search(sid, 1001))["status"] == "cancelled"

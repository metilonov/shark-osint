import asyncio
from datetime import datetime, timezone
from html import escape
import json
import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.keyboards import back_menu, cancel_search, keyboard, main_menu, result_menu
from core.validator import validate_username

logger = logging.getLogger(__name__)
WELCOME = "<b>SHARK OSINT</b>\n\nСистема анализа публичных цифровых следов.\n\nДоступные действия:"
STATE_NAMES = {"waiting": "ожидает", "running": "выполняется", "completed": "завершён", "imported": "импортирован CSV", "failed": "ошибка",
               "disabled": "отключён", "cancelled": "отменён", "partial": "частичный результат", "queued": "в очереди", "interrupted": "прерван перезапуском"}


class SearchForm(StatesGroup):
    nickname = State()


def summary(data):
    stats = data["statistics"]
    seconds = round(data["duration_seconds"])
    return (
        "<b>SHARK OSINT — РЕЗУЛЬТАТЫ</b>\n\n"
        f"Никнейм: <code>{escape(data['username'])}</code>\n"
        f"Статус: {STATE_NAMES.get(data['status'], data['status'])}\n\n"
        f"Проверено платформ: {stats['checked_platforms']}\n"
        f"Найдено профилей: {stats['profiles']}\n"
        f"Подтверждённых доступных страниц (HTTP 2xx): {stats['accessible_pages']}\n"
        f"Сомнительных совпадений: {stats['uncertain_pages']}\n"
        f"Совпадений нескольких источников: {stats['multi_source_matches']}\n"
        f"Время анализа: {seconds // 60} мин {seconds % 60} с\n\n"
        + "\n".join(f"{name}: {STATE_NAMES.get(status, status)}" for name, status in data['engines'].items())
        + f"\nОшибок / уведомлений: {len(data['errors'])}\n\n"
        "Доступность страницы не подтверждает личность владельца. Все совпадения требуют ручной проверки."
    )


class ProgressMessage:
    def __init__(self, message):
        self.message = message
        self.sid = ""
        self.latest = None
        self.started = time.monotonic()
        self.retry_at = 0
        self.task = None

    async def receive(self, data):
        self.latest = data

    async def run(self):
        while True:
            await asyncio.sleep(3)
            if not self.latest or time.monotonic() < self.retry_at:
                continue
            data = self.latest
            seconds = int(time.monotonic() - self.started)
            text = (f"<b>🔎 SHARK OSINT</b>\n\nНикнейм: <code>{escape(data['username'])}</code>\n\n"
                    + "\n".join(f"{name}: {STATE_NAMES.get(state, state)}" for name, state in data['engines'].items())
                    + f"\n\nНайдено публичных профилей: {data['found']}\n"
                    f"Получено результатов проверок: {data['checks']}\nВремя выполнения: {seconds // 60:02d}:{seconds % 60:02d}")
            try:
                await self.message.edit_text(text, reply_markup=cancel_search(self.sid))
            except TelegramRetryAfter as error:
                self.retry_at = time.monotonic() + error.retry_after
            except TelegramAPIError as error:
                logger.info("Progress delivery: %s", type(error).__name__)

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


def create_router(settings, storage, queue):
    router = Router()

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext):
        await state.clear()
        await message.answer(WELCOME, reply_markup=main_menu())

    @router.message(Command("cancel"))
    async def cancel_command(message: Message, state: FSMContext):
        await state.clear()
        job = queue.jobs.get(message.from_user.id)
        if job:
            queue.cancel(message.from_user.id, job.sid)
            await message.answer("Отмена запрошена. Частичные результаты будут сохранены.")
        else:
            await message.answer("Активной проверки нет.", reply_markup=main_menu())

    @router.callback_query(F.data.in_({"menu", "search", "history", "reports", "settings", "about"}))
    async def menu(callback: CallbackQuery, state: FSMContext):
        await callback.answer()
        await state.clear()
        action = callback.data
        if action == "menu":
            await callback.message.answer(WELCOME, reply_markup=main_menu())
        elif action == "search":
            if callback.from_user.id in queue.jobs:
                await callback.message.answer("У вас уже есть активная проверка. Для отмены: /cancel")
                return
            await state.set_state(SearchForm.nickname)
            await callback.message.answer("<b>ПОИСК ПО НИКУ</b>\n\nВведите один ваш никнейм или ник владельца, разрешившего проверку.\nПример: <code>Shadow_777</code>\n\nМассовые проверки, телефоны, адреса и ссылки не принимаются.", reply_markup=back_menu())
        elif action in {"history", "reports"}:
            rows = await storage.history(callback.from_user.id)
            if not rows:
                await callback.message.answer("История пуста.", reply_markup=back_menu())
                return
            buttons = []
            lines = ["<b>ИСТОРИЯ ПРОВЕРОК</b>" if action == "history" else "<b>МОИ ОТЧЁТЫ</b>"]
            for row in rows:
                date = datetime.fromtimestamp(row["created_at"], timezone.utc).strftime("%d.%m %H:%M UTC")
                lines.append(f"{escape(row['username'])} · {STATE_NAMES.get(row['status'], row['status'])} · {date}")
                buttons.append([(row["username"], f"result:{row['id']}")])
            buttons.append([("Главное меню", "menu")])
            await callback.message.answer("\n\n".join(lines), reply_markup=keyboard(buttons))
        elif action == "settings":
            await callback.message.answer(
                f"<b>НАСТРОЙКИ</b>\n\nХранение: {settings.retention_days} дней\n"
                f"Лимит платформ на движок: {settings.site_limit}\nПроцессов одновременно: {settings.max_processes}\n"
                f"Соединений на движок: {settings.connections}\nЛимит памяти на процесс: {settings.memory_mb} МБ\n"
                f"Таймаут запроса: {settings.request_timeout} с\nТаймаут движка: {settings.engine_timeout} с\n"
                f"Пауза между проверками: {settings.cooldown_seconds} с\n\n"
                "Параметры задаются в локальном .env и применяются после перезапуска.\nДоступ разрешён только администратору в личном чате.", reply_markup=back_menu())
        else:
            await callback.message.answer("<b>SHARK OSINT · Shark Team</b>\n\nSherlock + Maigret + независимый необязательный SpiderFoot.\n"
                                          "Только публичные страницы и разрешённые исследования. Совпадение ника не доказывает общность владельца.\n"
                                          "Отчёты хранятся локально и передаются только администратору в Telegram.", reply_markup=back_menu())

    @router.message(SearchForm.nickname, F.text)
    async def nickname(message: Message, state: FSMContext):
        try:
            username = validate_username(message.text)
        except ValueError as error:
            await message.answer(str(error))
            return
        progress_message = await message.answer("SHARK OSINT · Подготовка проверки…")
        progress = ProgressMessage(progress_message)
        async def finished(result):
            await progress.stop()
            if result is None:
                await progress_message.edit_text("Ошибка проверки. Смотрите историю и локальные журналы.", reply_markup=back_menu())
            else:
                await progress_message.edit_text(summary(result.to_dict()), reply_markup=result_menu(result.search_id))
        try:
            sid = await queue.submit(message.from_user.id, username, progress.receive, finished)
        except ValueError as error:
            await progress_message.edit_text(str(error), reply_markup=back_menu())
            return
        progress.sid = sid
        progress.task = asyncio.create_task(progress.run())
        await state.clear()
        # Avoid racing a fast failed scan's final message with an initial edit.
        # The queued message is sent before queue.submit; subsequent edits belong to progress/final.

    @router.callback_query(F.data.startswith("cancel:"))
    async def cancel(callback: CallbackQuery):
        sid = callback.data.split(":", 1)[1]
        found = queue.cancel(callback.from_user.id, sid)
        await callback.answer("Отмена запрошена" if found else "Проверка уже завершена", show_alert=True)

    @router.callback_query(F.data.startswith("result:"))
    async def result(callback: CallbackQuery):
        row = await storage.get_search(callback.data.split(":", 1)[1], callback.from_user.id)
        if not row:
            await callback.answer("Отчёт недоступен или срок хранения истёк", show_alert=True)
            return
        await callback.answer()
        if row["result_json"]:
            await callback.message.answer(summary(json.loads(row["result_json"])), reply_markup=result_menu(row["id"]))
        else:
            await callback.message.answer(f"Статус: {STATE_NAMES.get(row['status'], row['status'])}. Готового отчёта нет.", reply_markup=back_menu())

    @router.callback_query(F.data.startswith("file:"))
    async def download(callback: CallbackQuery):
        parts = callback.data.split(":")
        if len(parts) != 3 or parts[2] not in {"html", "json", "pdf"}:
            await callback.answer("Неверный формат", show_alert=True)
            return
        _, sid, fmt = parts
        content = await storage.get_report(sid, callback.from_user.id, fmt)
        if content is None:
            await callback.answer("Отчёт недоступен или срок хранения истёк", show_alert=True)
            return
        await callback.answer()
        await callback.message.answer_document(BufferedInputFile(content, filename=f"shark-{sid[:8]}.{fmt}"))

    @router.callback_query(F.data.startswith("profiles:"))
    async def profiles(callback: CallbackQuery):
        parts = callback.data.split(":")
        if len(parts) != 3 or not parts[2].isdigit():
            await callback.answer("Неверная страница", show_alert=True)
            return
        _, sid, page = parts
        row = await storage.get_search(sid, callback.from_user.id)
        if not row or not row["result_json"]:
            await callback.answer("Отчёт недоступен", show_alert=True)
            return
        await callback.answer()
        data = json.loads(row["result_json"])
        items = data["profiles"]
        page = min(int(page), max(0, (len(items) - 1) // 4))
        lines = [f"<b>ПРОФИЛИ · {escape(data['username'])}</b>"]
        for item in items[page * 4:page * 4 + 4]:
            sources = ", ".join(sorted({s["engine"] for s in item["sources"]}))
            url = escape(item["url"], quote=True)
            label = escape(item["url"][:240])
            lines.append(f"<b>{escape(item['platform'])}</b>\n<a href=\"{url}\">{label}</a>\n"
                         f"{escape(sources)} · Уверенность: {escape(item['confidence'])}\nВозможное ложное совпадение: да")
        if not items:
            lines.append("Профили не найдены. Проверьте статусы движков и ошибки в отчёте.")
        nav = []
        if page > 0:
            nav.append(("←", f"profiles:{sid}:{page-1}"))
        if (page + 1) * 4 < len(items):
            nav.append(("→", f"profiles:{sid}:{page+1}"))
        rows = ([nav] if nav else []) + [[("К отчёту", f"result:{sid}")]]
        await callback.message.answer("\n\n".join(lines), reply_markup=keyboard(rows), disable_web_page_preview=True)

    @router.callback_query()
    async def unknown_callback(callback: CallbackQuery):
        await callback.answer("Кнопка устарела. Используйте /start.")

    @router.message()
    async def fallback(message: Message):
        await message.answer("Используйте /start для меню или /cancel для отмены проверки.", reply_markup=main_menu())

    return router

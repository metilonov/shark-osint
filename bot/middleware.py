from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message


class AdminOnlyMiddleware(BaseMiddleware):
    def __init__(self, admin_id):
        self.admin_id = admin_id

    async def __call__(self, handler, event, data):
        user = event.from_user
        message = event.message if isinstance(event, CallbackQuery) else event
        private = isinstance(message, Message) and message.chat.type == "private"
        if not user or user.id != self.admin_id or not private:
            if isinstance(event, CallbackQuery):
                await event.answer("Доступ закрыт", show_alert=True)
            elif private:
                await event.answer("SHARK OSINT · Доступ закрыт.")
            return None
        return await handler(event, data)

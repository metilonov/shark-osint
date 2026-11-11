from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows
    ])


def main_menu():
    return keyboard([[('🔎 Поиск по нику', 'search')], [('📊 История проверок', 'history')],
                     [('📁 Мои отчёты', 'reports')], [('⚙️ Настройки', 'settings')], [('ℹ️ О системе', 'about')]])


def back_menu():
    return keyboard([[('Главное меню', 'menu')]])


def cancel_search(sid):
    return keyboard([[('Отменить поиск', f'cancel:{sid}')]])


def result_menu(sid):
    return keyboard([[('Просмотреть профили', f'profiles:{sid}:0')],
                     [('Скачать HTML', f'file:{sid}:html'), ('Скачать JSON', f'file:{sid}:json')],
                     [('Скачать PDF', f'file:{sid}:pdf')], [('Новый поиск', 'search')], [('Главное меню', 'menu')]])

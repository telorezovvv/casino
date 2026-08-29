import asyncio
import random
import logging
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from collections import defaultdict

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
import sqlite3

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8830027608:AAFr-5t2m9AKN-C9LUDb6zwr81CYFpw2p_I"
ADMIN_IDS = [7572622307]  # Ваши ID
BOT_NAME = "🎰 Group Casino"
CURRENCY = "🪙"
CHANNEL_URL = "https://t.me/+5slr_856RjtkNmEy"  # Ссылка на ваш канал

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self, db_name="casino.db"):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.init_db()

    def init_db(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 1000,
                total_won INTEGER DEFAULT 0,
                total_lost INTEGER DEFAULT 0,
                games_played INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0
            )
        """)
        self.conn.commit()

    def get_user(self, user_id: int, username: str = None) -> Dict:
        self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        result = self.cursor.fetchone()
        if not result:
            if not username:
                username = str(user_id)
            self.cursor.execute(
                "INSERT INTO users (user_id, username, balance, clicks) VALUES (?, ?, ?, ?)",
                (user_id, username, 1000, 0)
            )
            self.conn.commit()
            return {"user_id": user_id, "username": username, "balance": 1000, 
                    "total_won": 0, "total_lost": 0, "games_played": 0, "clicks": 0}
        return {
            "user_id": result[0],
            "username": result[1] or str(result[0]),
            "balance": result[2],
            "total_won": result[3],
            "total_lost": result[4],
            "games_played": result[5],
            "clicks": result[6] if len(result) > 6 else 0
        }

    def update_balance(self, user_id: int, amount: int):
        self.cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        self.conn.commit()

    def update_username(self, user_id: int, username: str):
        self.cursor.execute(
            "UPDATE users SET username = ? WHERE user_id = ?",
            (username, user_id)
        )
        self.conn.commit()

    def add_game_stats(self, user_id: int, won: int, lost: int):
        self.cursor.execute(
            "UPDATE users SET total_won = total_won + ?, total_lost = total_lost + ?, games_played = games_played + 1 WHERE user_id = ?",
            (won, lost, user_id)
        )
        self.conn.commit()

    def add_click(self, user_id: int):
        self.cursor.execute(
            "UPDATE users SET clicks = clicks + 1 WHERE user_id = ?",
            (user_id,)
        )
        self.conn.commit()

    def get_top_balance(self, limit: int = 10) -> List[Tuple]:
        self.cursor.execute(
            "SELECT user_id, username, balance FROM users ORDER BY balance DESC LIMIT ?",
            (limit,)
        )
        return self.cursor.fetchall()

    def get_top_clicks(self, limit: int = 10) -> List[Tuple]:
        self.cursor.execute(
            "SELECT user_id, username, clicks FROM users ORDER BY clicks DESC LIMIT ?",
            (limit,)
        )
        return self.cursor.fetchall()

    def get_all_users(self) -> List[Tuple]:
        self.cursor.execute("SELECT user_id, username, balance FROM users")
        return self.cursor.fetchall()

db = Database()

# ========== FSM ==========
class BetStates(StatesGroup):
    waiting_for_custom_bet = State()

class BlackJackStates(StatesGroup):
    playing = State()

# ========== БОТ ==========
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ========== ХРАНИЛИЩЕ СООБЩЕНИЙ ДЛЯ КЛИКЕРА ==========
click_messages = defaultdict(list)  # {user_id: [message_id, message_id, ...]}

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def format_balance(balance: int) -> str:
    return f"{CURRENCY} {balance:,}".replace(",", " ")

def get_username(user_id: int, username: str = None) -> str:
    """Получить юзернейм или создать на основе ID"""
    if username and username != str(user_id):
        return f"@{username}"
    return f"ID{user_id}"

def create_main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⚽ Футбол", callback_data="game_football"),
        InlineKeyboardButton(text="🏀 Кольцо", callback_data="game_basketball"),
    )
    builder.row(
        InlineKeyboardButton(text="🎲 Кубик", callback_data="game_dice"),
        InlineKeyboardButton(text="🎰 Слоты", callback_data="game_slots"),
    )
    builder.row(
        InlineKeyboardButton(text="🃏 Black Jack", callback_data="game_blackjack"),
    )
    builder.row(
        InlineKeyboardButton(text="🖱️ Кликер", callback_data="game_clicker"),
    )
    builder.row(
        InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
        InlineKeyboardButton(text="🏆 Топ", callback_data="top"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 Баланс", callback_data="balance"),
        InlineKeyboardButton(text="📢 Канал", url=CHANNEL_URL),
    )
    return builder.as_markup()

def create_bet_keyboard(game: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    amounts = [50, 100, 250, 500, 1000]
    for amount in amounts:
        builder.add(InlineKeyboardButton(
            text=f"{format_balance(amount)}", 
            callback_data=f"bet_{game}_{amount}"
        ))
    builder.adjust(3, 2)
    builder.row(
        InlineKeyboardButton(text="✏️ Своя сумма", callback_data=f"bet_custom_{game}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")
    )
    return builder.as_markup()

def create_dice_choice_keyboard(game: str, bet: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🟢 Четное", callback_data=f"dice_even_{game}_{bet}"),
        InlineKeyboardButton(text="🔴 Нечетное", callback_data=f"dice_odd_{game}_{bet}")
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_menu")
    )
    return builder.as_markup()

def create_clicker_menu() -> InlineKeyboardMarkup:
    """Меню кликера"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🖱️ КЛИК! (+1 монета)", callback_data="click"),
    )
    builder.row(
        InlineKeyboardButton(text="🏆 Топ кликеров", callback_data="top_clicks"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")
    )
    return builder.as_markup()

def create_private_menu() -> InlineKeyboardMarkup:
    """Красивое меню для личных сообщений"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎰 Начать игру", callback_data="start_game"),
    )
    builder.row(
        InlineKeyboardButton(text="📢 Наш канал", url=CHANNEL_URL),
    )
    builder.row(
        InlineKeyboardButton(text="ℹ️ О боте", callback_data="about_bot"),
    )
    return builder.as_markup()

def create_welcome_menu() -> InlineKeyboardMarkup:
    """Приветственное меню при добавлении в группу"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎰 Начать играть", callback_data="start_game_from_welcome"),
    )
    builder.row(
        InlineKeyboardButton(text="📢 Наш канал", url=CHANNEL_URL),
    )
    builder.row(
        InlineKeyboardButton(text="ℹ️ О боте", callback_data="about_bot_from_welcome"),
    )
    return builder.as_markup()

def create_blackjack_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для Black Jack"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🃏 Взять карту", callback_data="bj_hit"),
        InlineKeyboardButton(text="✋ Хватит", callback_data="bj_stand"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Выйти", callback_data="back_to_menu")
    )
    return builder.as_markup()

# ========== ОБРАБОТЧИК ДОБАВЛЕНИЯ БОТА В ЧАТ ==========
@dp.my_chat_member()
async def on_bot_added(event: ChatMemberUpdated):
    """Обработчик добавления бота в чат"""
    if event.new_chat_member.status in ["member", "administrator"]:
        chat_id = event.chat.id
        chat_title = event.chat.title or "Группа"
        
        if event.chat.type in ["group", "supergroup"]:
            await bot.send_message(
                chat_id,
                f"🎰 Привет, {chat_title}!\n\n"
                f"Я {BOT_NAME} - бот для веселых игр с друзьями!\n\n"
                f"🎮 Доступные игры:\n"
                f"⚽ Футбол - забей гол!\n"
                f"🏀 Кольцо - попади в кольцо!\n"
                f"🎲 Кубик - угадай чет/нечет!\n"
                f"🎰 Слоты - крути барабаны!\n"
                f"🃏 Black Jack - играй до 21!\n"
                f"🖱️ Кликер - зарабатывай монеты кликами!\n\n"
                f"💰 Каждому новому игроку выдается {format_balance(1000)}!\n\n"
                f"📌 Для начала игры используйте команду:\n"
                f"<code>/GroupCasino</code>\n\n"
                f"Подпишись на наш канал, чтобы быть в курсе обновлений!",
                reply_markup=create_welcome_menu(),
                parse_mode="HTML"
            )
            
            logger.info(f"Бот добавлен в чат: {chat_title} (ID: {chat_id})")

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@dp.message(Command("GroupCasino"))
async def cmd_group_casino(message: Message):
    """Главная команда для запуска в группах"""
    user = db.get_user(message.from_user.id, message.from_user.username or str(message.from_user.id))
    
    if message.from_user.username:
        db.update_username(message.from_user.id, message.from_user.username)
    
    await message.answer(
        f"🎰 Добро пожаловать в {BOT_NAME}!\n\n"
        f"👤 Игрок: {get_username(message.from_user.id, message.from_user.username)}\n"
        f"💰 Ваш баланс: {format_balance(user['balance'])}\n\n"
        f"Выберите игру:",
        reply_markup=create_main_menu()
    )

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Команда для личных сообщений"""
    if message.chat.type == "private":
        await message.answer(
            f"🎰 Добро пожаловать в {BOT_NAME}!\n\n"
            f"👋 Привет! Я бот-казино для групповых игр.\n\n"
            f"📌 Чтобы начать играть в группе, используй команду:\n"
            f"<code>/GroupCasino</code>\n\n"
            f"🎮 Доступные игры:\n"
            f"⚽ Футбол - забей гол!\n"
            f"🏀 Кольцо - попади в кольцо!\n"
            f"🎲 Кубик - угадай чет/нечет!\n"
            f"🎰 Слоты - крути барабаны!\n"
            f"🃏 Black Jack - играй до 21!\n"
            f"🖱️ Кликер - зарабатывай монеты кликами!\n\n"
            f"💰 Начальный баланс: {format_balance(1000)}\n\n"
            f"Подпишись на наш канал, чтобы быть в курсе обновлений!",
            reply_markup=create_private_menu(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"❌ Используйте команду /GroupCasino для начала игры в этой группе!"
        )

@dp.message(Command("topcasino"))
async def cmd_top(message: Message):
    """Топ игроков по балансу"""
    top_users = db.get_top_balance(10)
    if not top_users:
        await message.answer("📊 Пока нет игроков в топе!")
        return
    
    text = "🏆 ТОП ИГРОКОВ ПО БАЛАНСУ 🏆\n\n"
    medals = ["🥇", "🥈", "🥉"]
    
    for i, (user_id, username, balance) in enumerate(top_users):
        medal = medals[i] if i < 3 else f"{i+1}."
        user_display = get_username(user_id, username)
        text += f"{medal} {user_display}: {format_balance(balance)}\n"
    
    await message.answer(text)

@dp.message(Command("topclicks"))
async def cmd_top_clicks(message: Message):
    """Топ кликеров"""
    top_users = db.get_top_clicks(10)
    if not top_users:
        await message.answer("📊 Пока нет кликеров в топе!")
        return
    
    text = "🖱️ ТОП КЛИКЕРОВ 🖱️\n\n"
    medals = ["🥇", "🥈", "🥉"]
    
    for i, (user_id, username, clicks) in enumerate(top_users):
        medal = medals[i] if i < 3 else f"{i+1}."
        user_display = get_username(user_id, username)
        text += f"{medal} {user_display}: {clicks} кликов\n"
    
    await message.answer(text)

@dp.message(Command("givemoney"))
async def cmd_give_money(message: Message, command: CommandObject):
    """Выдача монет админом"""
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("❌ У вас нет прав на эту команду!")
        return
    
    args = command.args
    if not args:
        await message.reply("ℹ️ Использование: /givemoney @username 100")
        return
    
    parts = args.split()
    if len(parts) != 2:
        await message.reply("ℹ️ Использование: /givemoney @username 100")
        return
    
    username = parts[0].replace("@", "")
    try:
        amount = int(parts[1])
        if amount <= 0:
            await message.reply("❌ Сумма должна быть больше 0!")
            return
    except ValueError:
        await message.reply("❌ Сумма должна быть числом!")
        return
    
    all_users = db.get_all_users()
    found = False
    for user_id, user_name, balance in all_users:
        if user_name and user_name.lower() == username.lower():
            db.update_balance(user_id, amount)
            await message.reply(
                f"✅ Выдано {format_balance(amount)} пользователю @{username}\n"
                f"Новый баланс: {format_balance(balance + amount)}"
            )
            found = True
            break
    
    if not found:
        await message.reply(f"❌ Пользователь @{username} не найден в базе!")

@dp.message(Command("giveme"))
async def cmd_give_me(message: Message, command: CommandObject):
    """Выдача монет себе (только для админов)"""
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("❌ У вас нет прав на эту команду!")
        return
    
    args = command.args
    if not args:
        await message.reply("ℹ️ Использование: /giveme 100")
        return
    
    try:
        amount = int(args)
        if amount <= 0:
            await message.reply("❌ Сумма должна быть больше 0!")
            return
    except ValueError:
        await message.reply("❌ Введите число!")
        return
    
    db.update_balance(message.from_user.id, amount)
    user = db.get_user(message.from_user.id)
    
    await message.reply(
        f"✅ Выдано {format_balance(amount)} себе!\n"
        f"💰 Новый баланс: {format_balance(user['balance'])}"
    )

# ========== ОБРАБОТЧИКИ CALLBACK ==========
@dp.callback_query(F.data == "start_game_from_welcome")
async def start_game_from_welcome(callback: CallbackQuery):
    """Кнопка начала игры из приветственного сообщения"""
    await callback.message.delete()
    user = db.get_user(callback.from_user.id, callback.from_user.username or str(callback.from_user.id))
    
    await callback.message.answer(
        f"🎰 Добро пожаловать в {BOT_NAME}!\n\n"
        f"👤 Игрок: {get_username(callback.from_user.id, callback.from_user.username)}\n"
        f"💰 Ваш баланс: {format_balance(user['balance'])}\n\n"
        f"Выберите игру:",
        reply_markup=create_main_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "about_bot_from_welcome")
async def about_bot_from_welcome(callback: CallbackQuery):
    """О боте из приветственного сообщения"""
    await callback.message.delete()
    await callback.message.answer(
        f"🤖 О боте {BOT_NAME}\n\n"
        f"Версия: 2.0.0\n"
        f"Разработчик: @mainfucking\n\n"
        f"📌 Особенности:\n"
        f"• 6 увлекательных игр\n"
        f"• Анимированные эмодзи\n"
        f"• Система баланса\n"
        f"• Топ игроков\n"
        f"• Кликер для заработка\n"
        f"• Black Jack\n"
        f"• Работает в любых группах\n\n"
        f"💡 Для начала игры используйте команду:\n"
        f"<code>/GroupCasino</code>\n\n"
        f"🔗 Подпишись на наш канал для новостей!",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📢 Наш канал", url=CHANNEL_URL)],
                [InlineKeyboardButton(text="🎰 Начать играть", callback_data="start_game_from_welcome")]
            ]
        ),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "start_game")
async def start_game_from_private(callback: CallbackQuery):
    """Кнопка начала игры из личных сообщений"""
    await callback.message.delete()
    await callback.message.answer(
        f"🎰 Чтобы начать играть, добавьте бота в группу и используйте команду:\n"
        f"<code>/GroupCasino</code>\n\n"
        f"Или перейдите в группу по ссылке:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Пригласить бота в группу", url="https://t.me/GroupCasinoBot?startgroup=true")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_private")]
            ]
        ),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "about_bot")
async def about_bot(callback: CallbackQuery):
    """О боте"""
    await callback.message.delete()
    await callback.message.answer(
        f"🤖 О боте {BOT_NAME}\n\n"
        f"Версия: 2.0.0\n"
        f"Разработчик: @ваш_ник\n\n"
        f"📌 Особенности:\n"
        f"• 6 увлекательных игр\n"
        f"• Анимированные эмодзи\n"
        f"• Система баланса\n"
        f"• Топ игроков\n"
        f"• Кликер для заработка\n"
        f"• Black Jack\n"
        f"• Работает в любых группах\n\n"
        f"💡 Для начала игры в группе используйте команду:\n"
        f"<code>/GroupCasino</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_private")]
            ]
        ),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_private")
async def back_to_private(callback: CallbackQuery):
    """Назад в приватное меню"""
    await callback.message.delete()
    await callback.message.answer(
        f"🎰 Добро пожаловать в {BOT_NAME}!\n\n"
        f"👋 Привет! Я бот-казино для групповых игр.\n\n"
        f"📌 Чтобы начать играть в группе, используй команду:\n"
        f"<code>/GroupCasino</code>\n\n"
        f"🎮 Доступные игры:\n"
        f"⚽ Футбол - забей гол!\n"
        f"🏀 Кольцо - попади в кольцо!\n"
        f"🎲 Кубик - угадай чет/нечет!\n"
        f"🎰 Слоты - крути барабаны!\n"
        f"🃏 Black Jack - играй до 21!\n"
        f"🖱️ Кликер - зарабатывай монеты кликами!\n\n"
        f"💰 Начальный баланс: {format_balance(1000)}\n\n"
        f"Подпишись на наш канал, чтобы быть в курсе обновлений!",
        reply_markup=create_private_menu(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    """Назад в главное меню"""
    await state.clear()
    user = db.get_user(callback.from_user.id)
    await callback.message.delete()
    await callback.message.answer(
        f"🎰 Добро пожаловать в {BOT_NAME}!\n\n"
        f"👤 Игрок: {get_username(callback.from_user.id, callback.from_user.username)}\n"
        f"💰 Ваш баланс: {format_balance(user['balance'])}\n\n"
        f"Выберите игру:",
        reply_markup=create_main_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    """Показать профиль"""
    user = db.get_user(callback.from_user.id)
    await callback.message.delete()
    await callback.message.answer(
        f"👤 Ваш профиль\n\n"
        f"🆔 ID: {callback.from_user.id}\n"
        f"👤 Имя: {get_username(callback.from_user.id, callback.from_user.username)}\n"
        f"💰 Баланс: {format_balance(user['balance'])}\n"
        f"🏆 Выигрышей: {format_balance(user['total_won'])}\n"
        f"💸 Проигрышей: {format_balance(user['total_lost'])}\n"
        f"🎮 Игр сыграно: {user['games_played']}\n"
        f"🖱️ Кликов сделано: {user['clicks']}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]]
        )
    )
    await callback.answer()

@dp.callback_query(F.data == "balance")
async def show_balance(callback: CallbackQuery):
    """Показать баланс"""
    user = db.get_user(callback.from_user.id)
    await callback.message.delete()
    await callback.message.answer(
        f"💰 Ваш баланс\n\n"
        f"Баланс: {format_balance(user['balance'])}\n"
        f"Всего выиграно: {format_balance(user['total_won'])}\n"
        f"Всего проиграно: {format_balance(user['total_lost'])}\n"
        f"Игр сыграно: {user['games_played']}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]]
        )
    )
    await callback.answer()

@dp.callback_query(F.data == "top")
async def show_top(callback: CallbackQuery):
    """Показать топ по балансу"""
    top_users = db.get_top_balance(10)
    if not top_users:
        await callback.message.delete()
        await callback.message.answer(
            "📊 Пока нет игроков в топе!",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]]
            )
        )
        await callback.answer()
        return
    
    text = "🏆 ТОП ИГРОКОВ ПО БАЛАНСУ 🏆\n\n"
    medals = ["🥇", "🥈", "🥉"]
    
    for i, (user_id, username, balance) in enumerate(top_users):
        medal = medals[i] if i < 3 else f"{i+1}."
        user_display = get_username(user_id, username)
        text += f"{medal} {user_display}: {format_balance(balance)}\n"
    
    await callback.message.delete()
    await callback.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🖱️ Топ кликеров", callback_data="top_clicks")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
            ]
        )
    )
    await callback.answer()

@dp.callback_query(F.data == "top_clicks")
async def show_top_clicks(callback: CallbackQuery):
    """Показать топ кликеров"""
    top_users = db.get_top_clicks(10)
    if not top_users:
        await callback.message.delete()
        await callback.message.answer(
            "📊 Пока нет кликеров в топе!",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]]
            )
        )
        await callback.answer()
        return
    
    text = "🖱️ ТОП КЛИКЕРОВ 🖱️\n\n"
    medals = ["🥇", "🥈", "🥉"]
    
    for i, (user_id, username, clicks) in enumerate(top_users):
        medal = medals[i] if i < 3 else f"{i+1}."
        user_display = get_username(user_id, username)
        text += f"{medal} {user_display}: {clicks} кликов\n"
    
    await callback.message.delete()
    await callback.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🏆 Топ по балансу", callback_data="top")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
            ]
        )
    )
    await callback.answer()

# ========== КЛИКЕР ==========
@dp.callback_query(F.data == "game_clicker")
async def start_clicker(callback: CallbackQuery):
    """Запуск кликера"""
    user = db.get_user(callback.from_user.id)
    
    await callback.message.delete()
    await callback.message.answer(
        f"🖱️ КЛИКЕР 🖱️\n\n"
        f"💰 Ваш баланс: {format_balance(user['balance'])}\n"
        f"🖱️ Кликов сделано: {user['clicks']}\n\n"
        f"Нажимайте на кнопку и получайте по +1 монете!\n"
        f"Чем больше кликов, тем больше монет!",
        reply_markup=create_clicker_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "click")
async def handle_click(callback: CallbackQuery):
    """Обработка клика"""
    user_id = callback.from_user.id
    username = callback.from_user.username or str(user_id)
    
    # Обновляем баланс и статистику
    db.update_balance(user_id, 1)
    db.add_click(user_id)
    
    user = db.get_user(user_id)
    
    # Создаем сообщение о клике
    click_text = f"🖱️ {get_username(user_id, username)} нажал на кнопку!\n💰 +1 монета!\n💎 Баланс: {format_balance(user['balance'])}"
    
    # Отправляем сообщение
    sent_msg = await callback.message.answer(click_text)
    
    # Сохраняем ID сообщения для пользователя
    click_messages[user_id].append(sent_msg.message_id)
    
    # Если сообщений больше 5, удаляем самые старые
    if len(click_messages[user_id]) > 5:
        oldest_msg_id = click_messages[user_id].pop(0)
        try:
            await bot.delete_message(callback.message.chat.id, oldest_msg_id)
        except:
            pass
    
    # Анимируем кнопку
    await callback.answer("🖱️ +1 монета!", show_alert=False)
    
    # Обновляем меню кликера с новым балансом
    await callback.message.edit_text(
        f"🖱️ КЛИКЕР 🖱️\n\n"
        f"💰 Ваш баланс: {format_balance(user['balance'])}\n"
        f"🖱️ Кликов сделано: {user['clicks']}\n\n"
        f"Нажимайте на кнопку и получайте по +1 монете!\n"
        f"Чем больше кликов, тем больше монет!",
        reply_markup=create_clicker_menu()
    )

# ========== BLACK JACK ==========
# Хранилище игр Black Jack
bj_games = {}

class BlackJackGame:
    def __init__(self, user_id: int, bet: int):
        self.user_id = user_id
        self.bet = bet
        self.user_cards = []
        self.bot_cards = []
        self.user_score = 0
        self.bot_score = 0
        self.finished = False
        self.message_id = None
        
        # Начальная раздача
        self.user_cards = [self.get_card(), self.get_card()]
        self.bot_cards = [self.get_card(), self.get_card()]
        self.update_scores()
    
    def get_card(self) -> int:
        """Получить случайную карту (2-11)"""
        card = random.randint(1, 11)
        # Если выпал туз, даем 11
        if card == 1:
            return 11
        return min(card, 10)  # 10, J, Q, K = 10
    
    def update_scores(self):
        """Обновить очки"""
        self.user_score = sum(self.user_cards)
        self.bot_score = sum(self.bot_cards)
        
        # Корректировка тузов
        while self.user_score > 21 and 11 in self.user_cards:
            idx = self.user_cards.index(11)
            self.user_cards[idx] = 1
            self.user_score = sum(self.user_cards)
        
        while self.bot_score > 21 and 11 in self.bot_cards:
            idx = self.bot_cards.index(11)
            self.bot_cards[idx] = 1
            self.bot_score = sum(self.bot_cards)
    
    def hit(self) -> bool:
        """Взять карту"""
        if self.finished:
            return False
        
        self.user_cards.append(self.get_card())
        self.update_scores()
        
        if self.user_score > 21:
            self.finished = True
            return False
        return True
    
    def stand(self):
        """Закончить игру"""
        if self.finished:
            return
        
        # Бот добирает карты до 17+
        while self.bot_score < 17:
            self.bot_cards.append(self.get_card())
            self.update_scores()
        
        self.finished = True
    
    def get_result(self) -> tuple:
        """Получить результат игры"""
        if not self.finished:
            return None, None
        
        # Проверяем перебор
        if self.user_score > 21:
            return "lose", 0
        if self.bot_score > 21:
            return "win", self.bet * 2
        
        # Сравниваем очки
        if self.user_score > self.bot_score:
            return "win", self.bet * 2
        elif self.user_score < self.bot_score:
            return "lose", 0
        else:
            return "draw", self.bet  # Возвращаем ставку
    
    def get_display(self) -> str:
        """Получить отображение игры"""
        user_cards_str = " + ".join(map(str, self.user_cards))
        bot_cards_str = " + ".join(map(str, self.bot_cards))
        
        if self.finished:
            bot_display = f"🎴 {bot_cards_str} = {self.bot_score}"
        else:
            bot_display = f"🎴 {self.bot_cards[0]} + ?"
        
        return (
            f"🃏 BLACK JACK 🃏\n\n"
            f"💰 Ставка: {format_balance(self.bet)}\n\n"
            f"👤 Ваши карты:\n{user_cards_str} = {self.user_score}\n\n"
            f"🤖 Карты бота:\n{bot_display}\n"
        )

@dp.callback_query(F.data == "game_blackjack")
async def start_blackjack(callback: CallbackQuery):
    """Начало игры в Black Jack"""
    await callback.message.delete()
    await callback.message.answer(
        f"🃏 BLACK JACK 🃏\n\n"
        f"💰 Ваш баланс: {format_balance(db.get_user(callback.from_user.id)['balance'])}\n\n"
        f"Выберите сумму ставки:",
        reply_markup=create_bet_keyboard("blackjack")
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("bet_"))
async def place_bet(callback: CallbackQuery, state: FSMContext):
    """Размещение ставки"""
    parts = callback.data.split("_")
    game = parts[1]
    amount = parts[2]
    
    if amount == "custom":
        await callback.message.delete()
        await callback.message.answer(
            "✏️ Введите сумму ставки (число):",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_menu")]]
            )
        )
        await state.set_state(BetStates.waiting_for_custom_bet)
        await state.update_data(game=game)
        await callback.answer()
        return
    
    bet = int(amount)
    user = db.get_user(callback.from_user.id)
    
    if bet > user["balance"]:
        await callback.answer(f"❌ Недостаточно средств! Баланс: {format_balance(user['balance'])}", show_alert=True)
        return
    
    # Для кубика нужен выбор чет/нечет
    if game == "dice":
        await callback.message.delete()
        await callback.message.answer(
            f"🎲 Ставка: {format_balance(bet)}\n\n"
            f"Выберите: четное или нечетное?",
            reply_markup=create_dice_choice_keyboard(game, bet)
        )
        await callback.answer()
        return
    
    # Для Black Jack своя логика
    if game == "blackjack":
        await callback.message.delete()
        await start_blackjack_game(callback.message, callback.from_user.id, bet)
        await callback.answer()
        return
    
    # Для остальных игр сразу кидаем
    await callback.message.delete()
    await play_animated_game(callback.message, callback.from_user.id, game, bet, None)

async def start_blackjack_game(message: Message, user_id: int, bet: int):
    """Запуск игры Black Jack"""
    # Создаем игру
    game = BlackJackGame(user_id, bet)
    bj_games[user_id] = game
    
    # Отправляем сообщение с игрой
    sent_msg = await message.answer(
        game.get_display(),
        reply_markup=create_blackjack_keyboard()
    )
    game.message_id = sent_msg.message_id

@dp.callback_query(F.data == "bj_hit")
async def blackjack_hit(callback: CallbackQuery):
    """Взять карту в Black Jack"""
    user_id = callback.from_user.id
    
    if user_id not in bj_games:
        await callback.answer("❌ Игра не найдена!", show_alert=True)
        return
    
    game = bj_games[user_id]
    
    if game.finished:
        await callback.answer("❌ Игра уже завершена!", show_alert=True)
        return
    
    # Берем карту
    game.hit()
    
    # Проверяем перебор
    if game.user_score > 21:
        game.finished = True
        await finish_blackjack_game(callback.message, user_id)
        return
    
    # Обновляем сообщение
    await callback.message.edit_text(
        game.get_display(),
        reply_markup=create_blackjack_keyboard()
    )
    await callback.answer("🃏 Карта взята!")

@dp.callback_query(F.data == "bj_stand")
async def blackjack_stand(callback: CallbackQuery):
    """Закончить игру в Black Jack"""
    user_id = callback.from_user.id
    
    if user_id not in bj_games:
        await callback.answer("❌ Игра не найдена!", show_alert=True)
        return
    
    game = bj_games[user_id]
    
    if game.finished:
        await callback.answer("❌ Игра уже завершена!", show_alert=True)
        return
    
    # Заканчиваем игру
    game.stand()
    await finish_blackjack_game(callback.message, user_id)

async def finish_blackjack_game(message: Message, user_id: int):
    """Завершить игру Black Jack"""
    game = bj_games.get(user_id)
    if not game:
        return
    
    # Получаем результат
    result, winnings = game.get_result()
    
    # Обновляем баланс
    user = db.get_user(user_id)
    if result == "win":
        db.update_balance(user_id, winnings - game.bet)
        db.add_game_stats(user_id, winnings, 0)
        result_text = f"🎉 ВЫ ПОБЕДИЛИ! 🎉\n💰 Выигрыш: {format_balance(winnings)}"
    elif result == "lose":
        db.update_balance(user_id, -game.bet)
        db.add_game_stats(user_id, 0, game.bet)
        result_text = f"😔 ВЫ ПРОИГРАЛИ! 😔\n💸 Потеряно: {format_balance(game.bet)}"
    else:  # draw
        db.update_balance(user_id, 0)
        result_text = f"🤝 НИЧЬЯ! 🤝\n💰 Ставка возвращена: {format_balance(game.bet)}"
    
    user = db.get_user(user_id)
    result_text += f"\n💎 Новый баланс: {format_balance(user['balance'])}"
    
    # Обновляем сообщение
    display = game.get_display()
    await message.edit_text(
        f"{display}\n\n{result_text}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🃏 Играть еще", callback_data="game_blackjack")],
                [InlineKeyboardButton(text="🎰 Главное меню", callback_data="back_to_menu")]
            ]
        )
    )
    
    # Удаляем игру
    del bj_games[user_id]

# ========== ОБРАБОТЧИКИ ИГР ==========
@dp.callback_query(F.data.startswith("game_"))
async def select_game(callback: CallbackQuery, state: FSMContext):
    """Выбор игры"""
    game = callback.data.split("_")[1]
    
    # Если выбрали кликер или Black Jack, они обрабатываются отдельно
    if game == "clicker":
        await start_clicker(callback)
        return
    if game == "blackjack":
        await start_blackjack(callback)
        return
    
    game_names = {
        "football": "⚽ Футбол",
        "basketball": "🏀 Кольцо",
        "dice": "🎲 Кубик",
        "slots": "🎰 Слоты"
    }
    
    await state.clear()
    
    await callback.message.delete()
    await callback.message.answer(
        f"🎮 {game_names.get(game, game)}\n\n"
        f"💰 Ваш баланс: {format_balance(db.get_user(callback.from_user.id)['balance'])}\n\n"
        f"Выберите сумму ставки:",
        reply_markup=create_bet_keyboard(game)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("dice_"))
async def dice_choice(callback: CallbackQuery):
    """Выбор чет/нечет для кубика"""
    parts = callback.data.split("_")
    choice = parts[1]  # even или odd
    game = parts[2]
    bet = int(parts[3])
    
    user = db.get_user(callback.from_user.id)
    if bet > user["balance"]:
        await callback.answer(f"❌ Недостаточно средств! Баланс: {format_balance(user['balance'])}", show_alert=True)
        await callback.message.delete()
        return
    
    await callback.message.delete()
    await play_animated_game(callback.message, callback.from_user.id, game, bet, choice)

async def play_animated_game(message: Message, user_id: int, game: str, bet: int, choice: Optional[str] = None):
    """Запуск анимированной игры"""
    
    emoji_map = {
        "football": "⚽",
        "basketball": "🏀",
        "dice": "🎲",
        "slots": "🎰"
    }
    
    # Отправляем анимированный эмодзи
    try:
        sent_message = await message.answer_dice(emoji=emoji_map[game])
    except Exception as e:
        logger.error(f"Ошибка отправки dice: {e}")
        await message.answer("❌ Ошибка при запуске игры!")
        return
    
    # Ждем завершения анимации
    await asyncio.sleep(4)
    
    # Получаем результат
    result_value = sent_message.dice.value
    
    # Определяем результат
    if game == "football":
        win = result_value >= 4
        if win:
            winnings = bet * 2
            result_text = "⚽⚽⚽ ГООООЛ! ⚽⚽⚽\n\n🏆 ВЫ ПОБЕДИЛИ! 🏆"
        else:
            winnings = 0
            result_text = "😱 МИМО! 😱\n\nПовезет в следующий раз!"
    
    elif game == "basketball":
        win = result_value >= 4
        if win:
            winnings = bet * 2
            result_text = "🏀🏀🏀 ПОПАДАНИЕ! 🏀🏀🏀\n\n🏆 ВЫ ПОБЕДИЛИ! 🏆"
        else:
            winnings = 0
            result_text = "😅 МИМО КОЛЬЦА! 😅\n\nВ следующий раз повезет!"
    
    elif game == "dice":
        is_even = result_value % 2 == 0
        user_choice_even = choice == "even"
        
        win = is_even == user_choice_even
        if win:
            winnings = bet * 2
            result_text = f"🎲🎲🎲 ВЫПАЛО: {result_value} 🎲🎲🎲\n\n"
            result_text += "✅ ВЫ УГАДАЛИ!\n🏆 ПОБЕДА!"
        else:
            winnings = 0
            result_text = f"🎲🎲🎲 ВЫПАЛО: {result_value} 🎲🎲🎲\n\n"
            result_text += "❌ ВЫ НЕ УГАДАЛИ!\n😔 ПОВЕЗЕТ В СЛЕДУЮЩИЙ РАЗ!"
    
    elif game == "slots":
        win = result_value > 50
        if win:
            multiplier = random.choice([2, 2, 3, 3, 5, 5, 10])
            winnings = bet * multiplier
            result_text = f"🎰🎰🎰 ДЖЕКПОТ! 🎰🎰🎰\n\n"
            result_text += f"🏆 ВЫ ВЫИГРАЛИ x{multiplier}!"
        else:
            winnings = 0
            result_text = "🎰🎰🎰 НЕ ПОВЕЗЛО... 🎰🎰🎰\n\n"
            result_text += "😕 В СЛЕДУЮЩИЙ РАЗ!"
    
    else:
        win = False
        winnings = 0
        result_text = "❌ Ошибка игры!"
    
    # Обновляем баланс
    user = db.get_user(user_id)
    if win:
        db.update_balance(user_id, winnings - bet)
        db.add_game_stats(user_id, winnings, 0)
        result_text += f"\n\n💰 Выигрыш: {format_balance(winnings)}"
    else:
        db.update_balance(user_id, -bet)
        db.add_game_stats(user_id, 0, bet)
        result_text += f"\n\n💸 Потеряно: {format_balance(bet)}"
    
    result_text += f"\n💎 Новый баланс: {format_balance(db.get_user(user_id)['balance'])}"
    
    # Отправляем результат
    await message.answer(
        result_text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎮 Играть еще", callback_data=f"game_{game}")],
                [InlineKeyboardButton(text="🎰 Главное меню", callback_data="back_to_menu")]
            ]
        )
    )

# ========== ОБРАБОТКА КАСТОМНЫХ СТАВОК ==========
@dp.message(BetStates.waiting_for_custom_bet)
async def process_custom_bet(message: Message, state: FSMContext):
    """Обработка кастомной ставки"""
    data = await state.get_data()
    game = data.get("game")
    
    try:
        bet = int(message.text)
        if bet <= 0:
            await message.reply("❌ Сумма должна быть больше 0!")
            return
    except ValueError:
        await message.reply("❌ Введите число!")
        return
    
    user = db.get_user(message.from_user.id)
    if bet > user["balance"]:
        await message.reply(f"❌ Недостаточно средств! Баланс: {format_balance(user['balance'])}")
        return
    
    await state.clear()
    await message.delete()
    
    # Для кубика нужен выбор
    if game == "dice":
        await message.answer(
            f"🎲 Ставка: {format_balance(bet)}\n\n"
            f"Выберите: четное или нечетное?",
            reply_markup=create_dice_choice_keyboard(game, bet)
        )
        return
    
    # Для Black Jack
    if game == "blackjack":
        await start_blackjack_game(message, message.from_user.id, bet)
        return
    
    await play_animated_game(message, message.from_user.id, game, bet, None)

# ========== ЗАПУСК ==========
async def main():
    logger.info(f"Запуск {BOT_NAME}...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

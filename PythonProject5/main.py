import asyncio
import random
import logging
from datetime import datetime
from typing import Optional, Dict, List, Tuple

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import IS_MEMBER, IS_NOT_MEMBER
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
                games_played INTEGER DEFAULT 0
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
                "INSERT INTO users (user_id, username, balance) VALUES (?, ?, ?)",
                (user_id, username, 1000)
            )
            self.conn.commit()
            return {"user_id": user_id, "username": username, "balance": 1000,
                    "total_won": 0, "total_lost": 0, "games_played": 0}
        return {
            "user_id": result[0],
            "username": result[1] or str(result[0]),
            "balance": result[2],
            "total_won": result[3],
            "total_lost": result[4],
            "games_played": result[5]
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

    def get_top_balance(self, limit: int = 10) -> List[Tuple]:
        self.cursor.execute(
            "SELECT user_id, username, balance FROM users ORDER BY balance DESC LIMIT ?",
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


# ========== БОТ ==========
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def format_balance(balance: int) -> str:
    return f"{CURRENCY} {balance:,}".replace(",", " ")


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


# ========== ОБРАБОТЧИК ДОБАВЛЕНИЯ БОТА В ЧАТ ==========
@dp.my_chat_member()
async def on_bot_added(event: ChatMemberUpdated):
    """Обработчик добавления бота в чат"""
    if event.new_chat_member.status == "member" or event.new_chat_member.status == "administrator":
        # Бота добавили в чат или дали права
        chat_id = event.chat.id
        chat_title = event.chat.title or "Группа"

        # Проверяем, что это группа
        if event.chat.type in ["group", "supergroup"]:
            await bot.send_message(
                chat_id,
                f"🎰 Привет, {chat_title}!\n\n"
                f"Я {BOT_NAME} - бот для веселых игр с друзьями!\n\n"
                f"🎮 Доступные игры:\n"
                f"⚽ Футбол - забей гол!\n"
                f"🏀 Кольцо - попади в кольцо!\n"
                f"🎲 Кубик - угадай чет/нечет!\n"
                f"🎰 Слоты - крути барабаны!\n\n"
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

    # Обновляем юзернейм если изменился
    if message.from_user.username:
        db.update_username(message.from_user.id, message.from_user.username)

    await message.answer(
        f"🎰 Добро пожаловать в {BOT_NAME}!\n\n"
        f"👤 Игрок: @{message.from_user.username or 'без юзернейма'}\n"
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
            f"🎰 Слоты - крути барабаны!\n\n"
            f"💰 Начальный баланс: {format_balance(1000)}\n\n"
            f"Подпишись на наш канал, чтобы быть в курсе обновлений!",
            reply_markup=create_private_menu(),
            parse_mode="HTML"
        )
    else:
        # Если в группе написали /start, направляем на /GroupCasino
        await message.answer(
            f"❌ Используйте команду /GroupCasino для начала игры в этой группе!"
        )


@dp.message(Command("topcasino"))
async def cmd_top(message: Message):
    """Топ игроков"""
    top_users = db.get_top_balance(10)
    if not top_users:
        await message.answer("📊 Пока нет игроков в топе!")
        return

    text = "🏆 ТОП ИГРОКОВ КАЗИНО 🏆\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for i, (user_id, username, balance) in enumerate(top_users):
        medal = medals[i] if i < 3 else f"{i + 1}."
        user_display = f"@{username}" if username and username != str(user_id) else f"ID{user_id}"
        text += f"{medal} {user_display}: {format_balance(balance)}\n"

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


# ========== ОБРАБОТЧИКИ CALLBACK ==========
@dp.callback_query(F.data == "start_game_from_welcome")
async def start_game_from_welcome(callback: CallbackQuery):
    """Кнопка начала игры из приветственного сообщения"""
    await callback.message.delete()
    user = db.get_user(callback.from_user.id, callback.from_user.username or str(callback.from_user.id))

    await callback.message.answer(
        f"🎰 Добро пожаловать в {BOT_NAME}!\n\n"
        f"👤 Игрок: @{callback.from_user.username or 'без юзернейма'}\n"
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
        f"• 4 увлекательные игры\n"
        f"• Анимированные эмодзи\n"
        f"• Система баланса\n"
        f"• Топ игроков\n"
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
        f"• 4 увлекательные игры\n"
        f"• Анимированные эмодзи\n"
        f"• Система баланса\n"
        f"• Топ игроков\n"
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
        f"🎰 Слоты - крути барабаны!\n\n"
        f"💰 Начальный баланс: {format_balance(1000)}\n\n"
        f"Подпишись на наш канал, чтобы быть в курсе обновлений!",
        reply_markup=create_private_menu(),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    """Назад в главное меню"""
    user = db.get_user(callback.from_user.id)
    await callback.message.delete()
    await callback.message.answer(
        f"🎰 Добро пожаловать в {BOT_NAME}!\n\n"
        f"👤 Игрок: @{callback.from_user.username or 'без юзернейма'}\n"
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
        f"👤 Имя: @{callback.from_user.username or 'без юзернейма'}\n"
        f"💰 Баланс: {format_balance(user['balance'])}\n"
        f"🏆 Выигрышей: {format_balance(user['total_won'])}\n"
        f"💸 Проигрышей: {format_balance(user['total_lost'])}\n"
        f"🎮 Игр сыграно: {user['games_played']}",
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
    """Показать топ"""
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

    text = "🏆 ТОП ИГРОКОВ КАЗИНО 🏆\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for i, (user_id, username, balance) in enumerate(top_users):
        medal = medals[i] if i < 3 else f"{i + 1}."
        user_display = f"@{username}" if username and username != str(user_id) else f"ID{user_id}"
        text += f"{medal} {user_display}: {format_balance(balance)}\n"

    await callback.message.delete()
    await callback.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]]
        )
    )
    await callback.answer()


# ========== ОБРАБОТЧИКИ ИГР ==========
@dp.callback_query(F.data.startswith("game_"))
async def select_game(callback: CallbackQuery, state: FSMContext):
    """Выбор игры"""
    game = callback.data.split("_")[1]
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

    # Для остальных игр сразу кидаем
    await callback.message.delete()
    await play_animated_game(callback.message, callback.from_user.id, game, bet, None)


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

    await play_animated_game(message, message.from_user.id, game, bet, None)


# ========== ЗАПУСК ==========
async def main():
    logger.info(f"Запуск {BOT_NAME}...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
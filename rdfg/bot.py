import asyncio
import sqlite3
import aiohttp
import os
import logging
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN", "твой_токен_заглушка")
API_URL = os.getenv("API_URL", "https://твоя-ссылка.ngrok-free.dev/v1/chat/completions")
ADMIN_ID = 5760639200

MODELS = ["qwen3:4b", "deepseek-coder:6.7b", "llama3:8b", "qwen3:1.7b"]

# --- БАЗА ДАННЫХ ---
db = sqlite3.connect("simple_bot.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, model TEXT, status TEXT)")
db.commit()

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- КЛАВИАТУРЫ ---
def get_main_menu(user_id):
    kb = ReplyKeyboardBuilder()
    kb.button(text="🤖 Выбрать модель")
    kb.button(text="💬 Начать чат")
    if user_id == ADMIN_ID:
        kb.button(text="🛠 Админка")
    return kb.adjust(2).as_markup(resize_keyboard=True)

def render_progress_bar(percent):
    """Генерирует визуальную полоску загрузки"""
    percent = min(percent, 100) # Чтобы не ушло за 100
    length = 10
    filled_length = int(length * percent // 100)
    bar = "█" * filled_length + "░" * (length - filled_length)
    return f"⏳ **ИИ генерирует ответ...**\n`[{bar}]` {int(percent)}%"

# --- ЛОГИКА АДМИНКИ ---

@dp.message(F.text == "🛠 Админка")
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    users = db.execute("SELECT id FROM users WHERE status = 'approved' AND id != ?", (ADMIN_ID,)).fetchall()
    if not users:
        await message.answer("Активных пользователей нет.")
        return
    kb = InlineKeyboardBuilder()
    for user in users:
        uid = user[0]
        kb.button(text=f"❌ Удалить {uid}", callback_data=f"delete_{uid}")
    await message.answer("Выберите пользователя для удаления:", reply_markup=kb.adjust(1).as_markup())

@dp.callback_query(F.data.startswith("delete_"))
async def delete_user(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    uid_to_delete = int(call.data.split("_")[1])
    db.execute("DELETE FROM users WHERE id = ?", (uid_to_delete,))
    db.commit()
    await call.answer("Удалено")
    await call.message.edit_text(f"Доступ для {uid_to_delete} закрыт.")
    try:
        await bot.send_message(uid_to_delete, "🚫 Доступ аннулирован.", reply_markup=types.ReplyKeyboardRemove())
    except: pass

# --- ОСНОВНАЯ ЛОГИКА ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    res = db.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()
    if user_id == ADMIN_ID:
        db.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?)", (user_id, MODELS[0], "approved"))
        db.commit()
        await message.answer("Привет, Админ!", reply_markup=get_main_menu(user_id))
        return
    if not res:
        db.execute("INSERT INTO users VALUES (?, ?, ?)", (user_id, MODELS[0], "pending"))
        db.commit()
        await message.answer("Заявка отправлена.")
        kb = InlineKeyboardBuilder().button(text="✅ Одобрить", callback_data=f"approve_{user_id}")
        await bot.send_message(ADMIN_ID, f"Новый юзер: {user_id}", reply_markup=kb.as_markup())
    elif res[0] == "approved":
        await message.answer("Доступ есть!", reply_markup=get_main_menu(user_id))

@dp.callback_query(F.data.startswith("approve_"))
async def approve_user(call: types.CallbackQuery):
    user_to_approve = int(call.data.split("_")[1])
    db.execute("UPDATE users SET status = 'approved' WHERE id = ?", (user_to_approve,))
    db.commit()
    await bot.send_message(user_to_approve, "✅ Доступ открыт!", reply_markup=get_main_menu(user_to_approve))
    await call.message.edit_text(f"Юзер {user_to_approve} добавлен.")

@dp.message(F.text == "🤖 Выбрать модель")
async def choose_model(message: types.Message):
    kb = InlineKeyboardBuilder()
    for m in MODELS: kb.button(text=m, callback_data=f"set_{m}")
    await message.answer("Выбери нейросеть:", reply_markup=kb.adjust(1).as_markup())

@dp.callback_query(F.data.startswith("set_"))
async def set_model(call: types.CallbackQuery):
    new_model = call.data.split("_")[1]
    db.execute("UPDATE users SET model = ? WHERE id = ?", (new_model, call.from_user.id))
    db.commit()
    await call.message.edit_text(f"✅ Установлена модель: {new_model}")

@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_data = db.execute("SELECT model, status FROM users WHERE id = ?", (user_id,)).fetchone()

    if not user_data or user_data[1] != "approved":
        await message.answer("У вас нет доступа.")
        return

    if message.text in ["🤖 Выбрать модель", "💬 Начать чат", "🛠 Админка"]:
        return

    status_msg = await message.answer(render_progress_bar(5), parse_mode="Markdown")
    await bot.send_chat_action(message.chat.id, "typing")

    payload = {
        "model": user_data[0],
        "messages": [{"role": "user", "content": message.text}],
        "stream": True # ВКЛЮЧАЕМ СТРИМИНГ
    }

    full_answer = ""
    chunk_count = 0

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json=payload, timeout=300) as response:
                if response.status != 200:
                    await status_msg.edit_text(f"Ошибка API: {response.status}")
                    return

                # Читаем поток данных от ИИ
                async for chunk in response.content:
                    if chunk:
                        line = chunk.decode("utf-8").strip()
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]": break
                            
                            try:
                                data = json.loads(data_str)
                                content = data['choices'][0]['delta'].get('content', '')
                                full_answer += content
                                chunk_count += 1
                                
                                # Обновляем полоску каждые 15 кусочков текста, чтобы не спамить Telegram API
                                if chunk_count % 15 == 0:
                                    # Рассчитываем примерный прогресс (чисто визуально на основе накопления текста)
                                    # так как точный размер ответа ИИ заранее не знает
                                    progress = min(95, chunk_count // 2) 
                                    try:
                                        await status_msg.edit_text(render_progress_bar(progress), parse_mode="Markdown")
                                    except: pass
                            except: continue

                # Завершение
                await status_msg.edit_text(render_progress_bar(100), parse_mode="Markdown")
                await status_msg.delete()
                
                if full_answer:
                    await message.answer(full_answer)
                else:
                    await message.answer("ИИ прислал пустой ответ.")

    except Exception as e:
        logging.error(f"Error: {e}")
        await status_msg.edit_text("⚠️ Ошибка связи с нейросетью.")

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

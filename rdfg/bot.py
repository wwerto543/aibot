import asyncio
import sqlite3
import requests
import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


TOKEN = os.getenv("BOT_TOKEN", "твой_токен_заглушка")
API_URL = os.getenv("API_URL", "https://твоя-ссылка.ngrok-free.dev/v1/chat/completions")
ADMIN_ID = 5760639200


MODELS = ["qwen3:4b", "deepseek-coder:6.7b", "llama3:8b", "qwen3:1.7b"]


db = sqlite3.connect("simple_bot.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, model TEXT, status TEXT)")
db.commit()

bot = Bot(token=TOKEN)
dp = Dispatcher()



def get_main_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🤖 Выбрать модель")
    kb.button(text="💬 Начать чат")
    return kb.adjust(2).as_markup(resize_keyboard=True)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    res = db.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()

    if user_id == ADMIN_ID:
        db.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?)", (user_id, MODELS[0], "approved"))
        db.commit()
        await message.answer("Привет, Админ! Бот готов.", reply_markup=get_main_menu())
        return

    if not res:
        db.execute("INSERT INTO users VALUES (?, ?, ?)", (user_id, MODELS[0], "pending"))
        db.commit()
        await message.answer("Заявка отправлена. Ждите одобрения админом.")
        # Уведомление админу
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Одобрить", callback_data=f"approve_{user_id}")
        await bot.send_message(ADMIN_ID, f"Новый юзер: {message.from_user.full_name} (ID: {user_id})",
                               reply_markup=kb.as_markup())
    elif res[0] == "approved":
        await message.answer("Доступ есть! Можешь общаться.", reply_markup=get_main_menu())
    else:
        await message.answer("Ваша заявка еще на рассмотрении.")



@dp.callback_query(F.data.startswith("approve_"))
async def approve_user(call: types.CallbackQuery):
    user_to_approve = int(call.data.split("_")[1])
    db.execute("UPDATE users SET status = 'approved' WHERE id = ?", (user_to_approve,))
    db.commit()
    await call.answer("Пользователь одобрен!")
    await bot.send_message(user_to_approve, "✅ Админ одобрил ваш доступ! Теперь используйте меню.",
                           reply_markup=get_main_menu())
    await call.message.edit_text(f"Юзер {user_to_approve} успешно добавлен.")



@dp.message(F.text == "🤖 Выбрать модель")
async def choose_model(message: types.Message):
    kb = InlineKeyboardBuilder()
    for m in MODELS:
        kb.button(text=m, callback_data=f"set_{m}")
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

    if message.text in ["🤖 Выбрать модель", "💬 Начать чат"]:
        return


    await bot.send_chat_action(message.chat.id, "typing")


    try:
        payload = {
            "model": user_data[0],
            "messages": [{"role": "user", "content": message.text}],
            "stream": False
        }

        response = requests.post(API_URL, json=payload, timeout=300)

        if response.status_code == 200:
            result = response.json()
            answer = result['choices'][0]['message']['content']
            await message.answer(answer)
        else:
            await message.answer(f"Ошибка сервера: {response.status_code}")

    except Exception as e:
        await message.answer(f"Произошла ошибка: {str(e)}\n\nПроверь, запущен ли ngrok и сервер ИИ.")



async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
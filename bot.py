import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# ================= CONFIG =================

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан")

DATA_FILE = Path("data.json")

# ================= STORAGE =================

def load_data():
    try:
        if DATA_FILE.exists():
            return json.loads(DATA_FILE.read_text("utf-8"))
    except Exception as e:
        print("LOAD ERROR:", e)
    return {}

def save_data(data):
    try:
        DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        print("SAVE ERROR:", e)

def get_user(data, uid):
    uid = str(uid)
    if uid not in data:
        data[uid] = {"vitamins": [], "log": []}
    return data[uid]

# ================= FSM =================

class AddVitamin(StatesGroup):
    name = State()
    dose = State()
    time = State()

# ================= UTILS =================

def parse_time(text):
    text = text.lower().strip()

    aliases = {
        "утром": (8, 0),
        "обед": (13, 0),
        "вечером": (20, 0),
        "ночью": (22, 0),
    }

    if text in aliases:
        return aliases[text]

    m = re.match(r"(\d{1,2})[:.](\d{2})", text)
    if m:
        return int(m.group(1)), int(m.group(2))

    if text.isdigit():
        return int(text), 0

    return None

def time_str(h, m):
    return f"{h:02d}:{m:02d}"

# ================= BOT =================

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ================= KEYBOARDS =================

def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💊 Витамины", callback_data="list"),
            InlineKeyboardButton(text="➕ Добавить", callback_data="add"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data="delete"),
        ],
    ])

def reminder_kb(vid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Выпила", callback_data=f"taken:{vid}"),
        InlineKeyboardButton(text="❌ Пропустить", callback_data=f"skip:{vid}"),
    ]])

# ================= COMMANDS =================

@dp.message(CommandStart())
async def start(msg: Message):
    await msg.answer("👋 Бот напомнит про витамины", reply_markup=main_kb())

# ================= LIST =================

@dp.callback_query(F.data == "list")
async def list_vitamins(call: CallbackQuery):
    data = load_data()
    user = get_user(data, call.from_user.id)

    if not user["vitamins"]:
        text = "Пусто"
    else:
        text = "\n".join([
            f"{i+1}. {v['name']} ({time_str(v['hour'], v['minute'])})"
            for i, v in enumerate(user["vitamins"])
        ])

    await call.message.edit_text(text, reply_markup=main_kb())

# ================= ADD =================

@dp.callback_query(F.data == "add")
async def add_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AddVitamin.name)
    await call.message.answer("Название витамина?")

@dp.message(AddVitamin.name)
async def add_name(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await state.set_state(AddVitamin.dose)
    await msg.answer("Доза?")

@dp.message(AddVitamin.dose)
async def add_dose(msg: Message, state: FSMContext):
    await state.update_data(dose=msg.text)
    await state.set_state(AddVitamin.time)
    await msg.answer("Время? (например 21:00)")

@dp.message(AddVitamin.time)
async def add_time(msg: Message, state: FSMContext):
    parsed = parse_time(msg.text)
    if not parsed:
        await msg.answer("Неверное время")
        return

    h, m = parsed
    data_fsm = await state.get_data()
    await state.clear()

    data = load_data()
    user = get_user(data, msg.from_user.id)

    user["vitamins"].append({
        "id": int(datetime.now().timestamp()),
        "name": data_fsm["name"],
        "dose": data_fsm["dose"],
        "hour": h,
        "minute": m,
    })

    save_data(data)

    await msg.answer("Добавлено ✅", reply_markup=main_kb())

# ================= DELETE =================

@dp.callback_query(F.data == "delete")
async def delete_menu(call: CallbackQuery):
    data = load_data()
    user = get_user(data, call.from_user.id)

    buttons = [
        [InlineKeyboardButton(
            text=v["name"],
            callback_data=f"del:{v['id']}"
        )]
        for v in user["vitamins"]
    ]

    await call.message.edit_text(
        "Выбери:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

@dp.callback_query(F.data.startswith("del:"))
async def delete(call: CallbackQuery):
    vid = int(call.data.split(":")[1])

    data = load_data()
    user = get_user(data, call.from_user.id)

    user["vitamins"] = [v for v in user["vitamins"] if v["id"] != vid]

    save_data(data)

    await call.message.edit_text("Удалено", reply_markup=main_kb())

# ================= STATS =================

@dp.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    data = load_data()
    user = get_user(data, call.from_user.id)

    taken = len([x for x in user["log"] if x["action"] == "taken"])
    skipped = len([x for x in user["log"] if x["action"] == "skip"])

    await call.message.edit_text(
        f"✅ {taken}\n❌ {skipped}",
        reply_markup=main_kb()
    )

# ================= CALLBACKS =================

def log(uid, name, action):
    data = load_data()
    user = get_user(data, uid)

    user["log"].append({
        "action": action,
        "vitamin": name,
        "date": datetime.now().isoformat()
    })

    save_data(data)

@dp.callback_query(F.data.startswith("taken:"))
async def taken(call: CallbackQuery):
    vid = int(call.data.split(":")[1])

    data = load_data()
    user = get_user(data, call.from_user.id)

    v = next((x for x in user["vitamins"] if x["id"] == vid), None)

    if v:
        log(call.from_user.id, v["name"], "taken")

    await call.message.edit_text("Принято ✅")

@dp.callback_query(F.data.startswith("skip:"))
async def skip(call: CallbackQuery):
    vid = int(call.data.split(":")[1])

    data = load_data()
    user = get_user(data, call.from_user.id)

    v = next((x for x in user["vitamins"] if x["id"] == vid), None)

    if v:
        log(call.from_user.id, v["name"], "skip")

    await call.message.edit_text("Пропущено ❌")

# ================= SCHEDULER =================

async def scheduler():
    print("Scheduler started")

    sent = set()

    while True:
        try:
            now = datetime.now()
            data = load_data()

            for uid, user in data.items():
                for v in user["vitamins"]:
                    key = (uid, v["id"], now.date())

                    if key in sent:
                        continue

                    if now.hour == v["hour"] and now.minute == v["minute"]:
                        await bot.send_message(
                            int(uid),
                            f"💊 {v['name']} — {v['dose']}",
                            reply_markup=reminder_kb(v["id"])
                        )
                        sent.add(key)

        except Exception as e:
            print("Scheduler error:", e)

        await asyncio.sleep(30)

# ================= MAIN =================

async def main():
    print("🚀 Bot started")

    asyncio.create_task(scheduler())

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

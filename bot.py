import asyncio
import json
import os
import re
from datetime import datetime, time, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ── Config ────────────────────────────────────────────────────────────────────
TOKEN = os.getenv("BOT_TOKEN", "8681677219:AAHXp6ckwJiX8QcWX4S9ZUMVJZ0uJXp1JB0")
DATA_FILE = Path("data.json")

# ── Persistence ───────────────────────────────────────────────────────────────

def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {}


def save_data(data: dict):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_user(data: dict, uid: int) -> dict:
    key = str(uid)
    if key not in data:
        data[key] = {"vitamins": [], "log": []}
    return data[key]

# ── FSM ───────────────────────────────────────────────────────────────────────

class AddVitamin(StatesGroup):
    name = State()
    dose = State()
    time_str = State()

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_time(text: str):
    """Return (hour, minute) or None."""
    text = text.strip().lower()
    aliases = {"утром": (8, 0), "утро": (8, 0), "обед": (13, 0), "вечером": (20, 0), "ночью": (22, 0)}
    if text in aliases:
        return aliases[text]
    m = re.match(r"^(\d{1,2})[:\.](\d{2})$", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h < 24 and 0 <= mi < 60:
            return h, mi
    m = re.match(r"^(\d{1,2})$", text)
    if m:
        h = int(m.group(1))
        if 0 <= h < 24:
            return h, 0
    return None


def time_label(h: int, mi: int) -> str:
    return f"{h:02d}:{mi:02d}"


def vitamin_list_text(vitamins: list) -> str:
    if not vitamins:
        return "У тебя пока нет витаминов. Добавь первый командой /add"
    lines = []
    for i, v in enumerate(vitamins, 1):
        lines.append(f"{i}. 💊 <b>{v['name']}</b> — {v['dose']}, в {time_label(v['hour'], v['minute'])}")
    return "\n".join(lines)


def stats_text(user: dict) -> str:
    log = user.get("log", [])
    if not log:
        return "Статистика пуста — начни принимать витамины!"
    today = datetime.now().date().isoformat()
    week_ago = (datetime.now().date() - timedelta(days=7)).isoformat()
    taken = [e for e in log if e["action"] == "taken"]
    skipped = [e for e in log if e["action"] == "skipped"]
    taken_today = [e for e in taken if e["date"] == today]
    taken_week = [e for e in taken if e["date"] >= week_ago]
    total = len(taken) + len(skipped)
    pct = round(len(taken) / total * 100) if total else 0
    return (
        f"📊 <b>Статистика</b>\n\n"
        f"✅ Сегодня принято: <b>{len(taken_today)}</b>\n"
        f"📅 За неделю принято: <b>{len(taken_week)}</b>\n"
        f"🏆 Всего принято: <b>{len(taken)}</b>\n"
        f"❌ Всего пропущено: <b>{len(skipped)}</b>\n"
        f"📈 Процент приёма: <b>{pct}%</b>"
    )

# ── Keyboards ─────────────────────────────────────────────────────────────────

def reminder_kb(vitamin_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Выпила", callback_data=f"taken:{vitamin_id}"),
        InlineKeyboardButton(text="⏰ +10 мин", callback_data=f"snooze:{vitamin_id}"),
        InlineKeyboardButton(text="❌ Пропустить", callback_data=f"skip:{vitamin_id}"),
    ]])


def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💊 Мои витамины", callback_data="list"),
         InlineKeyboardButton(text="➕ Добавить", callback_data="add")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data="delete_menu")],
    ])

# ── Bot & Dispatcher ──────────────────────────────────────────────────────────

bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())

# ── Handlers ──────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(msg: Message):
    await msg.answer(
        "👋 Привет! Я помогу не забывать принимать витамины.\n\n"
        "Используй меню ниже или команды:\n"
        "/add — добавить витамин\n"
        "/list — список витаминов\n"
        "/stats — статистика\n"
        "/delete — удалить витамин",
        reply_markup=main_kb(),
    )


@dp.message(Command("list"))
@dp.callback_query(F.data == "list")
async def cmd_list(event):
    msg = event if isinstance(event, Message) else event.message
    data = load_data()
    user = get_user(data, msg.chat.id)
    text = vitamin_list_text(user["vitamins"])
    if isinstance(event, CallbackQuery):
        await event.answer()
    await msg.answer(text, reply_markup=main_kb())


@dp.message(Command("stats"))
@dp.callback_query(F.data == "stats")
async def cmd_stats(event):
    msg = event if isinstance(event, Message) else event.message
    data = load_data()
    user = get_user(data, msg.chat.id)
    if isinstance(event, CallbackQuery):
        await event.answer()
    await msg.answer(stats_text(user), reply_markup=main_kb())


# ── Add vitamin flow ──────────────────────────────────────────────────────────

@dp.message(Command("add"))
@dp.callback_query(F.data == "add")
async def cmd_add(event, state: FSMContext):
    msg = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer()
    await state.set_state(AddVitamin.name)
    await msg.answer("💊 Как называется витамин?\n<i>Например: Магний B6, Витамин D, Омега-3</i>")


@dp.message(AddVitamin.name)
async def add_name(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text.strip())
    await state.set_state(AddVitamin.dose)
    await msg.answer("📏 Какая доза?\n<i>Например: 1 таблетка, 2 капсулы, 5 мл</i>")


@dp.message(AddVitamin.dose)
async def add_dose(msg: Message, state: FSMContext):
    await state.update_data(dose=msg.text.strip())
    await state.set_state(AddVitamin.time_str)
    await msg.answer(
        "⏰ В какое время напоминать?\n"
        "<i>Например: 21:00, 8:30, утром, обед, вечером</i>"
    )


@dp.message(AddVitamin.time_str)
async def add_time(msg: Message, state: FSMContext):
    parsed = parse_time(msg.text)
    if not parsed:
        await msg.answer("❗ Не понял время. Попробуй: <b>21:00</b>, <b>8:30</b>, <b>утром</b>, <b>обед</b>")
        return
    h, mi = parsed
    data_fsm = await state.get_data()
    await state.clear()

    data = load_data()
    user = get_user(data, msg.from_user.id)
    user["vitamins"].append({
        "id": int(datetime.now().timestamp() * 1000),
        "name": data_fsm["name"],
        "dose": data_fsm["dose"],
        "hour": h,
        "minute": mi,
    })
    save_data(data)
    await msg.answer(
        f"✅ Добавлено!\n\n💊 <b>{data_fsm['name']}</b>\n"
        f"📏 {data_fsm['dose']}\n⏰ {time_label(h, mi)}",
        reply_markup=main_kb(),
    )


# ── Delete flow ───────────────────────────────────────────────────────────────

@dp.message(Command("delete"))
@dp.callback_query(F.data == "delete_menu")
async def cmd_delete_menu(event):
    msg = event if isinstance(event, Message) else event.message
    data = load_data()
    user = get_user(data, msg.chat.id)
    if isinstance(event, CallbackQuery):
        await event.answer()
    if not user["vitamins"]:
        await msg.answer("Нечего удалять.", reply_markup=main_kb())
        return
    buttons = [
        [InlineKeyboardButton(
            text=f"🗑 {v['name']} ({time_label(v['hour'], v['minute'])})",
            callback_data=f"del:{v['id']}"
        )]
        for v in user["vitamins"]
    ]
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="list")])
    await msg.answer("Выбери витамин для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data.startswith("del:"))
async def do_delete(call: CallbackQuery):
    vid = int(call.data.split(":")[1])
    data = load_data()
    user = get_user(data, call.from_user.id)
    before = len(user["vitamins"])
    user["vitamins"] = [v for v in user["vitamins"] if v["id"] != vid]
    save_data(data)
    await call.answer("Удалено ✅" if len(user["vitamins"]) < before else "Не найдено")
    await call.message.edit_text("Витамин удалён.", reply_markup=main_kb())


# ── Reminder callbacks ────────────────────────────────────────────────────────

def log_action(uid: int, vitamin_name: str, action: str):
    data = load_data()
    user = get_user(data, uid)
    user["log"].append({
        "action": action,
        "vitamin": vitamin_name,
        "date": datetime.now().date().isoformat(),
        "time": datetime.now().strftime("%H:%M"),
    })
    save_data(data)


@dp.callback_query(F.data.startswith("taken:"))
async def cb_taken(call: CallbackQuery):
    vid = int(call.data.split(":")[1])
    data = load_data()
    user = get_user(data, call.from_user.id)
    v = next((x for x in user["vitamins"] if x["id"] == vid), None)
    name = v["name"] if v else "витамин"
    log_action(call.from_user.id, name, "taken")
    await call.answer("Отлично! 💪")
    await call.message.edit_text(f"✅ <b>{name}</b> принят! Молодец!")


@dp.callback_query(F.data.startswith("skip:"))
async def cb_skip(call: CallbackQuery):
    vid = int(call.data.split(":")[1])
    data = load_data()
    user = get_user(data, call.from_user.id)
    v = next((x for x in user["vitamins"] if x["id"] == vid), None)
    name = v["name"] if v else "витамин"
    log_action(call.from_user.id, name, "skipped")
    await call.answer("Хорошо, пропускаем.")
    await call.message.edit_text(f"❌ <b>{name}</b> пропущен.")


@dp.callback_query(F.data.startswith("snooze:"))
async def cb_snooze(call: CallbackQuery):
    vid = int(call.data.split(":")[1])
    await call.answer("⏰ Напомню через 10 минут!")
    await call.message.edit_text("⏰ Напомню через 10 минут...")
    await asyncio.sleep(600)
    data = load_data()
    user = get_user(data, call.from_user.id)
    v = next((x for x in user["vitamins"] if x["id"] == vid), None)
    if v:
        await bot.send_message(
            call.from_user.id,
            f"🔔 Повторное напоминание!\n💊 <b>{v['name']}</b> — {v['dose']}",
            reply_markup=reminder_kb(vid),
        )


# ── Scheduler ─────────────────────────────────────────────────────────────────

async def scheduler():
    sent_today: set = set()  # (uid, vitamin_id, date)
    while True:
        now = datetime.now()
        data = load_data()
        for uid_str, user in data.items():
            uid = int(uid_str)
            for v in user.get("vitamins", []):
                key = (uid, v["id"], now.date().isoformat())
                if key in sent_today:
                    continue
                if now.hour == v["hour"] and now.minute == v["minute"]:
                    try:
                        await bot.send_message(
                            uid,
                            f"💊 Время принять <b>{v['name']}</b>!\n📏 {v['dose']}",
                            reply_markup=reminder_kb(v["id"]),
                        )
                        sent_today.add(key)
                    except Exception:
                        pass
        # Clean old sent_today keys (keep only today)
        today = now.date().isoformat()
        sent_today = {k for k in sent_today if k[2] == today}
        await asyncio.sleep(30)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    asyncio.create_task(scheduler())
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())

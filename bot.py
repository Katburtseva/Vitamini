"""
💊 Vitamin Reminder Bot — один файл, всё внутри
"""

import asyncio
import logging
import os
import re
import sqlite3
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ─── Только WARNING и выше, без лишнего шума ──────────────────────────────────
logging.basicConfig(format="%(levelname)s | %(message)s", level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.ERROR)

# ─── Настройки ────────────────────────────────────────────────────────────────
TIMEZONE = ZoneInfo("Europe/Moscow")   # ← меняйте под себя
DB_PATH  = Path("vitamins.db")

# ─── Состояния диалога ────────────────────────────────────────────────────────
WAITING_NAME, WAITING_DOSE, WAITING_TIME = range(3)


# ═══════════════════════════════════════════════════════════════════════════════
#  БАЗА ДАННЫХ
# ═══════════════════════════════════════════════════════════════════════════════

_conn: sqlite3.Connection | None = None


def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS users (
                id      INTEGER PRIMARY KEY,
                name    TEXT,
                created TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS vitamins (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                name       TEXT NOT NULL,
                dose       TEXT NOT NULL,
                hour       INTEGER NOT NULL,
                minute     INTEGER NOT NULL,
                time_label TEXT NOT NULL,
                active     INTEGER DEFAULT 1,
                created    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                vitamin_id INTEGER NOT NULL,
                status     TEXT DEFAULT 'pending',
                created    TEXT DEFAULT (datetime('now'))
            );
        """)
        _conn.commit()
    return _conn


def ensure_user(uid: int, name: str):
    db().execute("INSERT OR IGNORE INTO users (id, name) VALUES (?, ?)", (uid, name))
    db().commit()


def add_vitamin(uid, name, dose, hour, minute, time_label) -> int:
    cur = db().execute(
        "INSERT INTO vitamins (user_id,name,dose,hour,minute,time_label) VALUES (?,?,?,?,?,?)",
        (uid, name, dose, hour, minute, time_label)
    )
    db().commit()
    return cur.lastrowid


def get_vitamins(uid: int) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM vitamins WHERE user_id=? ORDER BY hour,minute", (uid,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_vitamin(vid: int) -> dict | None:
    row = db().execute("SELECT * FROM vitamins WHERE id=?", (vid,)).fetchone()
    return dict(row) if row else None


def get_all_active() -> list[dict]:
    return [dict(r) for r in db().execute("SELECT * FROM vitamins WHERE active=1").fetchall()]


def set_active(vid: int, active: bool):
    db().execute("UPDATE vitamins SET active=? WHERE id=?", (1 if active else 0, vid))
    db().commit()


def delete_vitamin(vid: int):
    db().execute("DELETE FROM logs WHERE vitamin_id=?", (vid,))
    db().execute("DELETE FROM vitamins WHERE id=?", (vid,))
    db().commit()


def create_log(vid: int) -> int:
    cur = db().execute("INSERT INTO logs (vitamin_id) VALUES (?)", (vid,))
    db().commit()
    return cur.lastrowid


def mark_log(log_id: int, status: str):
    db().execute("UPDATE logs SET status=? WHERE id=?", (status, log_id))
    db().commit()


def get_stats(uid: int) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    rows = db().execute("""
        SELECT v.name,
               COUNT(l.id) AS total,
               SUM(CASE WHEN l.status='taken'   THEN 1 ELSE 0 END) AS taken,
               SUM(CASE WHEN l.status='skipped' THEN 1 ELSE 0 END) AS skipped
        FROM vitamins v
        LEFT JOIN logs l ON l.vitamin_id=v.id AND l.created>=?
        WHERE v.user_id=?
        GROUP BY v.id ORDER BY v.hour,v.minute
    """, (cutoff, uid)).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
#  ХЕЛПЕРЫ
# ═══════════════════════════════════════════════════════════════════════════════

ALIASES = {
    "утром": "08:00", "утро": "08:00",
    "обед": "13:00",  "днём": "13:00", "днем": "13:00",
    "вечером": "19:00", "вечер": "19:00",
    "ночью": "22:00",   "ночь": "22:00",
}


def parse_time(text: str) -> dtime | None:
    text = ALIASES.get(text.strip().lower(), text.strip())
    m = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h < 24 and 0 <= mn < 60:
            return dtime(h, mn)
    return None


def fmt_vitamins(vitamins: list) -> str:
    if not vitamins:
        return "Список пуст. Добавьте витамин через /add"
    icons = ["💊","🔵","🟡","🟢","🔴","🟠","🟣"]
    parts = []
    for i, v in enumerate(vitamins):
        e = icons[i % len(icons)]
        status = "✅ Активен" if v["active"] else "⏸ Пауза"
        parts.append(f"{e} <b>{v['name']}</b> — {v['dose']}\n   ⏰ {v['time_label']}  |  {status}")
    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user(u.id, u.first_name)
    await update.message.reply_text(
        f"Привет, {u.first_name}! 👋\n\n"
        "Я напомню принять витамины вовремя.\n\n"
        "<b>Команды:</b>\n"
        "/add — добавить витамин\n"
        "/list — мой список\n"
        "/stats — статистика\n"
        "/pause — пауза / возобновить\n"
        "/delete — удалить\n"
        "/help — справка по времени",
        parse_mode="HTML"
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⏰ <b>Как указать время:</b>\n"
        "• <code>08:30</code> — точное время\n"
        "• <code>утром</code> → 08:00\n"
        "• <code>обед</code> → 13:00\n"
        "• <code>вечером</code> → 19:00\n"
        "• <code>ночью</code> → 22:00",
        parse_mode="HTML"
    )


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    vitamins = get_vitamins(update.effective_user.id)
    kb = [[
        InlineKeyboardButton("➕ Добавить", callback_data="add_new"),
        InlineKeyboardButton("⚙️ Управление", callback_data="manage"),
    ]] if vitamins else [[InlineKeyboardButton("➕ Добавить первый", callback_data="add_new")]]
    await update.message.reply_text(
        "💊 <b>Ваши витамины:</b>\n\n" + fmt_vitamins(vitamins),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    stats = get_stats(update.effective_user.id)
    if not stats:
        await update.message.reply_text("Статистики пока нет — начните принимать витамины!")
        return
    lines = ["📊 <b>Статистика за 30 дней:</b>\n"]
    for s in stats:
        pct = s["taken"] / s["total"] * 100 if s["total"] else 0
        bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
        e = "🟢" if pct >= 80 else "🟡" if pct >= 50 else "🔴"
        lines.append(
            f"{e} <b>{s['name']}</b>\n"
            f"   {bar} {pct:.0f}%\n"
            f"   ✅ {s['taken']} из {s['total']} напоминаний"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")


async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    vitamins = get_vitamins(update.effective_user.id)
    if not vitamins:
        await update.message.reply_text("Список пуст.")
        return
    kb = [[InlineKeyboardButton(
        f"{'▶️' if not v['active'] else '⏸'} {v['name']}",
        callback_data=f"toggle_{v['id']}"
    )] for v in vitamins]
    await update.message.reply_text("Нажмите для паузы / возобновления:", reply_markup=InlineKeyboardMarkup(kb))


async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    vitamins = get_vitamins(update.effective_user.id)
    if not vitamins:
        await update.message.reply_text("Список пуст.")
        return
    kb = [[InlineKeyboardButton(f"🗑 {v['name']}", callback_data=f"del_{v['id']}")] for v in vitamins]
    await update.message.reply_text("Выберите витамин для удаления:", reply_markup=InlineKeyboardMarkup(kb))


# ═══════════════════════════════════════════════════════════════════════════════
#  ДОБАВЛЕНИЕ ВИТАМИНА
# ═══════════════════════════════════════════════════════════════════════════════

CANCEL_KB = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")]])


async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    await msg.reply_text(
        "➕ <b>Добавление витамина</b>\n\nШаг 1/3: Название витамина?\n"
        "<i>Например: Магний B6, Витамин D3, Омега-3</i>",
        parse_mode="HTML", reply_markup=CANCEL_KB
    )
    return WAITING_NAME


async def got_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["name"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ <b>{ctx.user_data['name']}</b>\n\nШаг 2/3: Дозировка?\n"
        "<i>Например: 1 таблетка, 2 капсулы, 5 мл</i>",
        parse_mode="HTML", reply_markup=CANCEL_KB
    )
    return WAITING_DOSE


async def got_dose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["dose"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Доза: <b>{ctx.user_data['dose']}</b>\n\nШаг 3/3: Время напоминания?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🌅 Утром 08:00",  callback_data="time_утром"),
             InlineKeyboardButton("☀️ Обед 13:00",  callback_data="time_обед")],
            [InlineKeyboardButton("🌆 Вечер 19:00", callback_data="time_вечером"),
             InlineKeyboardButton("🌙 Ночь 22:00",  callback_data="time_ночью")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")],
        ])
    )
    return WAITING_TIME


async def got_time_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await _save(update, ctx, update.message.text.strip())


async def got_time_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _save(update, ctx, update.callback_query.data.replace("time_", ""), cb=True)


async def _save(update, ctx, time_str: str, cb=False):
    t = parse_time(time_str)
    msg = update.callback_query.message if cb else update.message
    if t is None:
        await msg.reply_text(
            "⚠️ Не понял время. Напишите <code>HH:MM</code> или слово: утром / обед / вечером / ночью",
            parse_mode="HTML"
        )
        return WAITING_TIME

    uid = update.effective_user.id
    vid = add_vitamin(uid, ctx.user_data["name"], ctx.user_data["dose"], t.hour, t.minute, time_str)
    schedule_vitamin(ctx.application, uid, vid, ctx.user_data["name"], ctx.user_data["dose"], t.hour, t.minute)

    await msg.reply_text(
        f"🎉 <b>{ctx.user_data['name']}</b> добавлен!\n⏰ Буду напоминать в <b>{t.strftime('%H:%M')}</b>",
        parse_mode="HTML"
    )
    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("❌ Отменено.")
    ctx.user_data.clear()
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════════
#  КНОПКИ (напоминание + управление)
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_callbacks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data, uid = q.data, update.effective_user.id

    if data.startswith("taken_"):
        _, vid, log_id = data.split("_")
        mark_log(int(log_id), "taken")
        vit = get_vitamin(int(vid))
        await q.edit_message_text(f"✅ <b>{vit['name']}</b> принят! Молодец 💪", parse_mode="HTML")

    elif data.startswith("snooze_"):
        _, vid, log_id = data.split("_")
        vit = get_vitamin(int(vid))
        await q.edit_message_text(f"⏰ Напомню про <b>{vit['name']}</b> через 10 минут", parse_mode="HTML")
        ctx.job_queue.run_once(
            send_reminder,
            when=600,
            data={"chat_id": uid, "vid": int(vid), "name": vit["name"], "dose": vit["dose"]},
            name=f"snooze_{uid}_{vid}",
        )

    elif data.startswith("skip_"):
        _, vid, log_id = data.split("_")
        mark_log(int(log_id), "skipped")
        vit = get_vitamin(int(vid))
        await q.edit_message_text(f"❌ <b>{vit['name']}</b> пропущен.", parse_mode="HTML")

    elif data.startswith("toggle_"):
        vid = int(data.replace("toggle_", ""))
        vit = get_vitamin(vid)
        new_state = not vit["active"]
        set_active(vid, new_state)
        if new_state:
            schedule_vitamin(ctx.application, uid, vid, vit["name"], vit["dose"], vit["hour"], vit["minute"])
            await q.edit_message_text(f"▶️ <b>{vit['name']}</b> возобновлён.", parse_mode="HTML")
        else:
            _remove_job(ctx.application, uid, vid)
            await q.edit_message_text(f"⏸ <b>{vit['name']}</b> на паузе.", parse_mode="HTML")

    elif data.startswith("del_"):
        vid = int(data.replace("del_", ""))
        vit = get_vitamin(vid)
        _remove_job(ctx.application, uid, vid)
        delete_vitamin(vid)
        await q.edit_message_text(f"🗑 <b>{vit['name']}</b> удалён.", parse_mode="HTML")

    elif data == "add_new":
        await q.message.reply_text(
            "➕ <b>Добавление витамина</b>\n\nШаг 1/3: Название витамина?",
            parse_mode="HTML"
        )

    elif data == "manage":
        vitamins = get_vitamins(uid)
        kb = (
            [[InlineKeyboardButton(f"{'⏸' if v['active'] else '▶️'} {v['name']}", callback_data=f"toggle_{v['id']}")] for v in vitamins]
            + [[InlineKeyboardButton(f"🗑 {v['name']}", callback_data=f"del_{v['id']}")] for v in vitamins]
        )
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))


# ═══════════════════════════════════════════════════════════════════════════════
#  РАСПИСАНИЕ
# ═══════════════════════════════════════════════════════════════════════════════

async def send_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    d = ctx.job.data
    log_id = create_log(d["vid"])
    await ctx.bot.send_message(
        chat_id=d["chat_id"],
        text=f"💊 <b>Время принять {d['name']}!</b>\n📏 Доза: {d['dose']}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Выпил(а)", callback_data=f"taken_{d['vid']}_{log_id}"),
             InlineKeyboardButton("⏰ +10 мин",  callback_data=f"snooze_{d['vid']}_{log_id}")],
            [InlineKeyboardButton("❌ Пропустить", callback_data=f"skip_{d['vid']}_{log_id}")],
        ])
    )


def schedule_vitamin(app, uid, vid, name, dose, hour, minute):
    _remove_job(app, uid, vid)
    app.job_queue.run_daily(
        send_reminder,
        time=dtime(hour=hour, minute=minute, tzinfo=TIMEZONE),
        data={"chat_id": uid, "vid": vid, "name": name, "dose": dose},
        name=f"vit_{uid}_{vid}",
    )


def _remove_job(app, uid, vid):
    jobs = app.job_queue.get_jobs_by_name(f"vit_{uid}_{vid}")
    for j in jobs:
        j.schedule_removal()


async def restore_schedules(app):
    for v in get_all_active():
        schedule_vitamin(app, v["user_id"], v["id"], v["name"], v["dose"], v["hour"], v["minute"])


# ═══════════════════════════════════════════════════════════════════════════════
#  ТОЧКА ВХОДА
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("Установите переменную окружения BOT_TOKEN")

    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("add", cmd_add), CallbackQueryHandler(cmd_add, pattern="^add_new$")],
        states={
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_name)],
            WAITING_DOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_dose)],
            WAITING_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_time_text),
                CallbackQueryHandler(got_time_btn, pattern="^time_"),
                CallbackQueryHandler(cancel_add, pattern="^cancel_add$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_add, pattern="^cancel_add$"),
            CommandHandler("start", cmd_start),
        ],
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("list",   cmd_list))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CommandHandler("pause",  cmd_pause))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    async def on_start(a):
        await restore_schedules(a)

    app.post_init = on_start

    print("🤖 Бот запущен. Ctrl+C для остановки.")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

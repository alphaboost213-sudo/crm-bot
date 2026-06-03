import logging
import sqlite3
import asyncio
from datetime import datetime, date
import re

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, JobQueue
)

TOKEN = "8751256202:AAHNVreF9fcad96N1pP2cbNgN_8TO2YkvVw"
MORNING_HOUR = 9  # Время утренней рассылки плана (МСК)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ─── База данных ───────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            target_username TEXT NOT NULL,
            remind_date TEXT NOT NULL,
            comment TEXT,
            done INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            task TEXT NOT NULL,
            done INTEGER DEFAULT 0,
            created_date TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS rating (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            display_name TEXT NOT NULL,
            points INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def get_conn():
    return sqlite3.connect("bot.db")

# ─── Напоминалки ───────────────────────────────────────────────────────────────

async def cmd_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /remind @username YYYY-MM-DD комментарий
    /remind @username DD.MM.YYYY комментарий
    """
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # Парсим
    pattern = r"/remind\s+(@\S+)\s+(\S+)\s*(.*)"
    m = re.match(pattern, text)
    if not m:
        await update.message.reply_text(
            "Формат: /remind @username 2025-07-15 комментарий\n"
            "или: /remind @username 15.07.2025 комментарий"
        )
        return

    target, raw_date, comment = m.group(1), m.group(2), m.group(3).strip()

    # Парсим дату
    remind_date = None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            remind_date = datetime.strptime(raw_date, fmt).date()
            break
        except ValueError:
            pass

    if not remind_date:
        await update.message.reply_text("Дата не распознана. Используй 2025-07-15 или 15.07.2025")
        return

    conn = get_conn()
    conn.execute(
        "INSERT INTO reminders (owner_id, target_username, remind_date, comment) VALUES (?, ?, ?, ?)",
        (user_id, target, str(remind_date), comment)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"Запомнил ✓\n"
        f"Кого: {target}\n"
        f"Когда: {remind_date.strftime('%d.%m.%Y')}\n"
        f"Комментарий: {comment or '—'}"
    )


async def cmd_reminders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Список активных напоминалок"""
    user_id = update.effective_user.id
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, target_username, remind_date, comment FROM reminders WHERE owner_id=? AND done=0 ORDER BY remind_date",
        (user_id,)
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("Активных напоминалок нет")
        return

    lines = ["📋 <b>Активные напоминалки:</b>\n"]
    for r in rows:
        rid, username, rdate, comment = r
        d = datetime.strptime(rdate, "%Y-%m-%d").strftime("%d.%m.%Y")
        lines.append(f"#{rid} {username} — {d}\n    {comment or '—'}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_done_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /done_remind 5  - закрыть напоминалку по ID
    """
    user_id = update.effective_user.id
    args = ctx.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Использование: /done_remind <id>")
        return

    rid = int(args[0])
    conn = get_conn()
    conn.execute("UPDATE reminders SET done=1 WHERE id=? AND owner_id=?", (rid, user_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Напоминалка #{rid} закрыта ✓")

# ─── План дня ──────────────────────────────────────────────────────────────────

async def cmd_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /plan задача1 | задача2 | задача3
    Устанавливает план на сегодня
    """
    user_id = update.effective_user.id
    text = update.message.text.replace("/plan", "", 1).strip()

    if not text:
        await update.message.reply_text(
            "Задай план:\n/plan Собрать базу | Ответить на долёты | Обзвон"
        )
        return

    tasks = [t.strip() for t in text.split("|") if t.strip()]
    today = str(date.today())

    conn = get_conn()
    # Удаляем старый план на сегодня
    conn.execute("DELETE FROM daily_plan WHERE owner_id=? AND created_date=?", (user_id, today))
    for task in tasks:
        conn.execute(
            "INSERT INTO daily_plan (owner_id, task, created_date) VALUES (?, ?, ?)",
            (user_id, task, today)
        )
    conn.commit()
    conn.close()

    lines = [f"✅ План на сегодня сохранён:\n"]
    for i, t in enumerate(tasks, 1):
        lines.append(f"{i}. {t}")

    await update.message.reply_text("\n".join(lines))


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать план на сегодня"""
    user_id = update.effective_user.id
    today = str(date.today())
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, task, done FROM daily_plan WHERE owner_id=? AND created_date=? ORDER BY id",
        (user_id, today)
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("План на сегодня не задан. Используй /plan")
        return

    lines = [f"📅 <b>План на {datetime.today().strftime('%d.%m.%Y')}:</b>\n"]
    for rid, task, done in rows:
        mark = "✅" if done else "⬜"
        lines.append(f"{mark} {task}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /check 1 2 3  - отметить задачи выполненными по номеру
    """
    user_id = update.effective_user.id
    today = str(date.today())
    args = ctx.args

    if not args:
        await update.message.reply_text("Использование: /check 1 2 3")
        return

    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM daily_plan WHERE owner_id=? AND created_date=? ORDER BY id",
        (user_id, today)
    ).fetchall()

    ids = [r[0] for r in rows]
    for arg in args:
        if arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(ids):
                conn.execute("UPDATE daily_plan SET done=1 WHERE id=?", (ids[idx],))

    conn.commit()
    conn.close()
    await cmd_today(update, ctx)

# ─── Рейтинг ───────────────────────────────────────────────────────────────────

async def cmd_add_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /add_member @username Имя - добавить участника в рейтинг
    """
    user_id = update.effective_user.id
    text = update.message.text.strip()
    pattern = r"/add_member\s+(@\S+)\s+(.*)"
    m = re.match(pattern, text)
    if not m:
        await update.message.reply_text("Использование: /add_member @username Имя Фамилия")
        return

    username, display_name = m.group(1), m.group(2).strip()
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM rating WHERE owner_id=? AND username=?", (user_id, username)
    ).fetchone()

    if existing:
        await update.message.reply_text(f"{username} уже есть в рейтинге")
    else:
        conn.execute(
            "INSERT INTO rating (owner_id, username, display_name, points) VALUES (?, ?, ?, 0)",
            (user_id, username, display_name)
        )
        conn.commit()
        await update.message.reply_text(f"Добавлен: {display_name} ({username})")

    conn.close()


async def cmd_points(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /points @username +5 комментарий
    /points @username -3 комментарий
    """
    user_id = update.effective_user.id
    text = update.message.text.strip()
    pattern = r"/points\s+(@\S+)\s+([+-]?\d+)\s*(.*)"
    m = re.match(pattern, text)
    if not m:
        await update.message.reply_text("Использование: /points @username +5 закрыл сделку")
        return

    username, delta, comment = m.group(1), int(m.group(2)), m.group(3).strip()
    conn = get_conn()
    existing = conn.execute(
        "SELECT id, points FROM rating WHERE owner_id=? AND username=?", (user_id, username)
    ).fetchone()

    if not existing:
        await update.message.reply_text(f"{username} не найден. Сначала /add_member")
        conn.close()
        return

    new_points = existing[1] + delta
    conn.execute("UPDATE rating SET points=? WHERE id=?", (new_points, existing[0]))
    conn.commit()
    conn.close()

    sign = "+" if delta > 0 else ""
    await update.message.reply_text(
        f"{username}: {sign}{delta} очков\n"
        f"Итого: {new_points} очков\n"
        f"{comment or ''}"
    )


async def cmd_rating(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать рейтинг команды"""
    user_id = update.effective_user.id
    conn = get_conn()
    rows = conn.execute(
        "SELECT display_name, username, points FROM rating WHERE owner_id=? ORDER BY points DESC",
        (user_id,)
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("Рейтинг пустой. Добавь участников через /add_member")
        return

    lines = ["🏆 <b>Рейтинг команды:</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (name, username, points) in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {name} ({username}) — {points} очков")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

# ─── Джоба: проверка напоминалок каждые утро ───────────────────────────────────

async def morning_job(context: ContextTypes.DEFAULT_TYPE):
    today = str(date.today())
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, owner_id, target_username, comment FROM reminders WHERE remind_date=? AND done=0",
        (today,)
    ).fetchall()

    for rid, owner_id, username, comment in rows:
        try:
            msg = f"⏰ Пингани {username}"
            if comment:
                msg += f"\n{comment}"
            await context.bot.send_message(chat_id=owner_id, text=msg)
        except Exception as e:
            logging.error(f"Не удалось отправить напоминалку {rid}: {e}")

    # Отправляем план дня тем у кого он есть
    plan_owners = conn.execute(
        "SELECT DISTINCT owner_id FROM daily_plan WHERE created_date=?", (today,)
    ).fetchall()

    for (owner_id,) in plan_owners:
        rows_plan = conn.execute(
            "SELECT task FROM daily_plan WHERE owner_id=? AND created_date=? ORDER BY id",
            (owner_id, today)
        ).fetchall()
        if rows_plan:
            lines = [f"☀️ Доброе утро! План на {datetime.today().strftime('%d.%m.%Y')}:\n"]
            for i, (task,) in enumerate(rows_plan, 1):
                lines.append(f"{i}. {task}")
            try:
                await context.bot.send_message(chat_id=owner_id, text="\n".join(lines))
            except Exception as e:
                logging.error(f"Не удалось отправить план {owner_id}: {e}")

    conn.close()

# ─── /help ─────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = """<b>Команды бота:</b>

<b>Напоминалки:</b>
/remind @username 15.07.2025 комментарий
/reminders — список активных
/done_remind 5 — закрыть напоминалку #5

<b>План дня:</b>
/plan Задача 1 | Задача 2 | Задача 3
/today — посмотреть план
/check 1 3 — отметить задачи 1 и 3 выполненными

<b>Рейтинг:</b>
/add_member @username Имя — добавить участника
/points @username +5 комментарий — начислить очки
/rating — показать таблицу

Утром в 9:00 бот сам присылает план и напоминалки на сегодня."""
    await update.message.reply_text(text, parse_mode="HTML")

# ─── Старт ─────────────────────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("done_remind", cmd_done_remind))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("add_member", cmd_add_member))
    app.add_handler(CommandHandler("points", cmd_points))
    app.add_handler(CommandHandler("rating", cmd_rating))

    # Джоба каждый день в 9:00
    app.job_queue.run_daily(
        morning_job,
        time=datetime.strptime(f"{MORNING_HOUR}:00", "%H:%M").time()
    )

    print("Бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

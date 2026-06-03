import logging
import sqlite3
from datetime import datetime, date, time as dtime, timezone, timedelta
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)

TOKEN = "8751256202:AAHNVreF9fcad96N1pP2cbNgN_8TO2YkvVw"
KYIV = timezone(timedelta(hours=3))
MORNING_HOUR = 9
ADMIN_ID = 7382509664
ADMIN_NICK = "Kurama"

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

(
    ONBOARD_NICK,
    REMIND_USERNAME, REMIND_DATE, REMIND_COMMENT,
    PLAN_TASKS,
    RATING_ADD_USERNAME, RATING_ADD_NAME,
    RATING_POINTS_WHO, RATING_POINTS_DELTA, RATING_POINTS_COMMENT,
) = range(10)

# ─── БД ───────────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect("/app/bot.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        nick TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        target_username TEXT NOT NULL,
        remind_date TEXT NOT NULL,
        comment TEXT,
        done INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS daily_plan (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        task TEXT NOT NULL,
        done INTEGER DEFAULT 0,
        created_date TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS rating (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        display_name TEXT NOT NULL,
        points INTEGER DEFAULT 0
    )""")
    conn.commit()
    conn.close()

def get_conn():
    return sqlite3.connect("/app/data/bot.db")

def get_user_nick(user_id):
    if user_id == ADMIN_ID:
        return ADMIN_NICK
    conn = get_conn()
    row = conn.execute("SELECT nick FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else None

def save_user_nick(user_id, nick):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO users (user_id, nick) VALUES (?, ?)", (user_id, nick))
    conn.commit()
    conn.close()

# ─── Главное меню ──────────────────────────────────────────────────────────────

def main_menu_keyboard(user_id):
    rows = [
        ["⏰ Напоминалки", "📅 План дня"],
        ["🏆 Рейтинг команды"],
    ]
    if user_id == ADMIN_ID:
        rows.append(["👁 Напоминалки оперов"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

# ─── ОНБОРДИНГ ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Админ — сразу в меню
    if user_id == ADMIN_ID:
        await update.message.reply_text(
            f"Привет, {ADMIN_NICK}! 👋",
            reply_markup=main_menu_keyboard(user_id)
        )
        return ConversationHandler.END

    nick = get_user_nick(user_id)
    if nick:
        await update.message.reply_text(
            f"С возвращением, {nick}! 👋",
            reply_markup=main_menu_keyboard(user_id)
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "Привет! Прежде чем начать, введи свой ник (как тебя будут видеть в системе):"
    )
    return ONBOARD_NICK

async def onboard_got_nick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    user_id = update.effective_user.id
    save_user_nick(user_id, nick)
    await update.message.reply_text(
        f"Отлично, {nick}! Теперь ты в системе 👇",
        reply_markup=main_menu_keyboard(user_id)
    )
    return ConversationHandler.END

# ─── НАПОМИНАЛКИ ОПЕРОВ (только админ) ────────────────────────────────────────

async def admin_all_reminders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    conn = get_conn()
    rows = conn.execute("""
        SELECT r.id, r.owner_id, r.target_username, r.remind_date, r.comment, u.nick
        FROM reminders r
        LEFT JOIN users u ON u.user_id = r.owner_id
        WHERE r.done = 0
        ORDER BY r.remind_date, u.nick
    """).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("Активных напоминалок у оперов нет")
        return

    # Группируем по оперу
    by_oper = {}
    for rid, owner_id, target, rdate, comment, nick in rows:
        label = nick or f"id{owner_id}"
        if label not in by_oper:
            by_oper[label] = []
        d = format_remind_date(rdate)
        by_oper[label].append(f"  #{rid} {target} — {d}\n  {comment or '—'}")

    lines = ["👁 <b>Напоминалки оперов:</b>\n"]
    for oper, items in by_oper.items():
        lines.append(f"<b>{oper}:</b>")
        lines.extend(items)
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

# ─── НАПОМИНАЛКИ ──────────────────────────────────────────────────────────────

def format_remind_date(rdate_str):
    """Форматирует дату из БД в читаемый вид"""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(rdate_str, fmt)
            if " " in rdate_str:
                return dt.strftime("%d.%m.%Y в %H:%M Киев")
            return dt.strftime("%d.%m.%Y")
        except ValueError:
            pass
    return rdate_str


def reminders_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить напоминалку", callback_data="remind_add")],
        [InlineKeyboardButton("📋 Список активных", callback_data="remind_list")],
        [InlineKeyboardButton("✅ Закрыть напоминалку", callback_data="remind_close")],
    ])

async def show_reminders_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    await msg.reply_text("⏰ <b>Напоминалки</b>", parse_mode="HTML", reply_markup=reminders_menu())

async def remind_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Введи username клиента (с @ или без):")
    return REMIND_USERNAME

async def remind_got_username(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    if not username.startswith("@"):
        username = "@" + username
    ctx.user_data["remind_username"] = username
    await update.message.reply_text(
        f"Окей, {username}\n"
        "Введи дату (и время по желанию) по Киев:\n\n"
        "Только дата: 15.07.2025\n"
        "Дата + время: 15.07.2025 14:30"
    )
    return REMIND_DATE

async def remind_got_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    remind_dt = None
    # Пробуем с временем
    for fmt in ("%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            remind_dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            pass
    # Пробуем только дату
    if not remind_dt:
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                remind_dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                pass
    if not remind_dt:
        await update.message.reply_text(
            "Не понял. Введи дату в формате:\n"
            "15.07.2025  или  15.07.2025 14:30"
        )
        return REMIND_DATE
    ctx.user_data["remind_dt"] = remind_dt
    await update.message.reply_text("Добавь комментарий (или напиши «-» если не нужен):")
    return REMIND_COMMENT

async def remind_got_comment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    comment = update.message.text.strip()
    if comment == "-":
        comment = ""
    user_id = update.effective_user.id
    username = ctx.user_data["remind_username"]
    remind_dt = ctx.user_data["remind_dt"]
    # Сохраняем как "YYYY-MM-DD HH:MM" или "YYYY-MM-DD"
    has_time = remind_dt.hour != 0 or remind_dt.minute != 0
    dt_str = remind_dt.strftime("%Y-%m-%d %H:%M") if has_time else remind_dt.strftime("%Y-%m-%d")
    conn = get_conn()
    conn.execute(
        "INSERT INTO reminders (owner_id, target_username, remind_date, comment) VALUES (?, ?, ?, ?)",
        (user_id, username, dt_str, comment)
    )
    conn.commit()
    conn.close()
    when_label = remind_dt.strftime("%d.%m.%Y в %H:%M Киев") if has_time else remind_dt.strftime("%d.%m.%Y")
    await update.message.reply_text(
        f"✅ Сохранено!\nКого: {username}\nКогда: {when_label}\nКомментарий: {comment or '—'}",
        reply_markup=main_menu_keyboard(user_id)
    )
    return ConversationHandler.END

async def remind_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    user_id = update.effective_user.id
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, target_username, remind_date, comment FROM reminders WHERE owner_id=? AND done=0 ORDER BY remind_date",
        (user_id,)
    ).fetchall()
    conn.close()
    if not rows:
        await update.callback_query.message.reply_text("Активных напоминалок нет")
        return
    lines = ["📋 <b>Активные напоминалки:</b>\n"]
    for rid, username, rdate, comment in rows:
        d = format_remind_date(rdate)
        lines.append(f"#{rid} {username} — {d}\n  {comment or '—'}")
    await update.callback_query.message.reply_text("\n\n".join(lines), parse_mode="HTML")

async def remind_close_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    user_id = update.effective_user.id
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, target_username, remind_date FROM reminders WHERE owner_id=? AND done=0 ORDER BY remind_date",
        (user_id,)
    ).fetchall()
    conn.close()
    if not rows:
        await update.callback_query.message.reply_text("Нечего закрывать, активных напоминалок нет")
        return
    buttons = []
    for rid, username, rdate in rows:
        d = format_remind_date(rdate)
        buttons.append([InlineKeyboardButton(f"#{rid} {username} {d}", callback_data=f"close_remind_{rid}")])
    await update.callback_query.message.reply_text("Выбери какую закрыть:", reply_markup=InlineKeyboardMarkup(buttons))

async def remind_close_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    rid = int(update.callback_query.data.replace("close_remind_", ""))
    user_id = update.effective_user.id
    conn = get_conn()
    conn.execute("UPDATE reminders SET done=1 WHERE id=? AND owner_id=?", (rid, user_id))
    conn.commit()
    conn.close()
    await update.callback_query.message.reply_text(f"✅ Напоминалка #{rid} закрыта", reply_markup=main_menu_keyboard(user_id))

# ─── ПЛАН ДНЯ ─────────────────────────────────────────────────────────────────

def plan_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Задать план на сегодня", callback_data="plan_set")],
        [InlineKeyboardButton("📋 Посмотреть план", callback_data="plan_view")],
        [InlineKeyboardButton("✅ Отметить выполненное", callback_data="plan_check")],
    ])

async def show_plan_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    await msg.reply_text("📅 <b>План дня</b>", parse_mode="HTML", reply_markup=plan_menu())

async def plan_set_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Введи задачи — каждую с новой строки или через запятую:\n\nНапример:\nСобрать базу\nОтветить на долёты\nОбзвон"
    )
    return PLAN_TASKS

async def plan_got_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    tasks = [t.strip() for t in (text.split("\n") if "\n" in text else text.split(",")) if t.strip()]
    user_id = update.effective_user.id
    today = str(date.today())
    conn = get_conn()
    conn.execute("DELETE FROM daily_plan WHERE owner_id=? AND created_date=?", (user_id, today))
    for task in tasks:
        conn.execute("INSERT INTO daily_plan (owner_id, task, created_date) VALUES (?, ?, ?)", (user_id, task, today))
    conn.commit()
    conn.close()
    lines = ["✅ <b>План на сегодня сохранён:</b>\n"] + [f"{i}. {t}" for i, t in enumerate(tasks, 1)]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=main_menu_keyboard(user_id))
    return ConversationHandler.END

async def plan_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    user_id = update.effective_user.id
    today = str(date.today())
    conn = get_conn()
    rows = conn.execute(
        "SELECT task, done FROM daily_plan WHERE owner_id=? AND created_date=? ORDER BY id", (user_id, today)
    ).fetchall()
    conn.close()
    if not rows:
        await update.callback_query.message.reply_text("План на сегодня не задан")
        return
    lines = [f"📅 <b>План на {datetime.today().strftime('%d.%m.%Y')}:</b>\n"]
    for task, done in rows:
        lines.append(f"{'✅' if done else '⬜'} {task}")
    await update.callback_query.message.reply_text("\n".join(lines), parse_mode="HTML")

async def plan_check_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    user_id = update.effective_user.id
    today = str(date.today())
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, task FROM daily_plan WHERE owner_id=? AND created_date=? AND done=0 ORDER BY id", (user_id, today)
    ).fetchall()
    conn.close()
    if not rows:
        await update.callback_query.message.reply_text("Все задачи уже выполнены 🎉")
        return
    buttons = [[InlineKeyboardButton(f"✅ {task}", callback_data=f"check_task_{rid}")] for rid, task in rows]
    await update.callback_query.message.reply_text("Нажми на задачу:", reply_markup=InlineKeyboardMarkup(buttons))

async def plan_check_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    rid = int(update.callback_query.data.replace("check_task_", ""))
    user_id = update.effective_user.id
    conn = get_conn()
    conn.execute("UPDATE daily_plan SET done=1 WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    await update.callback_query.message.reply_text("✅ Отмечено!", reply_markup=main_menu_keyboard(user_id))

# ─── РЕЙТИНГ ──────────────────────────────────────────────────────────────────

def rating_menu(is_admin):
    buttons = [[InlineKeyboardButton("🏆 Посмотреть рейтинг", callback_data="rating_view")]]
    if is_admin:
        buttons.append([InlineKeyboardButton("➕ Добавить участника", callback_data="rating_add")])
        buttons.append([InlineKeyboardButton("⭐ Начислить очки", callback_data="rating_points")])
    return InlineKeyboardMarkup(buttons)

async def show_rating_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    user_id = update.effective_user.id
    await msg.reply_text("🏆 <b>Рейтинг команды</b>", parse_mode="HTML", reply_markup=rating_menu(user_id == ADMIN_ID))

async def rating_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    user_id = update.effective_user.id
    conn = get_conn()
    rows = conn.execute(
        "SELECT display_name, username, points FROM rating WHERE owner_id=? ORDER BY points DESC", (user_id,)
    ).fetchall()
    conn.close()
    if not rows:
        await update.callback_query.message.reply_text("Рейтинг пустой. Сначала добавь участников")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Рейтинг команды:</b>\n"]
    for i, (name, username, points) in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {name} ({username}) — {points} очков")
    await update.callback_query.message.reply_text("\n".join(lines), parse_mode="HTML")

async def rating_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if update.effective_user.id != ADMIN_ID:
        await update.callback_query.message.reply_text("Нет доступа")
        return ConversationHandler.END
    await update.callback_query.message.reply_text("Введи username участника (с @ или без):")
    return RATING_ADD_USERNAME

async def rating_add_username(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    if not username.startswith("@"):
        username = "@" + username
    ctx.user_data["new_member_username"] = username
    await update.message.reply_text(f"Окей, {username}\nТеперь введи имя для рейтинга:")
    return RATING_ADD_NAME

async def rating_add_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    display_name = update.message.text.strip()
    username = ctx.user_data["new_member_username"]
    user_id = update.effective_user.id
    conn = get_conn()
    existing = conn.execute("SELECT id FROM rating WHERE owner_id=? AND username=?", (user_id, username)).fetchone()
    if existing:
        await update.message.reply_text(f"{username} уже есть в рейтинге", reply_markup=main_menu_keyboard(user_id))
    else:
        conn.execute("INSERT INTO rating (owner_id, username, display_name, points) VALUES (?, ?, ?, 0)", (user_id, username, display_name))
        conn.commit()
        await update.message.reply_text(f"✅ Добавлен: {display_name} ({username})", reply_markup=main_menu_keyboard(user_id))
    conn.close()
    return ConversationHandler.END

async def rating_points_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if update.effective_user.id != ADMIN_ID:
        await update.callback_query.message.reply_text("Нет доступа")
        return ConversationHandler.END
    user_id = update.effective_user.id
    conn = get_conn()
    rows = conn.execute(
        "SELECT username, display_name, points FROM rating WHERE owner_id=? ORDER BY points DESC", (user_id,)
    ).fetchall()
    conn.close()
    if not rows:
        await update.callback_query.message.reply_text("Сначала добавь участников")
        return ConversationHandler.END
    buttons = [[InlineKeyboardButton(f"{name} ({pts} оч.)", callback_data=f"pts_who_{uname}")] for uname, name, pts in rows]
    await update.callback_query.message.reply_text("Кому начислить очки?", reply_markup=InlineKeyboardMarkup(buttons))
    return RATING_POINTS_WHO

async def rating_points_who(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    ctx.user_data["pts_username"] = update.callback_query.data.replace("pts_who_", "")
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("+1", callback_data="pts_delta_+1"), InlineKeyboardButton("+3", callback_data="pts_delta_+3"),
         InlineKeyboardButton("+5", callback_data="pts_delta_+5"), InlineKeyboardButton("+10", callback_data="pts_delta_+10")],
        [InlineKeyboardButton("-1", callback_data="pts_delta_-1"), InlineKeyboardButton("-3", callback_data="pts_delta_-3"),
         InlineKeyboardButton("-5", callback_data="pts_delta_-5")],
    ])
    await update.callback_query.message.reply_text(f"Сколько очков?", reply_markup=buttons)
    return RATING_POINTS_DELTA

async def rating_points_delta(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    ctx.user_data["pts_delta"] = int(update.callback_query.data.replace("pts_delta_", ""))
    await update.callback_query.message.reply_text("Добавь комментарий (или напиши «-»):")
    return RATING_POINTS_COMMENT

async def rating_points_comment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    comment = update.message.text.strip()
    if comment == "-":
        comment = ""
    username = ctx.user_data["pts_username"]
    delta = ctx.user_data["pts_delta"]
    user_id = update.effective_user.id
    conn = get_conn()
    existing = conn.execute("SELECT id, points FROM rating WHERE owner_id=? AND username=?", (user_id, username)).fetchone()
    if existing:
        new_points = existing[1] + delta
        conn.execute("UPDATE rating SET points=? WHERE id=?", (new_points, existing[0]))
        conn.commit()
        sign = "+" if delta > 0 else ""
        await update.message.reply_text(
            f"✅ {username}: {sign}{delta} очков\nИтого: {new_points}\n{comment}",
            reply_markup=main_menu_keyboard(user_id)
        )
    conn.close()
    return ConversationHandler.END

# ─── Обработка текстовых кнопок ───────────────────────────────────────────────

async def handle_menu_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Проверяем что ник введён (кроме админа)
    if user_id != ADMIN_ID and not get_user_nick(user_id):
        await update.message.reply_text("Сначала введи свой ник — напиши /start")
        return
    text = update.message.text
    if text == "⏰ Напоминалки":
        await show_reminders_menu(update, ctx)
    elif text == "📅 План дня":
        await show_plan_menu(update, ctx)
    elif text == "🏆 Рейтинг команды":
        await show_rating_menu(update, ctx)
    elif text == "👁 Напоминалки оперов" and user_id == ADMIN_ID:
        await admin_all_reminders(update, ctx)

# ─── Утренняя джоба ────────────────────────────────────────────────────────────

async def morning_job(context: ContextTypes.DEFAULT_TYPE):
    today = str(date.today())
    conn = get_conn()
    now_msk = datetime.now(KYIV)
    rows = conn.execute(
        "SELECT id, owner_id, target_username, remind_date, comment FROM reminders WHERE done=0"
    ).fetchall()
    for rid, owner_id, username, rdate, comment in rows:
        try:
            # Если есть время — шлём только в нужный час Киев
            if " " in rdate:
                dt = datetime.strptime(rdate, "%Y-%m-%d %H:%M")
                if dt.date() != now_msk.date():
                    continue
                if dt.hour != now_msk.hour or dt.minute > now_msk.minute:
                    continue
            else:
                # Без времени — шлём утром как раньше
                if rdate != today:
                    continue
            msg = f"⏰ Пингани {username}"
            if comment:
                msg += f"\n{comment}"
            await context.bot.send_message(chat_id=owner_id, text=msg)
            # Помечаем как отправленную чтобы не дублировать
            conn.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
            conn.commit()
        except Exception as e:
            logging.error(f"Напоминалка {rid}: {e}")

    # Утренний план — только в 9:00 Киев
    if now_msk.hour == MORNING_HOUR and now_msk.minute < 1:
        plan_owners = conn.execute("SELECT DISTINCT owner_id FROM daily_plan WHERE created_date=?", (today,)).fetchall()
        for (owner_id,) in plan_owners:
            rows_plan = conn.execute(
                "SELECT task FROM daily_plan WHERE owner_id=? AND created_date=? ORDER BY id", (owner_id, today)
            ).fetchall()
            if rows_plan:
                lines = [f"☀️ Доброе утро! План на {datetime.today().strftime('%d.%m.%Y')}:\n"]
                for i, (task,) in enumerate(rows_plan, 1):
                    lines.append(f"{i}. {task}")
                try:
                    await context.bot.send_message(chat_id=owner_id, text="\n".join(lines))
                except Exception as e:
                    logging.error(f"План {owner_id}: {e}")
    conn.close()

# ─── Отмена ────────────────────────────────────────────────────────────────────

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("Отменено", reply_markup=main_menu_keyboard(user_id))
    return ConversationHandler.END

# ─── Запуск ────────────────────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    # Онбординг (ввод ника при старте)
    onboard_conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            ONBOARD_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_got_nick)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    remind_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(remind_add_start, pattern="^remind_add$")],
        states={
            REMIND_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, remind_got_username)],
            REMIND_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, remind_got_date)],
            REMIND_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, remind_got_comment)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    plan_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(plan_set_start, pattern="^plan_set$")],
        states={
            PLAN_TASKS: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_got_tasks)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    rating_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(rating_add_start, pattern="^rating_add$")],
        states={
            RATING_ADD_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, rating_add_username)],
            RATING_ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, rating_add_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    rating_points_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(rating_points_start, pattern="^rating_points$")],
        states={
            RATING_POINTS_WHO: [CallbackQueryHandler(rating_points_who, pattern="^pts_who_")],
            RATING_POINTS_DELTA: [CallbackQueryHandler(rating_points_delta, pattern="^pts_delta_")],
            RATING_POINTS_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, rating_points_comment)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(onboard_conv)
    app.add_handler(remind_conv)
    app.add_handler(plan_conv)
    app.add_handler(rating_add_conv)
    app.add_handler(rating_points_conv)

    app.add_handler(CallbackQueryHandler(remind_list, pattern="^remind_list$"))
    app.add_handler(CallbackQueryHandler(remind_close_start, pattern="^remind_close$"))
    app.add_handler(CallbackQueryHandler(remind_close_done, pattern="^close_remind_"))
    app.add_handler(CallbackQueryHandler(plan_view, pattern="^plan_view$"))
    app.add_handler(CallbackQueryHandler(plan_check_start, pattern="^plan_check$"))
    app.add_handler(CallbackQueryHandler(plan_check_done, pattern="^check_task_"))
    app.add_handler(CallbackQueryHandler(rating_view, pattern="^rating_view$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))

    # Каждый час проверяем напоминалки с точным временем
    # Утренняя рассылка плана — отдельно в 9:00
    app.job_queue.run_repeating(morning_job, interval=60, first=10)

    print("Бот запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

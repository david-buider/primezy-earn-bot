import os
import sqlite3
import logging
from datetime import datetime
from html import escape

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ============================================================
# PRIMEZY TASKS
# Bot: @primezyearn_BOT
# Owner: @PRIMEZY_SALES
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "8990188046:AAEIvnkPnGamYWe27ayVDyYO5vklb7okOBw")

BOT_USERNAME = "primezyearn_BOT"
OWNER_USERNAME = "@PRIMEZY_SALES"
MAIN_ADMIN_ID = 8822268676

REFERRAL_REWARD = 50
MIN_WITHDRAWAL = 200
MAX_WITHDRAWAL = 400

DB_FILE = "primezy_tasks.db"

# Users must join these two communities before using the bot.
REQUIRED_CHATS = [
    {
        "chat_id": "@primezy_earn_payout",
        "url": "https://t.me/primezy_earn_payout",
        "name": "💰 𝑷𝑹𝑰𝑴𝑬𝒁𝒀 𝑷𝑨𝒀𝑶𝑼𝑻𝑺",
    },
    {
        "chat_id": "@primezy_orders",
        "url": "https://t.me/primezy_orders",
        "name": "📦 𝑷𝑹𝑰𝑴𝑬𝒁𝒀 𝑶𝑹𝑫𝑬𝑹𝑺",
    },
]

PAYOUT_CHANNEL = "@primezy_earn_payout"

# Optional support contact kept from the previous Primezy setup.
SUPPORT_USERNAME = "@PRIMEZY_SALES"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("PRIMEZY_TASKS")


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance INTEGER DEFAULT 0,
            task_earnings INTEGER DEFAULT 0,
            referral_earnings INTEGER DEFAULT 0,
            referred_by INTEGER,
            referral_paid INTEGER DEFAULT 0,
            joined_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            is_main INTEGER DEFAULT 0,
            added_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            link TEXT NOT NULL,
            reward INTEGER NOT NULL,
            proof_required INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            proof_file TEXT,
            status TEXT DEFAULT 'pending',
            reward INTEGER DEFAULT 0,
            created_at TEXT,
            UNIQUE(task_id, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            method TEXT NOT NULL,
            details TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            processed_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Main admin migration: make 8822268676 the main admin.
    cur.execute(
        "INSERT OR IGNORE INTO admins(user_id, is_main, added_at) VALUES (?, 1, ?)",
        (MAIN_ADMIN_ID, datetime.utcnow().isoformat()),
    )
    cur.execute(
        "UPDATE admins SET is_main=0 WHERE user_id != ?",
        (MAIN_ADMIN_ID,),
    )
    cur.execute(
        "UPDATE admins SET is_main=1 WHERE user_id=?",
        (MAIN_ADMIN_ID,),
    )

    conn.commit()
    conn.close()


# ============================================================
# USER HELPERS
# ============================================================

def save_user(tg_user, referred_by=None):
    conn = db()
    cur = conn.cursor()

    existing = cur.execute(
        "SELECT user_id, referred_by FROM users WHERE user_id=?",
        (tg_user.id,),
    ).fetchone()

    if existing:
        cur.execute("""
            UPDATE users
            SET username=?, first_name=?
            WHERE user_id=?
        """, (
            tg_user.username,
            tg_user.first_name,
            tg_user.id,
        ))
    else:
        cur.execute("""
            INSERT INTO users(
                user_id, username, first_name, balance,
                task_earnings, referral_earnings, referred_by,
                referral_paid, joined_at
            )
            VALUES (?, ?, ?, 0, 0, 0, ?, 0, ?)
        """, (
            tg_user.id,
            tg_user.username,
            tg_user.first_name,
            referred_by,
            datetime.utcnow().isoformat(),
        ))

    conn.commit()
    conn.close()


def get_user(user_id):
    conn = db()
    row = conn.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row


def add_balance(user_id, amount, kind="task"):
    conn = db()

    if kind == "referral":
        conn.execute("""
            UPDATE users
            SET balance=balance+?,
                referral_earnings=referral_earnings+?
            WHERE user_id=?
        """, (amount, amount, user_id))
    else:
        conn.execute("""
            UPDATE users
            SET balance=balance+?,
                task_earnings=task_earnings+?
            WHERE user_id=?
        """, (amount, amount, user_id))

    conn.commit()
    conn.close()


def deduct_balance(user_id, amount):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET balance=balance-?
        WHERE user_id=? AND balance>=?
    """, (amount, user_id, amount))
    ok = cur.rowcount > 0
    conn.commit()
    conn.close()
    return ok


# ============================================================
# ADMIN HELPERS
# ============================================================

def is_admin(user_id):
    conn = db()
    row = conn.execute(
        "SELECT 1 FROM admins WHERE user_id=?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row is not None


def is_main_admin(user_id):
    conn = db()
    row = conn.execute(
        "SELECT is_main FROM admins WHERE user_id=?",
        (user_id,),
    ).fetchone()
    conn.close()
    return bool(row and row["is_main"])


def admin_ids():
    conn = db()
    rows = conn.execute("SELECT user_id FROM admins").fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def add_admin(user_id):
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO admins(user_id, is_main, added_at) VALUES (?, 0, ?)",
        (user_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def remove_admin(user_id):
    if user_id == MAIN_ADMIN_ID:
        return False

    conn = db()
    conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    return True


# ============================================================
# MEMBERSHIP CHECK
# ============================================================

async def user_is_member(bot, user_id, chat_id):
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as exc:
        logger.warning("Membership check failed for %s: %s", chat_id, exc)
        return False


async def check_required_membership(bot, user_id):
    for chat in REQUIRED_CHATS:
        if not await user_is_member(bot, user_id, chat["chat_id"]):
            return False
    return True


async def send_join_screen(update, context):
    buttons = []
    for chat in REQUIRED_CHATS:
        buttons.append([
            InlineKeyboardButton(chat["name"], url=chat["url"])
        ])

    buttons.append([
        InlineKeyboardButton("✅ 𝑰'𝑽𝑬 𝑱𝑶𝑰𝑵𝑬𝑫", callback_data="verify_join")
    ])

    text = (
        "🔐 <b>𝑱𝑶𝑰𝑵 𝑹𝑬𝑸𝑼𝑰𝑹𝑬𝑴𝑬𝑵𝑻</b>\n\n"
        "Please join the required Primezy communities below.\n\n"
        "After joining them, tap <b>✅ I'VE JOINED</b> to continue."
    )

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
    else:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard(user_id):
    rows = [
        [KeyboardButton("🎯 𝑻𝑨𝑺𝑲𝑺"), KeyboardButton("👥 𝑹𝑬𝑭𝑬𝑹 & 𝑬𝑨𝑹𝑵")],
        [KeyboardButton("💰 𝑩𝑨𝑳𝑨𝑵𝑪𝑬"), KeyboardButton("💳 𝑾𝑰𝑻𝑯𝑫𝑹𝑨𝑾")],
        [KeyboardButton("📊 𝑺𝑻𝑨𝑻𝑺"), KeyboardButton("ℹ️ 𝑨𝑩𝑶𝑼𝑻")],
    ]

    if is_admin(user_id):
        rows.append([KeyboardButton("🛠 𝑨𝑫𝑴𝑰𝑵 𝑷𝑨𝑵𝑬𝑳")])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ 𝑨𝑫𝑫 𝑻𝑨𝑺𝑲"), KeyboardButton("📋 𝑴𝑨𝑵𝑨𝑮𝑬 𝑻𝑨𝑺𝑲𝑺")],
        [KeyboardButton("📸 𝑷𝑬𝑵𝑫𝑰𝑵𝑮 𝑷𝑹𝑶𝑶𝑭𝑺"), KeyboardButton("💳 𝑷𝑬𝑵𝑫𝑰𝑵𝑮 𝑾𝑰𝑻𝑯𝑫𝑹𝑨𝑾𝑨𝑳𝑺")],
        [KeyboardButton("👥 𝑼𝑺𝑬𝑹𝑺"), KeyboardButton("📢 𝑩𝑹𝑶𝑨𝑫𝑪𝑨𝑺𝑻")],
        [KeyboardButton("➕ 𝑨𝑫𝑫 𝑨𝑫𝑴𝑰𝑵"), KeyboardButton("➖ 𝑹𝑬𝑴𝑶𝑽𝑬 𝑨𝑫𝑴𝑰𝑵")],
        [KeyboardButton("👑 𝑨𝑫𝑴𝑰𝑵 𝑳𝑰𝑺𝑻"), KeyboardButton("⚙️ 𝑺𝑬𝑻𝑻𝑰𝑵𝑮𝑺")],
        [KeyboardButton("🏠 𝑴𝑨𝑰𝑵 𝑴𝑬𝑵𝑼")],
    ], resize_keyboard=True)


# ============================================================
# START / MAIN MENU
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    referred_by = None
    if context.args:
        try:
            possible = int(context.args[0])
            if possible != user.id:
                referred_by = possible
        except ValueError:
            referred_by = None

    save_user(user, referred_by=referred_by)

    # Pay referral only after the new user has passed the join gate.
    if referred_by:
        context.user_data["referrer_candidate"] = referred_by

    if not await check_required_membership(context.bot, user.id):
        await send_join_screen(update, context)
        return

    await activate_referral_if_needed(user.id, context)
    await send_main_menu(update, context)


async def activate_referral_if_needed(user_id, context):
    referrer_id = None
    conn = db()
    row = conn.execute(
        "SELECT referred_by, referral_paid FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()

    if row and row["referred_by"] and not row["referral_paid"]:
        referrer_id = row["referred_by"]

        ref_exists = conn.execute(
            "SELECT 1 FROM users WHERE user_id=?",
            (referrer_id,),
        ).fetchone()

        if ref_exists:
            conn.execute(
                "UPDATE users SET referral_paid=1 WHERE user_id=?",
                (user_id,),
            )
            conn.commit()
        else:
            referrer_id = None

    conn.close()

    if referrer_id:
        add_balance(referrer_id, REFERRAL_REWARD, kind="referral")

        try:
            await context.bot.send_message(
                referrer_id,
                "🎉 <b>𝑹𝑬𝑭𝑬𝑹𝑹𝑨𝑳 𝑹𝑬𝑾𝑨𝑹𝑫!</b>\n\n"
                f"Someone joined using your referral link.\n"
                f"💰 <b>+₦{REFERRAL_REWARD}</b> has been added to your balance.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


async def send_main_menu(update, context):
    user = update.effective_user
    text = (
        "🤖 <b>𝑷𝑹𝑰𝑴𝑬𝒁𝒀 𝑻𝑨𝑺𝑲𝑺</b>\n\n"
        f"Welcome, <b>{escape(user.first_name or 'User')}</b>! 👋\n\n"
        "Complete tasks, earn rewards, and invite friends.\n\n"
        "👇 <b>Choose an option from the keypad.</b>"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(user.id),
    )


async def verify_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not await check_required_membership(context.bot, query.from_user.id):
        await query.answer(
            "❌ You have not joined all required communities.",
            show_alert=True,
        )
        return

    await activate_referral_if_needed(query.from_user.id, context)

    try:
        await query.message.delete()
    except Exception:
        pass

    await context.bot.send_message(
        query.from_user.id,
        "✅ <b>Verification successful!</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(query.from_user.id),
    )


# ============================================================
# GLOBAL ACCESS CHECK
# ============================================================

async def access_allowed(update, context):
    user = update.effective_user
    if not user:
        return False

    if is_admin(user.id):
        return True

    if not await check_required_membership(context.bot, user.id):
        await send_join_screen(update, context)
        return False

    return True


# ============================================================
# TASKS
# ============================================================

async def show_tasks(update, context):
    if not await access_allowed(update, context):
        return

    conn = db()
    tasks = conn.execute("""
        SELECT * FROM tasks
        WHERE active=1
        ORDER BY id DESC
    """).fetchall()
    conn.close()

    if not tasks:
        await update.effective_message.reply_text(
            "🎯 <b>𝑻𝑨𝑺𝑲𝑺</b>\n\n"
            "There are no active tasks right now. Check back later.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(update.effective_user.id),
        )
        return

    buttons = []
    for task in tasks:
        buttons.append([
            InlineKeyboardButton(
                f"🎯 {task['title']} • ₦{task['reward']}",
                callback_data=f"task:{task['id']}",
            )
        ])

    await update.effective_message.reply_text(
        "🎯 <b>𝑨𝑽𝑨𝑰𝑳𝑨𝑩𝑳𝑬 𝑻𝑨𝑺𝑲𝑺</b>\n\n"
        "Choose a task below to see the instructions.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def open_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not await check_required_membership(context.bot, query.from_user.id):
        await query.answer("❌ Please join the required communities first.", show_alert=True)
        return

    try:
        task_id = int(query.data.split(":")[1])
    except Exception:
        return

    conn = db()
    task = conn.execute(
        "SELECT * FROM tasks WHERE id=? AND active=1",
        (task_id,),
    ).fetchone()
    completion = conn.execute("""
        SELECT * FROM task_completions
        WHERE task_id=? AND user_id=?
    """, (task_id, query.from_user.id)).fetchone()
    conn.close()

    if not task:
        await query.answer("Task not found.", show_alert=True)
        return

    if completion:
        status = completion["status"]
        if status == "approved":
            state = "✅ <b>Already completed and approved.</b>"
        elif status == "pending":
            state = "⏳ <b>Your proof is pending review.</b>"
        elif status == "rejected":
            state = "❌ <b>Your previous proof was rejected. You may submit again.</b>"
        else:
            state = ""
    else:
        state = ""

    buttons = [
        [InlineKeyboardButton("🔗 𝑶𝑷𝑬𝑵 𝑻𝑨𝑺𝑲", url=task["link"])],
    ]

    if not completion or completion["status"] == "rejected":
        buttons.append([
            InlineKeyboardButton(
                "✅ 𝑫𝑶𝑵𝑬 / 𝑺𝑼𝑩𝑴𝑰𝑻",
                callback_data=f"done:{task_id}",
            )
        ])

    buttons.append([
        InlineKeyboardButton("⬅️ 𝑩𝑨𝑪𝑲", callback_data="tasks_back")
    ])

    description = escape(task["description"] or "Complete the task as instructed.")

    text = (
        f"🎯 <b>{escape(task['title'])}</b>\n\n"
        f"{description}\n\n"
        f"💰 <b>Reward:</b> ₦{task['reward']}\n"
        f"📸 <b>Proof:</b> {'Required' if task['proof_required'] else 'Not required'}\n\n"
        f"{state}"
    )

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def done_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        task_id = int(query.data.split(":")[1])
    except Exception:
        return

    conn = db()
    task = conn.execute(
        "SELECT * FROM tasks WHERE id=? AND active=1",
        (task_id,),
    ).fetchone()
    completion = conn.execute("""
        SELECT * FROM task_completions
        WHERE task_id=? AND user_id=?
    """, (task_id, query.from_user.id)).fetchone()

    if not task:
        conn.close()
        await query.answer("Task not found.", show_alert=True)
        return

    if completion and completion["status"] == "approved":
        conn.close()
        await query.answer("You already completed this task.", show_alert=True)
        return

    if completion and completion["status"] == "pending":
        conn.close()
        await query.answer("Your proof is already pending review.", show_alert=True)
        return

    if not task["proof_required"]:
        conn.execute("""
            INSERT OR REPLACE INTO task_completions(
                task_id, user_id, proof_file, status, reward, created_at
            )
            VALUES (?, ?, NULL, 'approved', ?, ?)
        """, (
            task_id,
            query.from_user.id,
            task["reward"],
            datetime.utcnow().isoformat(),
        ))
        conn.commit()
        conn.close()

        add_balance(query.from_user.id, task["reward"], kind="task")

        await query.edit_message_text(
            "🎉 <b>𝑻𝑨𝑺𝑲 𝑪𝑶𝑴𝑷𝑳𝑬𝑻𝑬𝑫!</b>\n\n"
            f"💰 Reward added: <b>₦{task['reward']}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    conn.close()

    context.user_data["proof_task_id"] = task_id

    await query.message.reply_text(
        "📸 <b>𝑺𝑼𝑩𝑴𝑰𝑻 𝑷𝑹𝑶𝑶𝑭</b>\n\n"
        "Please send a screenshot/photo proving that you completed the task.\n\n"
        "Send the photo here now.",
        parse_mode=ParseMode.HTML,
    )


async def handle_proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    task_id = context.user_data.get("proof_task_id")

    if not task_id:
        return

    if not await access_allowed(update, context):
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    conn = db()
    task = conn.execute(
        "SELECT * FROM tasks WHERE id=? AND active=1",
        (task_id,),
    ).fetchone()

    if not task:
        conn.close()
        context.user_data.pop("proof_task_id", None)
        await update.message.reply_text("❌ This task is no longer available.")
        return

    existing = conn.execute("""
        SELECT * FROM task_completions
        WHERE task_id=? AND user_id=?
    """, (task_id, user.id)).fetchone()

    if existing and existing["status"] == "approved":
        conn.close()
        context.user_data.pop("proof_task_id", None)
        await update.message.reply_text("✅ This task is already approved.")
        return

    conn.execute("""
        INSERT OR REPLACE INTO task_completions(
            task_id, user_id, proof_file, status, reward, created_at
        )
        VALUES (?, ?, ?, 'pending', ?, ?)
    """, (
        task_id,
        user.id,
        file_id,
        task["reward"],
        datetime.utcnow().isoformat(),
    ))
    conn.commit()
    conn.close()

    context.user_data.pop("proof_task_id", None)

    await update.message.reply_text(
        "✅ <b>PROOF SUBMITTED</b>\n\n"
        "Your proof has been sent to the admin for review.\n"
        f"💰 Potential reward: ₦{task['reward']}\n\n"
        "Please wait for approval.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(user.id),
    )

    # Notify admins with the proof.
    caption = (
        "📸 <b>𝑵𝑬𝑾 𝑻𝑨𝑺𝑲 𝑷𝑹𝑶𝑶𝑭</b>\n\n"
        f"👤 User: <b>{escape(user.first_name or 'User')}</b>\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"🔗 Username: @{escape(user.username) if user.username else 'none'}\n"
        f"🎯 Task: <b>{escape(task['title'])}</b>\n"
        f"💰 Reward: ₦{task['reward']}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ APPROVE", callback_data=f"approveproof:{task_id}:{user.id}"),
            InlineKeyboardButton("❌ REJECT", callback_data=f"rejectproof:{task_id}:{user.id}"),
        ]
    ])

    for admin_id in admin_ids():
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.warning("Could not notify admin %s: %s", admin_id, exc)


# ============================================================
# REFERRAL
# ============================================================

async def referral(update, context):
    if not await access_allowed(update, context):
        return

    user = get_user(update.effective_user.id)
    balance = user["balance"] if user else 0

    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start={update.effective_user.id}"

    text = (
        "👥 <b>𝑹𝑬𝑭𝑬𝑹 & 𝑬𝑨𝑹𝑵</b>\n\n"
        f"Invite friends and earn <b>₦{REFERRAL_REWARD}</b> for each eligible referral.\n\n"
        f"💰 Your current balance: <b>₦{balance}</b>\n\n"
        "🔗 <b>Your referral link:</b>\n"
        f"<code>{link}</code>\n\n"
        "Share your link with friends and grow your rewards! 🚀"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(update.effective_user.id),
    )


# ============================================================
# BALANCE / STATS
# ============================================================

async def balance(update, context):
    if not await access_allowed(update, context):
        return

    user = get_user(update.effective_user.id)

    await update.effective_message.reply_text(
        "💰 <b>𝑴𝒀 𝑩𝑨𝑳𝑨𝑵𝑪𝑬</b>\n\n"
        f"💵 Available: <b>₦{user['balance']}</b>\n"
        f"🎯 Task earnings: <b>₦{user['task_earnings']}</b>\n"
        f"👥 Referral earnings: <b>₦{user['referral_earnings']}</b>\n\n"
        f"💳 Withdrawal range: <b>₦{MIN_WITHDRAWAL} - ₦{MAX_WITHDRAWAL}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(update.effective_user.id),
    )


async def stats(update, context):
    if not await access_allowed(update, context):
        return

    conn = db()
    users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    tasks = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE active=1"
    ).fetchone()["c"]
    completed = conn.execute(
        "SELECT COUNT(*) AS c FROM task_completions WHERE status='approved'"
    ).fetchone()["c"]
    referrals = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE referred_by IS NOT NULL"
    ).fetchone()["c"]
    conn.close()

    await update.effective_message.reply_text(
        "📊 <b>𝑷𝑹𝑰𝑴𝑬𝒁𝒀 𝑺𝑻𝑨𝑻𝑺</b>\n\n"
        f"👥 Users: <b>{users}</b>\n"
        f"🎯 Active tasks: <b>{tasks}</b>\n"
        f"✅ Approved task completions: <b>{completed}</b>\n"
        f"👥 Referrals: <b>{referrals}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(update.effective_user.id),
    )


# ============================================================
# WITHDRAWALS
# ============================================================

async def withdraw_start(update, context):
    if not await access_allowed(update, context):
        return

    user = get_user(update.effective_user.id)

    if user["balance"] < MIN_WITHDRAWAL:
        await update.effective_message.reply_text(
            "❌ <b>𝑰𝑵𝑺𝑼𝑭𝑭𝑰𝑪𝑰𝑬𝑵𝑻 𝑩𝑨𝑳𝑨𝑵𝑪𝑬</b>\n\n"
            f"Minimum withdrawal: <b>₦{MIN_WITHDRAWAL}</b>\n"
            f"Your balance: <b>₦{user['balance']}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(update.effective_user.id),
        )
        return

    context.user_data["withdraw_step"] = "amount"

    await update.effective_message.reply_text(
        "💳 <b>𝑾𝑰𝑻𝑯𝑫𝑹𝑨𝑾</b>\n\n"
        f"Enter the amount you want to withdraw.\n"
        f"Minimum: <b>₦{MIN_WITHDRAWAL}</b>\n"
        f"Maximum: <b>₦{MAX_WITHDRAWAL}</b>\n"
        f"Your balance: <b>₦{user['balance']}</b>\n\n"
        "Send only the amount, e.g. <code>200</code>.",
        parse_mode=ParseMode.HTML,
    )


async def process_withdrawal_text(update, context):
    step = context.user_data.get("withdraw_step")
    if not step:
        return False

    user = update.effective_user

    if step == "amount":
        try:
            amount = int(update.message.text.replace("₦", "").replace(",", "").strip())
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid whole number.")
            return True

        row = get_user(user.id)
        if amount < MIN_WITHDRAWAL or amount > MAX_WITHDRAWAL:
            await update.message.reply_text(
                f"❌ Amount must be between ₦{MIN_WITHDRAWAL} and ₦{MAX_WITHDRAWAL}."
            )
            return True

        if amount > row["balance"]:
            await update.message.reply_text("❌ You do not have enough balance.")
            return True

        context.user_data["withdraw_amount"] = amount
        context.user_data["withdraw_step"] = "method"

        await update.message.reply_text(
            "💳 <b>𝑾𝑰𝑻𝑯𝑫𝑹𝑨𝑾𝑨𝑳 𝑴𝑬𝑻𝑯𝑶𝑫</b>\n\n"
            "Send your preferred payout method.\n\n"
            "Example: <b>Bank Transfer</b>",
            parse_mode=ParseMode.HTML,
        )
        return True

    if step == "method":
        context.user_data["withdraw_method"] = update.message.text.strip()
        context.user_data["withdraw_step"] = "details"

        await update.message.reply_text(
            "🏦 <b>𝑷𝑨𝒀𝑶𝑼𝑻 𝑫𝑬𝑻𝑨𝑰𝑳𝑺</b>\n\n"
            "Send the account/payment details needed to receive your payout.\n\n"
            "For bank transfer, include:\n"
            "• Account name\n"
            "• Account number\n"
            "• Bank name",
            parse_mode=ParseMode.HTML,
        )
        return True

    if step == "details":
        amount = context.user_data.get("withdraw_amount")
        method = context.user_data.get("withdraw_method")
        details = update.message.text.strip()

        if not amount or not method:
            context.user_data.clear()
            await update.message.reply_text("❌ Withdrawal session expired. Please start again.")
            return True

        # Reserve the balance immediately so the same funds cannot be withdrawn twice.
        if not deduct_balance(user.id, amount):
            context.user_data.clear()
            await update.message.reply_text("❌ Your balance changed. Please try again.")
            return True

        conn = db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO withdrawals(
                user_id, amount, method, details, status, created_at
            )
            VALUES (?, ?, ?, ?, 'pending', ?)
        """, (
            user.id,
            amount,
            method,
            details,
            datetime.utcnow().isoformat(),
        ))
        withdrawal_id = cur.lastrowid
        conn.commit()
        conn.close()

        context.user_data.clear()

        await update.message.reply_text(
            "✅ <b>𝑾𝑰𝑻𝑯𝑫𝑹𝑨𝑾𝑨𝑳 𝑺𝑼𝑩𝑴𝑰𝑻𝑻𝑬𝑫</b>\n\n"
            f"💰 Amount: <b>₦{amount}</b>\n"
            f"💳 Method: <b>{escape(method)}</b>\n"
            f"🆔 Request: <code>#{withdrawal_id}</code>\n\n"
            "Your request has been sent for review.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(user.id),
        )

        caption = (
            "💳 <b>𝑵𝑬𝑾 𝑾𝑰𝑻𝑯𝑫𝑹𝑨𝑾𝑨𝑳</b>\n\n"
            f"🆔 Request: <code>#{withdrawal_id}</code>\n"
            f"👤 User: <b>{escape(user.first_name or 'User')}</b>\n"
            f"🆔 User ID: <code>{user.id}</code>\n"
            f"🔗 Username: @{escape(user.username) if user.username else 'none'}\n"
            f"💰 Amount: <b>₦{amount}</b>\n"
            f"💳 Method: <b>{escape(method)}</b>\n"
            f"🏦 Details: <code>{escape(details)}</code>"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ APPROVE",
                    callback_data=f"approvewd:{withdrawal_id}"
                ),
                InlineKeyboardButton(
                    "❌ REJECT",
                    callback_data=f"rejectwd:{withdrawal_id}"
                ),
            ]
        ])

        # Send to every admin, including MAIN_ADMIN_ID.
        for admin_id in admin_ids():
            try:
                await context.bot.send_message(
                    admin_id,
                    caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
            except Exception as exc:
                logger.warning("Withdrawal admin notification failed: %s", exc)

        # Also send to the Primezy payout channel.
        try:
            await context.bot.send_message(
                PAYOUT_CHANNEL,
                caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.warning("Payout channel notification failed: %s", exc)

        return True

    return False


async def approve_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ Admin only.", show_alert=True)
        return

    try:
        withdrawal_id = int(query.data.split(":")[1])
    except Exception:
        return

    conn = db()
    row = conn.execute(
        "SELECT * FROM withdrawals WHERE id=?",
        (withdrawal_id,),
    ).fetchone()

    if not row:
        conn.close()
        await query.answer("Withdrawal not found.", show_alert=True)
        return

    if row["status"] != "pending":
        conn.close()
        await query.answer("This withdrawal is already processed.", show_alert=True)
        return

    conn.execute("""
        UPDATE withdrawals
        SET status='approved', processed_at=?
        WHERE id=?
    """, (datetime.utcnow().isoformat(), withdrawal_id))
    conn.commit()
    conn.close()

    await query.answer("Approved.")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    try:
        await context.bot.send_message(
            row["user_id"],
            "✅ <b>𝑾𝑰𝑻𝑯𝑫𝑹𝑨𝑾𝑨𝑳 𝑨𝑷𝑷𝑹𝑶𝑽𝑬𝑫</b>\n\n"
            f"💰 Amount: <b>₦{row['amount']}</b>\n"
            f"🆔 Request: <code>#{withdrawal_id}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


async def reject_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ Admin only.", show_alert=True)
        return

    try:
        withdrawal_id = int(query.data.split(":")[1])
    except Exception:
        return

    conn = db()
    row = conn.execute(
        "SELECT * FROM withdrawals WHERE id=?",
        (withdrawal_id,),
    ).fetchone()

    if not row:
        conn.close()
        await query.answer("Withdrawal not found.", show_alert=True)
        return

    if row["status"] != "pending":
        conn.close()
        await query.answer("This withdrawal is already processed.", show_alert=True)
        return

    conn.execute("""
        UPDATE withdrawals
        SET status='rejected', processed_at=?
        WHERE id=?
    """, (datetime.utcnow().isoformat(), withdrawal_id))
    conn.commit()
    conn.close()

    # Return reserved funds after rejection.
    add_balance(row["user_id"], row["amount"], kind="task")

    await query.answer("Rejected and balance returned.")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    try:
        await context.bot.send_message(
            row["user_id"],
            "❌ <b>𝑾𝑰𝑻𝑯𝑫𝑹𝑨𝑾𝑨𝑳 𝑹𝑬𝑱𝑬𝑪𝑻𝑬𝑫</b>\n\n"
            f"💰 ₦{row['amount']} has been returned to your balance.\n"
            f"🆔 Request: <code>#{withdrawal_id}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


# ============================================================
# ABOUT
# ============================================================

async def about(update, context):
    if not await access_allowed(update, context):
        return

    await update.effective_message.reply_text(
        "ℹ️ <b>𝑨𝑩𝑶𝑼𝑻</b>\n\n"
        "Welcome to <b>𝑷𝑹𝑰𝑴𝑬𝒁𝒀 𝑻𝑨𝑺𝑲𝑺</b>.\n\n"
        "🎯 Complete tasks\n"
        "👥 Refer friends\n"
        "💰 Earn rewards\n"
        "💳 Request withdrawals\n\n"
        f"👑 Owner: <b>{OWNER_USERNAME}</b>\n"
        f"🛠 Created by: <b>{OWNER_USERNAME}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(update.effective_user.id),
    )


# ============================================================
# ADMIN PANEL
# ============================================================

async def admin_panel(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return

    await update.effective_message.reply_text(
        "🛠 <b>𝑷𝑹𝑰𝑴𝑬𝒁𝒀 𝑨𝑫𝑴𝑰𝑵 𝑷𝑨𝑵𝑬𝑳</b>\n\n"
        "Choose an admin action from the keypad.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(),
    )


async def add_task_start(update, context):
    if not is_admin(update.effective_user.id):
        return

    context.user_data["admin_step"] = "task_title"
    await update.effective_message.reply_text(
        "➕ <b>𝑨𝑫𝑫 𝑻𝑨𝑺𝑲</b>\n\n"
        "Send the task title.",
        parse_mode=ParseMode.HTML,
    )


async def manage_tasks(update, context):
    if not is_admin(update.effective_user.id):
        return

    conn = db()
    tasks = conn.execute(
        "SELECT * FROM tasks ORDER BY id DESC"
    ).fetchall()
    conn.close()

    if not tasks:
        await update.effective_message.reply_text("📋 No tasks created yet.")
        return

    buttons = []
    for task in tasks:
        state = "🟢" if task["active"] else "🔴"
        buttons.append([
            InlineKeyboardButton(
                f"{state} #{task['id']} {task['title']}",
                callback_data=f"admintask:{task['id']}",
            )
        ])

    await update.effective_message.reply_text(
        "📋 <b>𝑴𝑨𝑵𝑨𝑮𝑬 𝑻𝑨𝑺𝑲𝑺</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_task_details(update, context):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Admin only.", show_alert=True)
        return

    task_id = int(query.data.split(":")[1])

    conn = db()
    task = conn.execute(
        "SELECT * FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    conn.close()

    if not task:
        await query.answer("Task not found.", show_alert=True)
        return

    text = (
        f"🎯 <b>{escape(task['title'])}</b>\n\n"
        f"📝 {escape(task['description'] or '')}\n"
        f"🔗 {escape(task['link'])}\n"
        f"💰 Reward: ₦{task['reward']}\n"
        f"📸 Proof: {'Yes' if task['proof_required'] else 'No'}\n"
        f"📌 Status: {'Active' if task['active'] else 'Inactive'}"
    )

    buttons = [
        [InlineKeyboardButton(
            "🔄 " + ("DEACTIVATE" if task["active"] else "ACTIVATE"),
            callback_data=f"toggle:{task_id}"
        )],
        [InlineKeyboardButton(
            "🗑 DELETE",
            callback_data=f"delete:{task_id}"
        )],
    ]

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def toggle_task(update, context):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Admin only.", show_alert=True)
        return

    task_id = int(query.data.split(":")[1])

    conn = db()
    conn.execute("""
        UPDATE tasks
        SET active=CASE WHEN active=1 THEN 0 ELSE 1 END
        WHERE id=?
    """, (task_id,))
    conn.commit()
    conn.close()

    await query.answer("Task status updated.")
    await manage_tasks_from_callback(query, context)


async def manage_tasks_from_callback(query, context):
    conn = db()
    tasks = conn.execute(
        "SELECT * FROM tasks ORDER BY id DESC"
    ).fetchall()
    conn.close()

    buttons = []
    for task in tasks:
        state = "🟢" if task["active"] else "🔴"
        buttons.append([
            InlineKeyboardButton(
                f"{state} #{task['id']} {task['title']}",
                callback_data=f"admintask:{task['id']}",
            )
        ])

    await query.edit_message_text(
        "📋 <b>𝑴𝑨𝑵𝑨𝑮𝑬 𝑻𝑨𝑺𝑲𝑺</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def delete_task(update, context):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Admin only.", show_alert=True)
        return

    task_id = int(query.data.split(":")[1])

    conn = db()
    conn.execute("DELETE FROM task_completions WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()

    await query.answer("Task deleted.")
    await manage_tasks_from_callback(query, context)


# ============================================================
# ADMIN PROOF REVIEW
# ============================================================

async def approve_proof(update, context):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Admin only.", show_alert=True)
        return

    try:
        _, task_id, user_id = query.data.split(":")
        task_id = int(task_id)
        user_id = int(user_id)
    except Exception:
        return

    conn = db()
    completion = conn.execute("""
        SELECT * FROM task_completions
        WHERE task_id=? AND user_id=?
    """, (task_id, user_id)).fetchone()

    task = conn.execute(
        "SELECT * FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()

    if not completion or not task:
        conn.close()
        await query.answer("Proof/task not found.", show_alert=True)
        return

    if completion["status"] != "pending":
        conn.close()
        await query.answer("Already processed.", show_alert=True)
        return

    conn.execute("""
        UPDATE task_completions
        SET status='approved'
        WHERE task_id=? AND user_id=?
    """, (task_id, user_id))
    conn.commit()
    conn.close()

    add_balance(user_id, task["reward"], kind="task")

    await query.answer("Proof approved.")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    try:
        await context.bot.send_message(
            user_id,
            "🎉 <b>𝑻𝑨𝑺𝑲 𝑨𝑷𝑷𝑹𝑶𝑽𝑬𝑫!</b>\n\n"
            f"🎯 Task: <b>{escape(task['title'])}</b>\n"
            f"💰 Reward: <b>₦{task['reward']}</b> added to your balance.",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


async def reject_proof(update, context):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Admin only.", show_alert=True)
        return

    try:
        _, task_id, user_id = query.data.split(":")
        task_id = int(task_id)
        user_id = int(user_id)
    except Exception:
        return

    conn = db()
    completion = conn.execute("""
        SELECT * FROM task_completions
        WHERE task_id=? AND user_id=?
    """, (task_id, user_id)).fetchone()

    if not completion:
        conn.close()
        await query.answer("Proof not found.", show_alert=True)
        return

    if completion["status"] != "pending":
        conn.close()
        await query.answer("Already processed.", show_alert=True)
        return

    conn.execute("""
        UPDATE task_completions
        SET status='rejected'
        WHERE task_id=? AND user_id=?
    """, (task_id, user_id))
    conn.commit()
    conn.close()

    await query.answer("Proof rejected.")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    try:
        await context.bot.send_message(
            user_id,
            "❌ <b>𝑻𝑨𝑺𝑲 𝑷𝑹𝑶𝑶𝑭 𝑹𝑬𝑱𝑬𝑪𝑻𝑬𝑫</b>\n\n"
            "Your proof was not approved. You may try the task again if it is still available.",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


async def pending_proofs(update, context):
    if not is_admin(update.effective_user.id):
        return

    conn = db()
    rows = conn.execute("""
        SELECT tc.*, t.title, u.username, u.first_name
        FROM task_completions tc
        JOIN tasks t ON t.id=tc.task_id
        JOIN users u ON u.user_id=tc.user_id
        WHERE tc.status='pending'
        ORDER BY tc.id DESC
        LIMIT 30
    """).fetchall()
    conn.close()

    if not rows:
        await update.effective_message.reply_text("📸 No pending proofs.")
        return

    text = "📸 <b>𝑷𝑬𝑵𝑫𝑰𝑵𝑮 𝑷𝑹𝑶𝑶𝑭𝑺</b>\n\n"
    for row in rows:
        text += (
            f"🆔 #{row['id']} | User: <code>{row['user_id']}</code>\n"
            f"🎯 {escape(row['title'])}\n"
            f"👤 @{escape(row['username']) if row['username'] else 'none'}\n\n"
        )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# ADMIN WITHDRAWAL LIST
# ============================================================

async def pending_withdrawals(update, context):
    if not is_admin(update.effective_user.id):
        return

    conn = db()
    rows = conn.execute("""
        SELECT w.*, u.username, u.first_name
        FROM withdrawals w
        JOIN users u ON u.user_id=w.user_id
        WHERE w.status='pending'
        ORDER BY w.id DESC
        LIMIT 30
    """).fetchall()
    conn.close()

    if not rows:
        await update.effective_message.reply_text("💳 No pending withdrawals.")
        return

    for row in rows:
        text = (
            "💳 <b>𝑷𝑬𝑵𝑫𝑰𝑵𝑮 𝑾𝑰𝑻𝑯𝑫𝑹𝑨𝑾𝑨𝑳</b>\n\n"
            f"🆔 Request: <code>#{row['id']}</code>\n"
            f"👤 User ID: <code>{row['user_id']}</code>\n"
            f"🔗 @{escape(row['username']) if row['username'] else 'none'}\n"
            f"💰 Amount: <b>₦{row['amount']}</b>\n"
            f"💳 Method: {escape(row['method'])}\n"
            f"🏦 Details: <code>{escape(row['details'])}</code>"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ APPROVE",
                    callback_data=f"approvewd:{row['id']}"
                ),
                InlineKeyboardButton(
                    "❌ REJECT",
                    callback_data=f"rejectwd:{row['id']}"
                ),
            ]
        ])
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


# ============================================================
# ADMIN USERS
# ============================================================

async def show_users(update, context):
    if not is_admin(update.effective_user.id):
        return

    conn = db()
    total = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    rows = conn.execute("""
        SELECT user_id, username, first_name, balance
        FROM users
        ORDER BY joined_at DESC
        LIMIT 25
    """).fetchall()
    conn.close()

    text = f"👥 <b>𝑼𝑺𝑬𝑹𝑺</b>\n\nTotal: <b>{total}</b>\n\n"
    for row in rows:
        text += (
            f"• <code>{row['user_id']}</code> "
            f"@{escape(row['username']) if row['username'] else 'none'} "
            f"— ₦{row['balance']}\n"
        )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(),
    )


# ============================================================
# ADMIN ADD / REMOVE
# ============================================================

async def add_admin_start(update, context):
    if not is_main_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Main admin only.")
        return

    context.user_data["admin_step"] = "add_admin"
    await update.effective_message.reply_text(
        "➕ Send the Telegram user ID of the new admin."
    )


async def remove_admin_start(update, context):
    if not is_main_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Main admin only.")
        return

    context.user_data["admin_step"] = "remove_admin"
    await update.effective_message.reply_text(
        "➖ Send the Telegram user ID of the admin to remove."
    )


async def admin_list(update, context):
    if not is_admin(update.effective_user.id):
        return

    conn = db()
    rows = conn.execute(
        "SELECT * FROM admins ORDER BY is_main DESC, added_at ASC"
    ).fetchall()
    conn.close()

    text = "👑 <b>𝑨𝑫𝑴𝑰𝑵 𝑳𝑰𝑺𝑻</b>\n\n"
    for row in rows:
        role = "👑 MAIN ADMIN" if row["is_main"] else "🛠 ADMIN"
        text += f"{role} — <code>{row['user_id']}</code>\n"

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(),
    )


# ============================================================
# ADMIN ADD TASK FLOW
# ============================================================

async def process_admin_text(update, context):
    step = context.user_data.get("admin_step")
    if not step:
        return False

    user_id = update.effective_user.id
    if not is_admin(user_id):
        context.user_data.pop("admin_step", None)
        return False

    text = update.message.text.strip()

    if step == "task_title":
        context.user_data["task_title"] = text
        context.user_data["admin_step"] = "task_description"
        await update.message.reply_text("📝 Send the task description.")
        return True

    if step == "task_description":
        context.user_data["task_description"] = text
        context.user_data["admin_step"] = "task_link"
        await update.message.reply_text(
            "🔗 Send the task link (https://...)."
        )
        return True

    if step == "task_link":
        if not text.startswith(("http://", "https://", "tg://")):
            await update.message.reply_text("❌ Please send a valid link.")
            return True

        context.user_data["task_link"] = text
        context.user_data["admin_step"] = "task_reward"
        await update.message.reply_text("💰 Send the reward amount in ₦.")
        return True

    if step == "task_reward":
        try:
            reward = int(text.replace("₦", "").replace(",", ""))
            if reward <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Send a valid positive number.")
            return True

        context.user_data["task_reward"] = reward
        context.user_data["admin_step"] = "task_proof"
        await update.message.reply_text(
            "📸 Should proof be required?\n\n"
            "Reply <b>YES</b> or <b>NO</b>.",
            parse_mode=ParseMode.HTML,
        )
        return True

    if step == "task_proof":
        if text.lower() not in ("yes", "no"):
            await update.message.reply_text("Reply YES or NO.")
            return True

        proof_required = 1 if text.lower() == "yes" else 0

        conn = db()
        conn.execute("""
            INSERT INTO tasks(
                title, description, link, reward,
                proof_required, active, created_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?)
        """, (
            context.user_data["task_title"],
            context.user_data["task_description"],
            context.user_data["task_link"],
            context.user_data["task_reward"],
            proof_required,
            datetime.utcnow().isoformat(),
        ))
        conn.commit()
        conn.close()

        context.user_data.clear()

        await update.message.reply_text(
            "✅ <b>TASK CREATED SUCCESSFULLY</b>\n\n"
            "The task is now active and visible to users.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_keyboard(),
        )
        return True

    if step == "add_admin":
        try:
            new_admin = int(text)
        except ValueError:
            await update.message.reply_text("❌ Send a valid numeric Telegram ID.")
            return True

        add_admin(new_admin)
        context.user_data.pop("admin_step", None)

        await update.message.reply_text(
            f"✅ <code>{new_admin}</code> has been added as an admin.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_keyboard(),
        )
        return True

    if step == "remove_admin":
        try:
            target = int(text)
        except ValueError:
            await update.message.reply_text("❌ Send a valid numeric Telegram ID.")
            return True

        if target == MAIN_ADMIN_ID:
            await update.message.reply_text("❌ The main admin cannot be removed.")
            return True

        remove_admin(target)
        context.user_data.pop("admin_step", None)

        await update.message.reply_text(
            f"✅ <code>{target}</code> has been removed.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_keyboard(),
        )
        return True

    return False


# ============================================================
# BROADCAST
# ============================================================

async def broadcast_start(update, context):
    if not is_admin(update.effective_user.id):
        return

    context.user_data["admin_step"] = "broadcast"
    await update.effective_message.reply_text(
        "📢 Send the message you want to broadcast to all users.\n\n"
        "Use /cancel to cancel."
    )


async def broadcast_message(update, context):
    if context.user_data.get("admin_step") != "broadcast":
        return False

    if not is_admin(update.effective_user.id):
        context.user_data.clear()
        return False

    context.user_data.clear()

    conn = db()
    rows = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()

    sent = 0
    failed = 0

    for row in rows:
        try:
            await context.bot.copy_message(
                chat_id=row["user_id"],
                from_chat_id=update.effective_chat.id,
                message_id=update.effective_message.message_id,
            )
            sent += 1
        except Exception:
            failed += 1

    await update.effective_message.reply_text(
        f"📢 <b>BROADCAST COMPLETE</b>\n\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(),
    )
    return True


# ============================================================
# TEXT ROUTER
# ============================================================

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # Cancel current input flow.
    if text == "/cancel" or text == "❌ 𝑪𝑨𝑵𝑪𝑬𝑳":
        context.user_data.clear()
        await update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=admin_keyboard() if is_admin(user_id) else main_keyboard(user_id),
        )
        return

    # Withdrawal flow gets priority.
    if await process_withdrawal_text(update, context):
        return

    # Admin multi-step flow.
    if await process_admin_text(update, context):
        return

    # Broadcast.
    if context.user_data.get("admin_step") == "broadcast":
        await broadcast_message(update, context)
        return

    # Main menu.
    if text == "🎯 𝑻𝑨𝑺𝑲𝑺":
        await show_tasks(update, context)
        return

    if text == "👥 𝑹𝑬𝑭𝑬𝑹 & 𝑬𝑨𝑹𝑵":
        await referral(update, context)
        return

    if text == "💰 𝑩𝑨𝑳𝑨𝑵𝑪𝑬":
        await balance(update, context)
        return

    if text == "💳 𝑾𝑰𝑻𝑯𝑫𝑹𝑨𝑾":
        await withdraw_start(update, context)
        return

    if text == "📊 𝑺𝑻𝑨𝑻𝑺":
        await stats(update, context)
        return

    if text == "ℹ️ 𝑨𝑩𝑶𝑼𝑻":
        await about(update, context)
        return

    if text == "🛠 𝑨𝑫𝑴𝑰𝑵 𝑷𝑨𝑵𝑬𝑳":
        await admin_panel(update, context)
        return

    # Admin keypad.
    if text == "➕ 𝑨𝑫𝑫 𝑻𝑨𝑺𝑲":
        await add_task_start(update, context)
        return

    if text == "📋 𝑴𝑨𝑵𝑨𝑮𝑬 𝑻𝑨𝑺𝑲𝑺":
        await manage_tasks(update, context)
        return

    if text == "📸 𝑷𝑬𝑵𝑫𝑰𝑵𝑮 𝑷𝑹𝑶𝑶𝑭𝑺":
        await pending_proofs(update, context)
        return

    if text == "💳 𝑷𝑬𝑵𝑫𝑰𝑵𝑮 𝑾𝑰𝑻𝑯𝑫𝑹𝑨𝑾𝑨𝑳𝑺":
        await pending_withdrawals(update, context)
        return

    if text == "👥 𝑼𝑺𝑬𝑹𝑺":
        await show_users(update, context)
        return

    if text == "📢 𝑩𝑹𝑶𝑨𝑫𝑪𝑨𝑺𝑻":
        await broadcast_start(update, context)
        return

    if text == "➕ 𝑨𝑫𝑫 𝑨𝑫𝑴𝑰𝑵":
        await add_admin_start(update, context)
        return

    if text == "➖ 𝑹𝑬𝑴𝑶𝑽𝑬 𝑨𝑫𝑴𝑰𝑵":
        await remove_admin_start(update, context)
        return

    if text == "👑 𝑨𝑫𝑴𝑰𝑵 𝑳𝑰𝑺𝑻":
        await admin_list(update, context)
        return

    if text == "⚙️ 𝑺𝑬𝑻𝑻𝑰𝑵𝑮𝑺":
        if is_admin(user_id):
            await update.message.reply_text(
                "⚙️ <b>𝑺𝑬𝑻𝑻𝑰𝑵𝑮𝑺</b>\n\n"
                f"👥 Referral reward: <b>₦{REFERRAL_REWARD}</b>\n"
                f"💳 Minimum withdrawal: <b>₦{MIN_WITHDRAWAL}</b>\n"
                f"💳 Maximum withdrawal: <b>₦{MAX_WITHDRAWAL}</b>\n"
                f"👑 Main admin: <code>{MAIN_ADMIN_ID}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_keyboard(),
            )
        return

    if text == "🏠 𝑴𝑨𝑰𝑵 𝑴𝑬𝑵𝑼":
        await send_main_menu(update, context)
        return

    await update.message.reply_text(
        "Please choose an option from the keypad.",
        reply_markup=admin_keyboard() if is_admin(user_id) else main_keyboard(user_id),
    )


# ============================================================
# CALLBACK ROUTER
# ============================================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""

    if data == "verify_join":
        await verify_join(update, context)
    elif data.startswith("task:"):
        await open_task(update, context)
    elif data.startswith("done:"):
        await done_task(update, context)
    elif data == "tasks_back":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        await show_tasks_from_callback(query, context)
    elif data.startswith("admintask:"):
        await admin_task_details(update, context)
    elif data.startswith("toggle:"):
        await toggle_task(update, context)
    elif data.startswith("delete:"):
        await delete_task(update, context)
    elif data.startswith("approveproof:"):
        await approve_proof(update, context)
    elif data.startswith("rejectproof:"):
        await reject_proof(update, context)
    elif data.startswith("approvewd:"):
        await approve_withdrawal(update, context)
    elif data.startswith("rejectwd:"):
        await reject_withdrawal(update, context)


async def show_tasks_from_callback(query, context):
    conn = db()
    tasks = conn.execute("""
        SELECT * FROM tasks
        WHERE active=1
        ORDER BY id DESC
    """).fetchall()
    conn.close()

    if not tasks:
        await context.bot.send_message(
            query.from_user.id,
            "🎯 <b>𝑻𝑨𝑺𝑲𝑺</b>\n\nNo active tasks right now.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(query.from_user.id),
        )
        return

    buttons = []
    for task in tasks:
        buttons.append([
            InlineKeyboardButton(
                f"🎯 {task['title']} • ₦{task['reward']}",
                callback_data=f"task:{task['id']}",
            )
        ])

    await context.bot.send_message(
        query.from_user.id,
        "🎯 <b>𝑨𝑽𝑨𝑰𝑳𝑨𝑩𝑳𝑬 𝑻𝑨𝑺𝑲𝑺</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ============================================================
# /ADMIN
# ============================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return

    await admin_panel(update, context)


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(update, context):
    logger.exception("Unhandled exception", exc_info=context.error)


# ============================================================
# MAIN
# ============================================================

def main():
    if BOT_TOKEN == "PASTE_NEW_BOT_TOKEN_HERE":
        raise RuntimeError(
            "Set BOT_TOKEN as an environment variable or replace "
            "PASTE_NEW_BOT_TOKEN_HERE with your new bot token."
        )

    init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.PHOTO, handle_proof_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    logger.info("PRIMEZY TASKS BOT STARTING...")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
  

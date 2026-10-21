import os
import sqlite3
import threading
from flask import Flask, render_template_string, request, redirect, flash, abort
import telebot
from telebot import types

# ==========================================
# কনফিগারেশন ও ক্রেডেনশিয়াল
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8805137216:AAHwel2juBm2yEi5Gtd3kZSfMDPBwF86OFg")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7351236002"))
SECRET_ADMIN_KEY = os.getenv("ADMIN_KEY", "admin12345")
APP_URL = os.getenv("APP_URL", "")
DEFAULT_CHANNEL_URL = "https://t.me/hfjgfjfn"
DEFAULT_TARGET_CHANNEL = "@hfjgfjfn"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)
app.secret_key = os.urandom(24)

user_states = {}

# ==========================================
# SQLite ডাটাবেজ ক্লাস (Thread-Safe)
# ==========================================
class Database:
    def __init__(self, db_name="bot_data.db"):
        self.db_name = db_name
        self.lock = threading.Lock()
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_name, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        balance REAL DEFAULT 0.0,
                        counted_members INTEGER DEFAULT 0,
                        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        name TEXT,
                        invite_link TEXT UNIQUE,
                        member_count INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS withdrawals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        method TEXT,
                        phone TEXT,
                        amount REAL,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                conn.commit()

        if not self.get_setting("target_channel_id"):
            self.set_setting("target_channel_id", DEFAULT_TARGET_CHANNEL)
        if not self.get_setting("official_channel_url"):
            self.set_setting("official_channel_url", DEFAULT_CHANNEL_URL)
        if not self.get_setting("member_rate"):
            self.set_setting("member_rate", "1.0")

    def get_or_create_user(self, user_id):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                user = cur.fetchone()
                if not user:
                    cur.execute("INSERT INTO users (user_id, balance, counted_members) VALUES (?, 0.0, 0)", (user_id,))
                    conn.commit()
                    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                    user = cur.fetchone()
                return dict(user)

    def add_link(self, user_id, name, invite_link):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO links (user_id, name, invite_link) VALUES (?, ?, ?)", (user_id, name, invite_link))
                conn.commit()

    def get_user_links(self, user_id):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM links WHERE user_id = ? ORDER BY id DESC", (user_id,))
                return [dict(r) for r in cur.fetchall()]

    def delete_link(self, link_id, user_id):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM links WHERE id = ? AND user_id = ?", (link_id, user_id))
                conn.commit()
                return cur.rowcount > 0

    def find_link(self, invite_link):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM links WHERE invite_link = ?", (invite_link,))
                r = cur.fetchone()
                return dict(r) if r else None

    def increment_member(self, link_id, user_id):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                rate = float(self.get_setting("member_rate", "1.0"))
                cur.execute("UPDATE links SET member_count = member_count + 1 WHERE id = ?", (link_id,))
                cur.execute("UPDATE users SET counted_members = counted_members + 1, balance = balance + ? WHERE user_id = ?", (rate, user_id))
                conn.commit()
                return rate

    def create_withdrawal(self, user_id, method, phone, amount):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
                cur.execute("INSERT INTO withdrawals (user_id, method, phone, amount, status) VALUES (?, ?, ?, ?, 'pending')",
                            (user_id, method, phone, amount))
                conn.commit()
                return cur.lastrowid

    def get_recent_withdrawals(self, user_id, limit=5):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM withdrawals WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
                return [dict(r) for r in cur.fetchall()]

    def get_all_withdrawals(self):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM withdrawals ORDER BY id DESC")
                return [dict(r) for r in cur.fetchall()]

    def get_withdrawal(self, req_id):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM withdrawals WHERE id = ?", (req_id,))
                r = cur.fetchone()
                return dict(r) if r else None

    def update_withdrawal_status(self, req_id, status):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("UPDATE withdrawals SET status = ? WHERE id = ?", (status, req_id))
                conn.commit()

    def refund_user(self, user_id, amount):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
                conn.commit()

    def set_setting(self, key, value):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
                conn.commit()

    def get_setting(self, key, default=None):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
                r = cur.fetchone()
                return r["value"] if r else default

    def get_all_users(self):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM users")
                return [dict(r) for r in cur.fetchall()]

    def get_pending_sum(self, user_id):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT SUM(amount) as s FROM withdrawals WHERE user_id = ? AND status = 'pending'", (user_id,))
                r = cur.fetchone()
                return r["s"] or 0.0

    def get_total_stats(self):
        with self.lock:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) as total_users, SUM(balance) as total_bal, SUM(counted_members) as total_members FROM users")
                u_stats = dict(cur.fetchone())
                cur.execute("SELECT COUNT(*) as pending_wd, SUM(amount) as pending_amt FROM withdrawals WHERE status = 'pending'")
                w_stats = dict(cur.fetchone())
                cur.execute("SELECT SUM(amount) as paid_amt FROM withdrawals WHERE status = 'approved'")
                paid_stats = dict(cur.fetchone())
                return {**u_stats, **w_stats, **paid_stats}

db = Database()

# ==========================================
# কিবোর্ড ও UI টেক্সট
# ==========================================
def main_menu_markup():
    channel_url = db.get_setting("official_channel_url", DEFAULT_CHANNEL_URL)
    markup = types.InlineKeyboardMarkup(row_width=2)
    b1 = types.InlineKeyboardButton("+ Create Link", callback_data="btn_create_link")
    b2 = types.InlineKeyboardButton("🔗 My Link List", callback_data="btn_link_list")
    b3 = types.InlineKeyboardButton("🗑 Delete Link", callback_data="btn_delete_link")
    b4 = types.InlineKeyboardButton("🌿 Withdraw", callback_data="btn_withdraw")
    b5 = types.InlineKeyboardButton("👤 My Account", callback_data="btn_my_account")
    b6 = types.InlineKeyboardButton("🔄 Refresh", callback_data="btn_refresh")
    b7 = types.InlineKeyboardButton("📢 Official Channel", url=channel_url)
    markup.add(b1, b2)
    markup.add(b3, b4)
    markup.add(b5, b6)
    markup.add(b7)
    return markup

def get_dashboard_text(user_id):
    u = db.get_or_create_user(user_id)
    rate = db.get_setting("member_rate", "1.0")
    return (
        f"👤 <b>My Dashboard</b>\n"
        f"Active Members: {u['counted_members']}\n"
        f"Rate: ৳{rate} / Member\n"
        f"Available Balance: ৳{u['balance']:.2f}"
    )

# ==========================================
# টেলিগ্রাম বট হ্যান্ডলার
# ==========================================
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    uid = msg.from_user.id
    db.get_or_create_user(uid)
    bot.send_message(msg.chat.id, get_dashboard_text(uid), reply_markup=main_menu_markup())

@bot.message_handler(commands=['help', 'admin_help'])
def cmd_admin_help(msg):
    if msg.from_user.id != ADMIN_ID:
        return
    admin_web = f"{APP_URL}/admin?key={SECRET_ADMIN_KEY}" if APP_URL else f"/admin?key={SECRET_ADMIN_KEY}"
    help_text = (
        f"⚡ <b>অ্যাডমিন কন্ট্রোল প্যানেল:</b>\n\n"
        f"🌐 <b>Web Admin Link:</b>\n{admin_web}\n\n"
        f"🔹 <b>/set_app_url &lt;url&gt;</b> - ওয়েবসাইটের মূল লিংক সেট করুন (যেমন: https://xxx.koyeb.app)\n"
        f"🔹 <b>/set_rate &lt;টাকা&gt;</b> - মেম্বার রেট পরিবর্তন করুন\n"
        f"🔹 <b>/send &lt;user_id&gt; &lt;মেসেজ&gt;</b> - নির্দিষ্ট ইউজারকে সরাসরি এসএমএস দিন\n"
        f"🔹 <b>/broadcast &lt;মেসেজ&gt;</b> - সবাইকে এক ক্লিকে নোটিশ পাঠান\n"
        f"🔹 <b>/stats</b> - মোট ইউজার ও পেমেন্ট হিস্ট্রি দেখুন\n"
        f"🔹 <b>/set_channel &lt;@username&gt;</b> - রেফারেল চ্যানেল পরিবর্তন করুন\n"
        f"🔹 <b>/replace_channel &lt;@username&gt;</b> - ৫০% ক্ষতিপূরণ সহ চ্যানেল পরিবর্তন করুন"
    )
    bot.reply_to(msg, help_text)

@bot.message_handler(commands=['set_app_url'])
def cmd_set_app_url(msg):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "ব্যবহার: /set_app_url https://your-app.koyeb.app")
        return
    global APP_URL
    APP_URL = parts[1].strip().rstrip("/")
    db.set_setting("app_url", APP_URL)
    bot.reply_to(msg, f"✅ Website URL সেট করা হয়েছে:\n{APP_URL}")

@bot.message_handler(commands=['set_rate'])
def cmd_set_rate(msg):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "ব্যবহার: /set_rate 2")
        return
    try:
        new_rate = float(parts[1])
        db.set_setting("member_rate", str(new_rate))
        bot.reply_to(msg, f"✅ নতুন রেট: <b>৳{new_rate} / Member</b>")
    except ValueError:
        bot.reply_to(msg, "সঠিক সংখ্যা লিখুন।")

@bot.message_handler(commands=['send'])
def cmd_send_direct(msg):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.reply_to(msg, "ব্যবহার: /send <user_id> <মেসেজ>")
        return
    try:
        bot.send_message(parts[1], f"📩 <b>Admin Message:</b>\n\n{parts[2]}")
        bot.reply_to(msg, "✅ মেসেজ পাঠানো হয়েছে!")
    except Exception as e:
        bot.reply_to(msg, f"❌ ব্যর্থ: {str(e)}")

@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(msg):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "ব্যবহার: /broadcast <নোটিশ>")
        return
    text = parts[1]
    users = db.get_all_users()
    count = 0
    for u in users:
        try:
            bot.send_message(u["user_id"], f"📢 <b>Important Notice:</b>\n\n{text}")
            count += 1
        except Exception:
            pass
    bot.reply_to(msg, f"✅ সর্বমোট {count} জন ইউজারের কাছে পাঠানো হয়েছে।")

@bot.message_handler(commands=['stats'])
def cmd_stats(msg):
    if msg.from_user.id != ADMIN_ID:
        return
    s = db.get_total_stats()
    bot.reply_to(msg, (
        f"📊 <b>পরিসংখ্যান:</b>\n"
        f"👥 মোট ইউজার: {s['total_users'] or 0}\n"
        f"🔗 মোট মেম্বার: {s['total_members'] or 0}\n"
        f"💳 বর্তমান ব্যালেন্স: ৳{s['total_bal'] or 0.0:.2f}\n"
        f"⏳ অপেক্ষমান উইথড্র: {s['pending_wd'] or 0} (৳{s['pending_amt'] or 0.0:.2f})\n"
        f"✅ পরিশোধিত: ৳{s['paid_amt'] or 0.0:.2f}"
    ))

@bot.message_handler(commands=['set_channel'])
def cmd_set_channel(msg):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "ব্যবহার: /set_channel @hfjgfjfn")
        return
    cid = parts[1].strip()
    if "t.me/" in cid:
        cid = "@" + cid.split("t.me/")[-1].replace("/", "")
    elif not cid.startswith("-100") and not cid.startswith("@"):
        cid = "@" + cid
    db.set_setting("target_channel_id", cid)
    bot.reply_to(msg, f"✅ চ্যানেল সেট হয়েছে: <code>{cid}</code>")

@bot.message_handler(commands=['replace_channel'])
def cmd_replace_channel(msg):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "ব্যবহার: /replace_channel @new_channel")
        return
    new_cid = parts[1].strip()
    if "t.me/" in new_cid:
        new_cid = "@" + new_cid.split("t.me/")[-1].replace("/", "")
    elif not new_cid.startswith("-100") and not new_cid.startswith("@"):
        new_cid = "@" + new_cid

    db.set_setting("target_channel_id", new_cid)
    users = db.get_all_users()
    for u in users:
        uid = u["user_id"]
        comp_base = u["balance"] + db.get_pending_sum(uid)
        if comp_base <= 0:
            continue
        credited = round(comp_base * 0.5, 2)
        with db.lock:
            with db.get_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE withdrawals SET status = 'returned' WHERE user_id = ? AND status = 'pending'", (uid,))
                c.execute("UPDATE users SET balance = ? WHERE user_id = ?", (credited, uid))
                conn.commit()
        try:
            bot.send_message(uid, (
                f"✅ <b>Channel Replacement Credit</b>\n\n"
                f"পুরনো Channel-এর unpaid eligible balance থেকে 50% compensation আপনার নতুন balance-এ যোগ হয়েছে।\n\n"
                f"💰 Compensation Base: ৳{comp_base:.2f}\n"
                f"✅ Credited Amount: ৳{credited:.2f}\n\n"
                f"আগে Approved/Paid হয়ে যাওয়া Withdrawal এই হিসাবের মধ্যে ধরা হয়নি।\n\n"
                f"Pending Withdrawal compensation হিসাবের মধ্যে রাখা হয়েছে এবং পুরনো request Return করা হয়েছে।"
            ))
        except Exception:
            pass
    bot.reply_to(msg, "✅ ৫০% ক্ষতিপূরণ সফলভাবে সম্পন্ন হয়েছে!")

# --- বাটন কলব্যাক হ্যান্ডলার ---
@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    uid = call.from_user.id
    cid = call.message.chat.id
    data = call.data

    if data == "btn_refresh":
        bot.answer_callback_query(call.id, "Refreshed!")
        try:
            bot.edit_message_text(chat_id=cid, message_id=call.message.message_id, text=get_dashboard_text(uid), reply_markup=main_menu_markup())
        except Exception:
            pass

    elif data == "btn_create_link":
        bot.answer_callback_query(call.id)
        sent = bot.send_message(cid, "Link Name দিন। উদাহরণ: Facebook Campaign, Team Rahim, August Marketing")
        bot.register_next_step_handler(sent, step_create_link)

    elif data == "btn_link_list":
        bot.answer_callback_query(call.id)
        links = db.get_user_links(uid)
        if not links:
            bot.send_message(cid, "আপনার কোনো Active Link তৈরি করা নেই।")
            return
        for lk in links:
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("🔗 Open Link", url=lk['invite_link']),
                types.InlineKeyboardButton("📋 Copy Link", switch_inline_query=lk['invite_link'])
            )
            markup.add(types.InlineKeyboardButton("🗑 Delete Link", callback_data=f"del_{lk['id']}"))
            txt = f"🏷 <b>Name:</b> {lk['name']}\n🔗 <b>Link:</b> {lk['invite_link']}\n👥 Current Members: {lk['member_count']}"
            bot.send_message(cid, txt, reply_markup=markup)

    elif data == "btn_delete_link":
        bot.answer_callback_query(call.id)
        links = db.get_user_links(uid)
        if not links:
            bot.send_message(cid, "মুছে ফেলার মতো কোনো লিংক নেই।")
            return
        markup = types.InlineKeyboardMarkup()
        for lk in links:
            markup.add(types.InlineKeyboardButton(f"❌ {lk['name']}", callback_data=f"del_{lk['id']}"))
        bot.send_message(cid, "যে লিংকটি ডিলিট করতে চান নির্বাচন করুন:", reply_markup=markup)

    elif data.startswith("del_"):
        lid = int(data.split("_")[1])
        if db.delete_link(lid, uid):
            bot.answer_callback_query(call.id, "লিংক ডিলিট সম্পন্ন!")
            bot.send_message(cid, "🗑 লিংকটি সফলভাবে মুছে ফেলা হয়েছে।")
        else:
            bot.answer_callback_query(call.id, "ডিলিট করা যায়নি।")

    elif data == "btn_my_account":
        bot.answer_callback_query(call.id)
        show_account_details(cid, uid)

    elif data == "btn_withdraw":
        bot.answer_callback_query(call.id)
        u = db.get_or_create_user(uid)
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("📱 bKash", callback_data="wmethod_bKash"),
            types.InlineKeyboardButton("📱 Nagad", callback_data="wmethod_Nagad")
        )
        markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="w_cancel"))
        bot.send_message(cid, f"Withdraw\nAvailable: ৳{u['balance']:.2f}\nMethod নির্বাচন করুন:", reply_markup=markup)

    elif data.startswith("wmethod_"):
        bot.answer_callback_query(call.id)
        user_states[uid] = {"method": data.split("_")[1]}
        sent = bot.send_message(cid, f"{user_states[uid]['method']} নম্বর লিখুন:")
        bot.register_next_step_handler(sent, step_withdraw_phone)

    elif data == "w_cancel":
        bot.answer_callback_query(call.id, "বাতিল")
        bot.send_message(cid, "Withdrawal বাতিল করা হয়েছে।")

def step_create_link(msg):
    uid = msg.from_user.id
    name = msg.text.strip()
    target_channel = db.get_setting("target_channel_id", DEFAULT_TARGET_CHANNEL)
    try:
        invite = bot.create_chat_invite_link(chat_id=target_channel, name=name, creates_join_request=False)
        db.add_link(uid, name, invite.invite_link)
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("🔗 Open Link", url=invite.invite_link), types.InlineKeyboardButton("📋 Copy Link", switch_inline_query=invite.invite_link))
        bot.send_message(msg.chat.id, f"✅ <b>লিংক তৈরি সম্পন্ন!</b>\n\n🏷 <b>Name:</b> {name}\n🔗 <b>Link:</b> <code>{invite.invite_link}</code>", reply_markup=markup)
    except Exception as e:
        bot.reply_to(msg, f"❌ ব্যর্থ: {str(e)}\nবটকে চ্যানেলে Admin বানিয়েছেন কিনা নিশ্চিত করুন।")

def step_withdraw_phone(msg):
    uid = msg.from_user.id
    if uid not in user_states: return
    user_states[uid]["phone"] = msg.text.strip()
    sent = bot.send_message(msg.chat.id, "কত টাকা Withdraw করতে চান? শুধু amount লিখুন।")
    bot.register_next_step_handler(sent, step_withdraw_amount)

def step_withdraw_amount(msg):
    uid = msg.from_user.id
    if uid not in user_states: return
    try:
        amount = float(msg.text.strip())
        if amount <= 0: raise ValueError()
    except ValueError:
        bot.reply_to(msg, "❌ সঠিক সংখ্যা লিখুন।")
        user_states.pop(uid, None)
        return

    user = db.get_or_create_user(uid)
    if amount > user["balance"]:
        bot.reply_to(msg, f"❌ অপর্যাপ্ত ব্যালেন্স! আপনার ব্যালেন্স: ৳{user['balance']:.2f}")
        user_states.pop(uid, None)
        return

    method = user_states[uid]["method"]
    phone = user_states[uid]["phone"]
    user_states.pop(uid, None)

    req_id = db.create_withdrawal(uid, method, phone, amount)
    bot.send_message(msg.chat.id, "Withdrawal Request Submitted... Status: Pending")

    current_url = db.get_setting("app_url", APP_URL)
    web_admin_link = f"{current_url}/admin?key={SECRET_ADMIN_KEY}" if current_url else f"#/admin?key={SECRET_ADMIN_KEY}"

    admin_markup = types.InlineKeyboardMarkup()
    admin_markup.add(types.InlineKeyboardButton("🌐 Open Web Dashboard", url=web_admin_link))

    alert = (
        f"📥 <b>New Withdrawal Request!</b>\n"
        f"👤 User ID: <code>{uid}</code>\n"
        f"📱 Method: {method}\n"
        f"📞 Number: <code>{phone}</code>\n"
        f"💰 Amount: ৳{amount:.2f}\n"
        f"🆔 Request ID: #{req_id}\n\n"
        f"🔗 <b>পেমেন্ট ক্লিয়ার করতে নিচের ওয়েব লিংকে ক্লিক করুন:</b>"
    )
    try:
        bot.send_message(ADMIN_ID, alert, reply_markup=admin_markup)
    except Exception as e:
        print("Admin alert failed:", e)

def show_account_details(chat_id, user_id):
    u = db.get_or_create_user(user_id)
    links = db.get_user_links(user_id)
    recent = db.get_recent_withdrawals(user_id, 5)
    recent_str = "".join([f"\n#{r['id']} • {r['method'].upper()} • ৳{r['amount']:.2f} • {r['status']}" for r in recent]) or "\nকোনো লেনদেন রেকর্ড পাওয়া যায়নি।"
    text = (
        f"👤 <b>My Account</b>\n\n"
        f"🆔 Telegram ID: <code>{user_id}</code>\n"
        f"🔗 Active Links: {len(links)}\n"
        f"✅ Counted Members: {u['counted_members']}\n"
        f"⏳ Pending 24H: 0\n"
        f"🛡 Estimated Payable: {u['counted_members']}\n"
        f"💰 Pending Earning: ৳0.00\n"
        f"💳 Available Balance: ৳{u['balance']:.2f}\n\n"
        f"💸 Recent Withdrawals"
        f"{recent_str}"
    )
    bot.send_message(chat_id, text)

@bot.chat_member_handler()
def on_member_join(update: types.ChatMemberUpdated):
    if update.old_chat_member.status in ["left", "kicked"] and update.new_chat_member.status in ["member", "administrator"]:
        inv = update.invite_link
        if inv and inv.invite_link:
            link = db.find_link(inv.invite_link)
            if link:
                rate = db.increment_member(link["id"], link["user_id"])
                try:
                    bot.send_message(link["user_id"], f"🎉 আপনার রেফারে নতুন মেম্বার যুক্ত হয়েছে!\n🏷 {link['name']}\n💰 ৳{rate:.2f} ব্যালেন্সে যোগ হয়েছে।")
                except Exception:
                    pass

# ==========================================
# ফ্লাস্ক ওয়েব অ্যাডমিন ড্যাশবোর্ড (HTML UI)
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Payout Admin Panel</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen">
    <div class="max-w-6xl mx-auto px-4 py-8">
        <header class="flex flex-col md:flex-row justify-between items-center mb-8 border-b border-slate-800 pb-4 gap-4">
            <div>
                <h1 class="text-2xl font-bold text-emerald-400">⚡ Automated Payout Dashboard</h1>
                <p class="text-sm text-slate-400">Telegram Channel Referral & Payment Release</p>
            </div>
            <a href="/admin?key={{ secret_key }}" class="bg-slate-800 hover:bg-slate-700 px-4 py-2 rounded text-sm border border-slate-700">🔄 Refresh Table</a>
        </header>

        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="mb-4 p-4 rounded {% if category == 'success' %}bg-emerald-800 text-emerald-100{% else %}bg-rose-800 text-rose-100{% endif %}">
                        {{ message }}
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        <div class="bg-slate-800 rounded-xl overflow-hidden shadow-2xl border border-slate-700">
            <div class="overflow-x-auto">
                <table class="w-full text-left">
                    <thead>
                        <tr class="bg-slate-950 text-slate-400 text-xs uppercase border-b border-slate-700">
                            <th class="p-4">Req ID</th>
                            <th class="p-4">User ID</th>
                            <th class="p-4">Method</th>
                            <th class="p-4">Phone Number</th>
                            <th class="p-4">Amount</th>
                            <th class="p-4">Status</th>
                            <th class="p-4">Action</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-slate-700 text-sm">
                        {% for r in withdrawals %}
                        <tr class="hover:bg-slate-750 transition">
                            <td class="p-4 font-mono font-bold text-slate-300">#{{ r.id }}</td>
                            <td class="p-4 font-mono text-emerald-400 select-all">{{ r.user_id }}</td>
                            <td class="p-4 uppercase font-semibold">{{ r.method }}</td>
                            <td class="p-4 font-mono select-all text-white font-bold">{{ r.phone }}</td>
                            <td class="p-4 font-bold text-emerald-400">৳{{ "%.2f"|format(r.amount) }}</td>
                            <td class="p-4">
                                <span class="px-2.5 py-1 rounded-full text-xs font-semibold {% if r.status == 'approved' %}bg-emerald-900/80 text-emerald-300 border border-emerald-500/30{% elif r.status == 'returned' %}bg-amber-900/80 text-amber-300 border border-amber-500/30{% else %}bg-blue-900/80 text-blue-300 border border-blue-500/30{% endif %}">
                                    {{ r.status }}
                                </span>
                            </td>
                            <td class="p-4">
                                {% if r.status == 'pending' %}
                                <div class="flex space-x-2">
                                    <form action="/admin/action" method="POST" class="inline">
                                        <input type="hidden" name="key" value="{{ secret_key }}">
                                        <input type="hidden" name="req_id" value="{{ r.id }}">
                                        <input type="hidden" name="action" value="approve">
                                        <button class="bg-emerald-600 hover:bg-emerald-500 text-white text-xs px-3 py-1.5 rounded font-medium shadow">Release / Approve</button>
                                    </form>
                                    <form action="/admin/action" method="POST" class="inline">
                                        <input type="hidden" name="key" value="{{ secret_key }}">
                                        <input type="hidden" name="req_id" value="{{ r.id }}">
                                        <input type="hidden" name="action" value="return">
                                        <button class="bg-rose-600 hover:bg-rose-500 text-white text-xs px-3 py-1.5 rounded font-medium shadow">Return</button>
                                    </form>
                                </div>
                                {% else %}
                                <span class="text-xs text-slate-500 font-mono">Processed</span>
                                {% endif %}
                            </td>
                        </tr>
                        {% else %}
                        <tr><td colspan="7" class="p-8 text-center text-slate-400">কোনো উইথড্র রিকোয়েস্ট পাওয়া যায়নি।</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</body>
</html>
"""

@app.route("/")
def home():
    return "Referral Payout Web Service is running 24/7 on Koyeb."

@app.route("/admin")
def web_admin():
    k = request.args.get("key", "")
    if k != SECRET_ADMIN_KEY:
        abort(403)
    withdrawals = db.get_all_withdrawals()
    return render_template_string(HTML_TEMPLATE, withdrawals=withdrawals, secret_key=k)

@app.route("/admin/action", methods=["POST"])
def admin_action():
    k = request.form.get("key")
    if k != SECRET_ADMIN_KEY:
        abort(403)
    req_id = int(request.form.get("req_id"))
    act = request.form.get("action")
    req_item = db.get_withdrawal(req_id)

    if not req_item or req_item["status"] != "pending":
        flash("এই রিকোয়েস্টটি ইতোমধ্যেই প্রসেস করা হয়েছে।", "error")
        return redirect(f"/admin?key={k}")

    if act == "approve":
        db.update_withdrawal_status(req_id, "approved")
        members = int(req_item["amount"])
        msg = f"{members} মেম্বারের পেমেন্ট ক্লিয়ার"
        try:
            bot.send_message(req_item["user_id"], msg)
        except Exception:
            pass
        flash(f"#{req_id} সফলভাবে Approved করা হয়েছে এবং ইউজারকে নোটিফিকেশন পাঠানো হয়েছে!", "success")

    elif act == "return":
        db.update_withdrawal_status(req_id, "returned")
        db.refund_user(req_item["user_id"], req_item["amount"])
        try:
            bot.send_message(req_item["user_id"], f"⚠️ আপনার #{req_id} নম্বর উইথড্রয়াল রিকোয়েস্টটি Return করা হয়েছে এবং টাকা একাউন্টে ফেরত দেওয়া হয়েছে।")
        except Exception:
            pass
        flash(f"#{req_id} Return ও ব্যালেন্স রিফান্ড করা হয়েছে!", "error")

    return redirect(f"/admin?key={k}")

# ==========================================
# অ্যাপ্লিকেশন রানার
# ==========================================
def start_bot():
    bot.remove_webhook()
    print("Telegram Bot Polling started...")
    bot.infinity_polling(allowed_updates=["message", "callback_query", "chat_member"])

if __name__ == "__main__":
    t = threading.Thread(target=start_bot, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

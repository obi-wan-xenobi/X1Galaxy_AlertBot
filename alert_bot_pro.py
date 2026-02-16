import json
import os
import sqlite3
import logging
import requests
from datetime import datetime
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- CONFIGURATION ---
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
PUBLIC_CHANNEL_ID = "-1002361138833" # Your X1Galaxy_Alerts Channel
DATA_FILE = "/var/www/app.x1galaxy.io/all_validator_data.json"
TPS_FILE = "/var/www/app.x1galaxy.io/epoch_tps_stats.json"
DB_FILE = "/root/xenobi_website/bot_users.db"

# Settings
WHALE_THRESHOLD = 50000  # XNT change to trigger whale alert
LAMPORTS = 1_000_000_000
FOOTER = "\n\n📊 <i>More data at <a href='https://x1galaxy.io'>x1galaxy.io</a></i>"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- DATABASE LOGIC ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions 
                 (user_id TEXT, identity TEXT, last_state TEXT, UNIQUE(user_id, identity))''')
    c.execute('''CREATE TABLE IF NOT EXISTS network_state (key TEXT PRIMARY KEY, value TEXT)''')
    conn.commit()
    conn.close()

def get_net_state(key, default=None):
    conn = sqlite3.connect(DB_FILE)
    res = conn.execute("SELECT value FROM network_state WHERE key=?", (key,)).fetchone()
    conn.close()
    return res[0] if res else default

def set_net_state(key, value):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR REPLACE INTO network_state (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

# --- DATA HELPERS ---
def load_data():
    if not os.path.exists(DATA_FILE): return {}
    try:
        with open(DATA_FILE, 'r') as f: return json.load(f)
    except: return {}

def find_validator(query, validators):
    query = query.strip().lower()
    if not query: return None
    # 1. Exact ID
    for v in validators:
        if query == v.get('identity', '').lower(): return v
    # 2. Exact Name
    for v in validators:
        if query == (v.get('name') or "").lower(): return v
    # 3. Partial Name
    for v in validators:
        if query in (v.get('name') or "").lower(): return v
    return None

def format_xnt(lamports):
    return f"{int(lamports / LAMPORTS):,}"

# --- BOT COMMANDS ---
async def post_init(application):
    """Sets up the hamburger menu in Telegram UI."""
    commands = [
        BotCommand("start", "Help & Instructions"),
        BotCommand("stats", "Snapshot: /stats <name/id>"),
        BotCommand("calc", "ROI: /calc <amount> <name>"),
        BotCommand("top", "Stake Leaderboard"),
        BotCommand("subscribe", "Get Private Alerts"),
        BotCommand("list", "Your Subscriptions"),
        BotCommand("unsubscribe", "Stop Alerts")
    ]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🛰 <b>X1Galaxy Bot: Network Intelligence</b>\n\n"
        "I provide real-time X1 validator stats and private alerts.\n\n"
        "<b>Commands:</b>\n"
        "• /stats <code>[name/id]</code> - Live performance card\n"
        "• /calc <code>[qty] [name]</code> - Estimated ROI\n"
        "• /top - Network Stake Top 10\n"
        "• /subscribe <code>[id]</code> - Get private DM alerts\n\n"
        "<i>Alerts for status, commission, and rewards are sent privately.</i>" + FOOTER
    )
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❓ <b>Usage:</b> /stats <code>[name or identity]</code>", parse_mode='HTML')
        return
    
    data = load_data()
    v = find_validator(" ".join(context.args), data.get('validators', []))
    if not v:
        await update.message.reply_text("❌ Validator not found.")
        return

    sorted_vals = sorted(data['validators'], key=lambda x: x.get('activatedStake', 0), reverse=True)
    rank = next((i for i, val in enumerate(sorted_vals) if val['identity'] == v['identity']), 0) + 1
    
    public_tip = ""
    if update.effective_chat.type != 'private':
        public_tip = "\n\n🔔 <i>Want private alerts for this node? DM me /start</i>"

    text = (
        f"🛰 <b>Validator Snapshot</b>\n"
        f"<b>Name:</b> {v.get('name', 'Unnamed Node')}\n"
        f"<b>ID:</b> <code>{v['identity']}</code>\n"
        f"----------------------------------\n"
        f"🏆 <b>Rank:</b> #{rank} / {len(sorted_vals)}\n"
        f"🚦 <b>Status:</b> {'🟢 Active' if v['status'] == 'Active' else '🔴 Delinquent'}\n"
        f"💰 <b>Stake:</b> {format_xnt(v.get('activatedStake', 0))} XNT\n"
        f"⚖️ <b>Comm:</b> {v.get('commission', '?')}%\n"
        f"📊 <b>Skip Rate:</b> {v.get('skipRate1', 0):.2f}%\n"
        f"🎁 <b>Recent Rewards:</b> +{v.get('rewards_last_1_epochs_xnt', 0):.2f} XNT"
        + FOOTER + public_tip
    )
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def calc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❓ <b>Usage:</b> /calc [amount] [name]", parse_mode='HTML')
        return
    
    try: amount = float(context.args[0].replace(',', ''))
    except: await update.message.reply_text("Invalid amount."); return

    data = load_data()
    v = find_validator(" ".join(context.args[1:]), data.get('validators', []))
    if not v: await update.message.reply_text("Validator not found."); return

    comm = v.get('commission', 10)
    est_apr = 0.07 * (1 - (comm/100))
    epoch_yield = (amount * est_apr) / 182 
    
    text = (
        f"💰 <b>ROI Estimate: {v.get('name', 'Node')}</b>\n"
        f"Principle: {amount:,.0f} XNT\n"
        f"----------------------------------\n"
        f"💎 <b>Per Epoch:</b> ~{epoch_yield:.4f} XNT\n"
        f"📅 <b>Annual:</b> ~{(amount * est_apr):,.2f} XNT\n"
        f"📈 <b>Net APY:</b> {(est_apr * 100):.2f}%" + FOOTER
    )
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    sorted_vals = sorted(data.get('validators', []), key=lambda x: x.get('activatedStake', 0), reverse=True)[:10]
    text = "🏆 <b>X1 Stake Leaderboard</b>\n\n"
    for i, v in enumerate(sorted_vals):
        name = v.get('name') or v['identity'][:8]
        text += f"{i+1}. <b>{name}</b> - {format_xnt(v.get('activatedStake', 0))} XNT\n"
    await update.message.reply_text(text + FOOTER, parse_mode='HTML', disable_web_page_preview=True)

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_type = update.effective_chat.type
    if not context.args:
        await update.message.reply_text("Usage: /subscribe <identity>")
        return
    
    identity = context.args[0]
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR REPLACE INTO subscriptions (user_id, identity, last_state) VALUES (?, ?, ?)", (user_id, identity, "{}"))
    conn.commit()
    conn.close()

    if chat_type == 'private':
        await update.message.reply_text(f"✅ Subscribed! I will DM you alerts for this validator.")
    else:
        await update.message.reply_text(
            f"✅ <b>Subscription Recorded!</b>\n\n"
            f"I will send alerts for <code>{identity[:8]}...</code> to you <b>privately</b>.\n"
            f"⚠️ <i>Must have clicked /start in my DMs first.</i>", parse_mode='HTML')

async def list_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    conn = sqlite3.connect(DB_FILE)
    subs = conn.execute("SELECT identity FROM subscriptions WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    if not subs:
        await update.message.reply_text("You have no active subscriptions.")
        return
    text = "<b>Your Private Subscriptions:</b>\n"
    for s in subs: text += f"• <code>{s[0]}</code>\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage: /unsubscribe <identity>")
        return
    identity = context.args[0]
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM subscriptions WHERE user_id = ? AND identity = ?", (user_id, identity))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"❌ Unsubscribed from alerts for <code>{identity}</code>", parse_mode='HTML')

# --- THE ENGINE (Check Job) ---
async def check_data_job(context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data: return
    validators = {v['identity']: v for v in data.get('validators', [])}
    
    # 1. WHALE WATCHER (Public Channel)
    prev_stakes = json.loads(get_net_state("stake_map", "{}"))
    curr_stakes = {idn: v.get('activatedStake', 0) for idn, v in validators.items()}
    for idn, stake in curr_stakes.items():
        if idn in prev_stakes:
            diff = (stake - prev_stakes[idn]) / LAMPORTS
            if abs(diff) >= WHALE_THRESHOLD:
                name = validators[idn].get('name') or f"<code>{idn[:8]}</code>"
                emoji = "🐋" if diff > 0 else "📉"
                verb = "delegated to" if diff > 0 else "withdrawn from"
                alert = f"{emoji} <b>WHALE MOVE</b>\n\n{abs(diff):,.0f} XNT was {verb} <b>{name}</b>!"
                await context.bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=alert + FOOTER, parse_mode='HTML')
    set_net_state("stake_map", json.dumps(curr_stakes))

    # 2. EPOCH REPORT
    if validators:
        sample_v = next(iter(validators.values()))
        if sample_v.get('epochCreditsFull'):
            curr_ep = sample_v['epochCreditsFull'][-1][0]
            last_ep = int(get_net_state("last_epoch", 0))
            if curr_ep > last_ep and last_ep != 0:
                report = f"🎆 <b>NEW EPOCH: {curr_ep}</b>\n\nNetwork Active Stake: {format_xnt(data.get('active_stake', 0))} XNT"
                await context.bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=report + FOOTER, parse_mode='HTML')
                set_net_state("last_epoch", curr_ep)

    # 3. PRIVATE SUBSCRIPTION ALERTS
    conn = sqlite3.connect(DB_FILE)
    subscriptions = conn.execute("SELECT user_id, identity, last_state FROM subscriptions").fetchall()
    for user_id, identity, last_state_json in subscriptions:
        if identity not in validators: continue
        curr, prev = validators[identity], json.loads(last_state_json)
        name = curr.get('name') or f"<code>{identity[:8]}</code>"
        pings = []
        if prev.get('status') and curr['status'] != prev['status']:
            pings.append(f"Status changed to <b>{curr['status']}</b>")
        if prev.get('comm') is not None and curr['commission'] != prev['comm']:
            pings.append(f"Commission: {prev['comm']}% ➡️ {curr['commission']}%")
        
        if pings:
            try: await context.bot.send_message(chat_id=user_id, text=f"🛰 <b>Alert: {name}</b>\n" + "\n".join(pings) + FOOTER, parse_mode='HTML')
            except: pass
        
        new_state = json.dumps({"status": curr['status'], "comm": curr['commission']})
        conn.execute("UPDATE subscriptions SET last_state = ? WHERE user_id = ? AND identity = ?", (new_state, user_id, identity))
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("calc", calc_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("list", list_subs))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.job_queue.run_repeating(check_data_job, interval=180, first=10)
    app.run_polling()

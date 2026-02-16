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
PUBLIC_CHANNEL_ID = "-1002361138833" 
DATA_FILE = "/var/www/app.x1galaxy.io/all_validator_data.json"
DB_FILE = "/root/xenobi_website/bot_users.db"

# Settings
WHALE_THRESHOLD = 50000 
LAMPORTS = 1_000_000_000
FOOTER = "\n\n📊 <i>More data at <a href='https://x1galaxy.io'>x1galaxy.io</a></i>"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- DATABASE LOGIC ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Stores user subscriptions with custom skip limits
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions 
                 (user_id TEXT, identity TEXT, last_state TEXT, skip_limit INTEGER DEFAULT 1, 
                 UNIQUE(user_id, identity))''')
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
    for v in validators:
        if query == v.get('identity', '').lower(): return v
    for v in validators:
        if query == (v.get('name') or "").lower(): return v
    for v in validators:
        if query in (v.get('name') or "").lower(): return v
    return None

def format_xnt(lamports):
    if lamports is None: return "0"
    return f"{int(lamports / LAMPORTS):,}"

# --- BOT COMMANDS ---
async def post_init(application):
    commands = [
        BotCommand("start", "Help & Instructions"),
        BotCommand("stats", "Snapshot: /stats <name/id>"),
        BotCommand("calc", "ROI: /calc <amount> <name>"),
        BotCommand("set_limit", "Set skip alert limit (e.g. /set_limit 5)"),
        BotCommand("all_nodes_rewards", "Rewards table (DM Only)"),
        BotCommand("top", "Stake Leaderboard"),
        BotCommand("subscribe", "Get Private DM Alerts"),
        BotCommand("list", "Your Subscriptions"),
        BotCommand("unsubscribe", "Stop Alerts")
    ]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🛰 <b>X1Galaxy Bot: Network Intelligence</b>\n\n"
        "<b>Commands:</b>\n"
        "• /stats <code>[name/id]</code> - Live card\n"
        "• /set_limit <code>[number]</code> - Alert when skips > X\n"
        "• /subscribe <code>[id]</code> - Get private alerts\n\n"
        "<i>I monitor performance, status, and account balances.</i>" + FOOTER
    )
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❓ Usage: /stats <code>[name/id]</code>", parse_mode='HTML')
        return
    
    data = load_data()
    v = find_validator(" ".join(context.args), data.get('validators', []))
    if not v:
        await update.message.reply_text("❌ Validator not found.")
        return

    sorted_vals = sorted(data['validators'], key=lambda x: x.get('activatedStake', 0), reverse=True)
    rank = next((i for i, val in enumerate(sorted_vals) if val['identity'] == v['identity']), 0) + 1
    
    # Calculate Balance
    balance_lamports = v.get('voteBalanceLamports', 0)
    balance_formatted = f"{balance_lamports / LAMPORTS:,.2f} XNT"

    text = (
        f"🛰 <b>Validator Snapshot</b>\n"
        f"<b>Name:</b> {v.get('name', 'Unnamed Node')}\n"
        f"<b>ID:</b> <code>{v['identity']}</code>\n"
        f"----------------------------------\n"
        f"🏆 <b>Rank:</b> #{rank} / {len(sorted_vals)}\n"
        f"🚦 <b>Status:</b> {'🟢 Active' if v['status'] == 'Active' else '🔴 Delinquent'}\n"
        f"💰 <b>Active Stake:</b> {format_xnt(v.get('activatedStake', 0))} XNT\n"
        f"🏦 <b>Vote Balance:</b> {balance_formatted}\n"
        f"⚖️ <b>Commission:</b> {v.get('commission', '?')}%\n"
        f"📊 <b>Skips (Epoch):</b> {v.get('skipped_slots_1_epochs', 0)}\n"
        f"🎁 <b>Last Rewards:</b> +{v.get('rewards_last_1_epochs_xnt', 0):.2f} XNT"
        + FOOTER
    )
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage: /set_limit <number>\nExample: <code>/set_limit 5</code>", parse_mode='HTML')
        return
    try:
        limit = int(context.args[0])
        conn = sqlite3.connect(DB_FILE)
        conn.execute("UPDATE subscriptions SET skip_limit = ? WHERE user_id = ?", (limit, user_id))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Your skip alert threshold has been set to <b>{limit} blocks</b> per epoch.", parse_mode='HTML')
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid whole number.")

async def calc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❓ Usage: /calc [amount] [name]", parse_mode='HTML')
        return
    try: amount = float(context.args[0].replace(',', ''))
    except: await update.message.reply_text("❌ Invalid amount."); return
    data = load_data()
    v = find_validator(" ".join(context.args[1:]), data.get('validators', []))
    if not v: await update.message.reply_text("❌ Validator not found."); return
    comm = v.get('commission', 10)
    est_apr = 0.07 * (1 - (comm/100))
    epoch_yield = (amount * est_apr) / 182 
    text = (f"💰 <b>ROI Estimate: {v.get('name', 'Node')}</b>\n"
            f"Principle: {amount:,.0f} XNT\n"
            f"----------------------------------\n"
            f"💎 <b>Per Epoch:</b> ~{epoch_yield:.4f} XNT\n"
            f"📈 <b>Net APY:</b> {(est_apr * 100):.2f}%" + FOOTER)
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    sorted_vals = sorted(data.get('validators', []), key=lambda x: x.get('activatedStake', 0), reverse=True)[:10]
    text = "🏆 <b>Stake Leaderboard</b>\n\n"
    for i, v in enumerate(sorted_vals):
        name = v.get('name') or v['identity'][:8]
        text += f"{i+1}. <b>{name}</b> - {format_xnt(v.get('activatedStake', 0))} XNT\n"
    await update.message.reply_text(text + FOOTER, parse_mode='HTML', disable_web_page_preview=True)

async def all_nodes_rewards_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        await update.message.reply_text("⚠️ This command is only available in private DM.", parse_mode='HTML')
        return
    data = load_data()
    active_nodes = [v for v in data.get("validators", []) if v.get("status") == "Active"]
    active_nodes.sort(key=lambda x: x.get("activatedStake", 0), reverse=True)
    header = "🛰 <b>Last Epoch Rewards</b>\n<code> # |  Rew  | Name           | ID    </code>\n<code>---|-------|----------------|-------</code>\n"
    rows = []
    for i, v in enumerate(active_nodes):
        row = f"<code>{str(i+1).ljust(2)} | {str(round(v.get('rewards_last_1_epochs_xnt',0),1)).rjust(5)} | {v.get('name','?').ljust(14)[:14]} | {v['identity'][:4]}..{v['identity'][-2:]}</code>"
        rows.append(row)
    for i in range(0, len(rows), 30):
        await update.message.reply_text((header if i==0 else "") + "\n".join(rows[i:i+30]) + (FOOTER if i+30>=len(rows) else ""), parse_mode='HTML')

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args: return
    identity = context.args[0]
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR IGNORE INTO subscriptions (user_id, identity, last_state) VALUES (?, ?, ?)", (user_id, identity, "{}"))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Alerts active for <code>{identity[:8]}...</code>", parse_mode='HTML')

async def list_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    conn = sqlite3.connect(DB_FILE)
    subs = conn.execute("SELECT identity FROM subscriptions WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    if not subs: await update.message.reply_text("No active subscriptions."); return
    text = "<b>Your Subscriptions:</b>\n" + "\n".join([f"• <code>{s[0]}</code>" for s in subs])
    await update.message.reply_text(text, parse_mode='HTML')

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args: return
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM subscriptions WHERE user_id = ? AND identity = ?", (user_id, context.args[0]))
    conn.commit()
    conn.close()
    await update.message.reply_text("❌ Unsubscribed.", parse_mode='HTML')

# --- BACKGROUND JOB ---
async def check_data_job(context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data: return
    validators = {v['identity']: v for v in data.get('validators', [])}
    
    curr_epoch = 0
    if validators:
        sample = next(iter(validators.values()))
        if sample.get('epochCreditsFull'):
            curr_epoch = sample['epochCreditsFull'][-1][0]

    # Handle Public Whale Alerts
    prev_stakes = json.loads(get_net_state("stake_map", "{}"))
    curr_stakes = {idn: v.get('activatedStake', 0) for idn, v in validators.items()}
    for idn, stake in curr_stakes.items():
        if idn in prev_stakes:
            diff = (stake - prev_stakes[idn]) / LAMPORTS
            if abs(diff) >= WHALE_THRESHOLD:
                name = validators[idn].get('name') or f"<code>{idn[:8]}</code>"
                alert = f"{'🐋' if diff > 0 else '📉'} <b>WHALE MOVE:</b> {abs(diff):,.0f} XNT {'delegated to' if diff > 0 else 'withdrawn from'} <b>{name}</b>"
                await context.bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=alert + FOOTER, parse_mode='HTML')
    set_net_state("stake_map", json.dumps(curr_stakes))

    # Handle Private Alerts
    conn = sqlite3.connect(DB_FILE)
    subscriptions = conn.execute("SELECT user_id, identity, last_state, skip_limit FROM subscriptions").fetchall()
    for user_id, identity, last_state_json, skip_limit in subscriptions:
        if identity not in validators: continue
        curr = validators[identity]
        prev = json.loads(last_state_json)
        name = curr.get('name') or f"<code>{identity[:8]}</code>"
        curr_skips = curr.get('skipped_slots_1_epochs', 0)
        pings = []
        
        if prev.get('status') and curr['status'] != prev['status']:
            pings.append(f"🚦 <b>Status:</b> {curr['status']}")
        
        last_notified_skip = prev.get('notified_skip', 0)
        last_seen_epoch = prev.get('epoch', 0)
        if curr_epoch > last_seen_epoch: last_notified_skip = 0

        if curr_skips >= skip_limit and curr_skips > last_notified_skip:
            pings.append(f"⚠️ <b>High Skip Alert:</b> {curr_skips} blocks skipped")
            last_notified_skip = curr_skips

        if pings:
            try: await context.bot.send_message(chat_id=user_id, text=f"🛰 <b>Alert: {name}</b>\n" + "\n".join(pings) + FOOTER, parse_mode='HTML')
            except: pass
        
        new_state = json.dumps({"status": curr['status'], "comm": curr['commission'], "notified_skip": last_notified_skip, "epoch": curr_epoch})
        conn.execute("UPDATE subscriptions SET last_state = ? WHERE user_id = ? AND identity = ?", (new_state, user_id, identity))
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("calc", calc_cmd))
    app.add_handler(CommandHandler("set_limit", set_limit))
    app.add_handler(CommandHandler("all_nodes_rewards", all_nodes_rewards_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("list", list_subs))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.job_queue.run_repeating(check_data_job, interval=180, first=10)
    app.run_polling()

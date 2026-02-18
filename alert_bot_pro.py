import json
import os
import sqlite3
import logging
import time
import asyncio
import requests
from datetime import datetime
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler

# --- CONFIGURATION ---
# Replace with your actual Bot Token from @BotFather
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
# Replace with your actual Channel ID (starts with -100...)
PUBLIC_CHANNEL_ID = "-1002361138833" 

# File Paths (Use absolute paths)
DATA_FILE = "/var/www/app.x1galaxy.io/all_validator_data.json"
DB_FILE = "/root/xenobi_website/bot_users.db"

# Settings & Thresholds
WHALE_THRESHOLD = 50000   # Trigger public alert if stake changes by > 50k XNT
LAMPORTS = 1_000_000_000
CACHE_TTL = 30            # Seconds to keep data in memory for commands
FOOTER = "\n\n📊 <i>More data at <a href='https://x1galaxy.io'>x1galaxy.io</a></i>"

# Global Cache Object
_data_cache = {"timestamp": 0, "data": None}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)

# --- DATABASE LOGIC ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Subscriptions table
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions 
                 (user_id TEXT, identity TEXT, last_state TEXT, skip_limit INTEGER DEFAULT 1, 
                 UNIQUE(user_id, identity))''')
    # Network state (Epochs, etc)
    c.execute('''CREATE TABLE IF NOT EXISTS network_state (key TEXT PRIMARY KEY, value TEXT)''')
    # Whale tracking (Identity -> Last Stake Alerted)
    c.execute('''CREATE TABLE IF NOT EXISTS whale_history (identity TEXT PRIMARY KEY, last_alerted_stake REAL)''')
    # Search Metrics
    c.execute('''CREATE TABLE IF NOT EXISTS metrics (identity TEXT PRIMARY KEY, hits INTEGER DEFAULT 0)''')
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

def track_metric(identity):
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.execute("INSERT INTO metrics (identity, hits) VALUES (?, 1) ON CONFLICT(identity) DO UPDATE SET hits = hits + 1", (identity,))
        conn.commit(); conn.close()
    except Exception as e:
        logging.error(f"Metrics Error: {e}")

# --- DATA HELPERS ---
def load_data(use_cache=True):
    global _data_cache
    curr_time = time.time()
    if use_cache and _data_cache["data"] and (curr_time - _data_cache["timestamp"] < CACHE_TTL):
        return _data_cache["data"]
    if not os.path.exists(DATA_FILE): return {}
    try:
        with open(DATA_FILE, 'r') as f:
            new_data = json.load(f)
            _data_cache["data"], _data_cache["timestamp"] = new_data, curr_time
            return new_data
    except Exception as e:
        logging.error(f"JSON Load Error: {e}")
        return _data_cache["data"] or {}

def find_validator_smart(query, validators):
    query = query.strip().lower()
    if not query: return None, []
    for v in validators:
        if query == v.get('identity', '').lower(): return v, []
    for v in validators:
        if query == (v.get('name') or "").lower(): return v, []
    suggestions = [v for v in validators if query in (v.get('name') or "").lower()]
    if len(suggestions) == 1: return suggestions[0], []
    return None, suggestions

def format_xnt(lamports):
    return f"{int(lamports / LAMPORTS):,}" if lamports else "0"

# --- BOT COMMANDS ---
async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Help & Instructions"),
        BotCommand("stats", "Snapshot: /stats <name/id>"),
        BotCommand("trending", "Top 10 popular nodes"),
        BotCommand("calc", "ROI: /calc <amount> <name>"),
        BotCommand("all_nodes_rewards", "Rewards table (DM Only)"),
        BotCommand("subscribe", "Get Private DM Alerts"),
        BotCommand("set_limit", "Alert Limit: /set_limit <num>"),
        BotCommand("list", "Your Subscriptions"),
        BotCommand("unsubscribe", "Stop Alerts")
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🛰 <b>X1Galaxy Bot: Network Intelligence</b>\n\n"
        "<b>Commands:</b>\n"
        "• /stats <code>[name/id]</code> - Live card\n"
        "• /trending - Popular validators\n"
        "• /all_nodes_rewards - Last epoch list (DM)\n"
        "• /subscribe - Get private pings\n\n"
        "<i>Search is smart: partial names will show matching buttons.</i>" + FOOTER
    )
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text("❓ Usage: /stats <code>[name or identity]</code>", parse_mode='HTML')
        return
    
    data = load_data()
    validators = data.get('validators', [])
    best_match, suggestions = find_validator_smart(query, validators)

    if not best_match and suggestions:
        buttons = [[InlineKeyboardButton(f"📊 {s.get('name') or s['identity'][:8]}", callback_data=f"stats:{s.get('name') or s['identity']}")] for s in suggestions[:8]]
        await update.message.reply_text(f"🔍 Multiple matches for '<b>{query}</b>':", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
        return
    if not best_match:
        await update.message.reply_text("❌ Validator not found."); return

    track_metric(best_match['identity'])
    sorted_vals = sorted(validators, key=lambda x: x.get('activatedStake', 0), reverse=True)
    rank = next((i for i, val in enumerate(sorted_vals) if val['identity'] == best_match['identity']), 0) + 1
    
    balance = best_match.get('voteBalanceLamports', 0) / LAMPORTS
    credits = int(best_match.get('avg_credits_last_1_epochs', 0))
    assigned = int(best_match.get('assigned_slots_1_epochs', 0))
    skips = int(best_match.get('skipped_slots_1_epochs', 0))

    text = (
        f"🛰 <b>Validator Snapshot</b>\n"
        f"<b>Name:</b> {best_match.get('name', 'Unnamed Node')}\n"
        f"<b>ID:</b> <code>{best_match['identity']}</code>\n"
        f"----------------------------------\n"
        f"🏆 <b>Rank:</b> #{rank} / {len(sorted_vals)}\n"
        f"🚦 <b>Status:</b> {'🟢 Active' if best_match['status'] == 'Active' else '🔴 Delinquent'}\n"
        f"💰 <b>Stake:</b> {format_xnt(best_match.get('activatedStake', 0))} XNT\n"
        f"🏦 <b>Vote Balance:</b> {balance:,.2f} XNT\n"
        f"⚖️ <b>Comm:</b> {best_match.get('commission', '?')}%\n"
        f"----------------------------------\n"
        f"📈 <b>Last Epoch Performance:</b>\n"
        f"• <b>Credits Earned:</b> {credits:,}\n"
        f"• <b>Blocks Assigned:</b> {assigned:,}\n"
        f"• <b>Blocks Skipped:</b> {skips:,}\n"
        f"• <b>Rewards:</b> +{best_match.get('rewards_last_1_epochs_xnt', 0):.2f} XNT"
        + FOOTER
    )
    if update.callback_query: await update.callback_query.message.edit_text(text, parse_mode='HTML', disable_web_page_preview=True)
    else: await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("stats:"):
        context.args = [query.data.split(":", 1)[1]]
        await stats_cmd(update, context)

async def trending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    top = conn.execute("SELECT identity, hits FROM metrics ORDER BY hits DESC LIMIT 10").fetchall()
    conn.close()
    if not top: await update.message.reply_text("No data yet."); return
    data = load_data()
    v_map = {v['identity']: v for v in data.get('validators', [])}
    text = "🔥 <b>Trending Validators (Most Searched)</b>\n\n"
    for i, (idn, hits) in enumerate(top):
        name = v_map.get(idn, {}).get('name') or f"<code>{idn[:8]}</code>"
        text += f"{i+1}. {name} — <b>{hits}</b> lookups\n"
    await update.message.reply_text(text + FOOTER, parse_mode='HTML', disable_web_page_preview=True)

async def all_nodes_rewards_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        await update.message.reply_text("⚠️ Use this command in private DM.", parse_mode='HTML'); return
    data = load_data()
    nodes = sorted([v for v in data.get("validators", []) if v.get("status") == "Active"], key=lambda x: x.get("activatedStake", 0), reverse=True)
    header = "🛰 <b>Last Epoch Rewards</b>\n<code> # |  Rew  | Name           | ID    </code>\n<code>---|-------|----------------|-------</code>\n"
    rows = [f"<code>{str(i+1).ljust(2)} | {str(round(v.get('rewards_last_1_epochs_xnt',0),1)).rjust(5)} | {v.get('name','?').ljust(14)[:14]} | {v['identity'][:4]}..{v['identity'][-2:]}</code>" for i,v in enumerate(nodes)]
    for i in range(0, len(rows), 30):
        await update.message.reply_text((header if i==0 else "") + "\n".join(rows[i:i+30]) + (FOOTER if i+30>=len(rows) else ""), parse_mode='HTML')

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args: await update.message.reply_text("Usage: /subscribe <id>"); return
    identity = context.args[0]
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR IGNORE INTO subscriptions (user_id, identity, last_state) VALUES (?, ?, ?)", (user_id, identity, "{}"))
    conn.commit(); conn.close()
    await update.message.reply_text(f"✅ Alerts active for <code>{identity[:8]}...</code>", parse_mode='HTML')

async def set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not context.args: await update.message.reply_text("Usage: /set_limit <num>"); return
    try:
        limit = int(context.args[0])
        conn = sqlite3.connect(DB_FILE)
        conn.execute("UPDATE subscriptions SET skip_limit = ? WHERE user_id = ?", (limit, user_id))
        conn.commit(); conn.close()
        await update.message.reply_text(f"✅ Threshold set to <b>{limit} blocks</b>.", parse_mode='HTML')
    except: await update.message.reply_text("❌ Invalid number.")

# --- THE ENGINE (Check Job) ---
async def check_data_job(context: ContextTypes.DEFAULT_TYPE):
    data = load_data(use_cache=False)
    if not data: return
    validators = {v['identity']: v for v in data.get('validators', [])}
    
    # 1. Identify Epoch
    curr_epoch = 0
    if validators:
        sample = next(iter(validators.values()))
        if sample.get('epochCreditsFull'): curr_epoch = sample['epochCreditsFull'][-1][0]

    # --- A. EPOCH TRANSITION (Reports) ---
    last_ep = int(get_net_state("last_epoch", 0))
    if curr_epoch > last_ep and last_ep != 0:
        await context.bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=f"🎆 <b>NEW EPOCH: {curr_epoch}</b>\n\nNetwork Active Stake: {format_xnt(data.get('active_stake', 0))} XNT" + FOOTER, parse_mode='HTML')
        
        conn = sqlite3.connect(DB_FILE)
        all_subs = conn.execute("SELECT user_id, identity FROM subscriptions").fetchall()
        for u_id, idn in all_subs:
            if idn in validators:
                v = validators[idn]
                msg = (f"🎇 <b>Epoch {last_ep} Summary</b>\n<b>Validator:</b> {v.get('name') or idn[:8]}\n----------------------------------\n"
                       f"✅ <b>Credits:</b> {int(v.get('avg_credits_last_1_epochs',0)):,}\n"
                       f"📦 <b>Blocks Produced:</b> {int(v.get('assigned_slots_1_epochs',0)) - int(v.get('skipped_slots_1_epochs',0))}/{int(v.get('assigned_slots_1_epochs',0))}\n"
                       f"💰 <b>Rewards:</b> +{v.get('rewards_last_1_epochs_xnt',0):.4f} XNT" + FOOTER)
                try: await context.bot.send_message(chat_id=u_id, text=msg, parse_mode='HTML'); await asyncio.sleep(0.05)
                except: pass
        conn.close()
        set_net_state("last_epoch", curr_epoch)

    # --- B. SPAM-PROOF WHALE ALERTS ---
    conn = sqlite3.connect(DB_FILE)
    for idn, v in validators.items():
        curr_stake = v.get('activatedStake', 0)
        # Fetch the last stake value that we actually ALERTED on
        res = conn.execute("SELECT last_alerted_stake FROM whale_history WHERE identity=?", (idn,)).fetchone()
        last_alerted = res[0] if res else 0

        if last_alerted == 0:
            # First time seeing this validator, just record the stake, don't alert
            conn.execute("INSERT OR REPLACE INTO whale_history (identity, last_alerted_stake) VALUES (?, ?)", (idn, curr_stake))
            continue

        diff = (curr_stake - last_alerted) / LAMPORTS
        if abs(diff) >= WHALE_THRESHOLD:
            name = v.get('name') or f"<code>{idn[:8]}</code>"
            emoji = "🐋" if diff > 0 else "📉"
            verb = "delegated to" if diff > 0 else "withdrawn from"
            alert = f"{emoji} <b>WHALE MOVE:</b> {abs(diff):,.0f} XNT {verb} <b>{name}</b>!"
            await context.bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=alert + FOOTER, parse_mode='HTML')
            # UPDATE the history so we don't alert again for this same value
            conn.execute("INSERT OR REPLACE INTO whale_history (identity, last_alerted_stake) VALUES (?, ?)", (idn, curr_stake))
    conn.commit(); conn.close()

    # --- C. PRIVATE USER PINGS ---
    conn = sqlite3.connect(DB_FILE)
    subscriptions = conn.execute("SELECT user_id, identity, last_state, skip_limit FROM subscriptions").fetchall()
    for user_id, identity, last_state_json, skip_limit in subscriptions:
        if identity not in validators: continue
        curr, prev = validators[identity], json.loads(last_state_json)
        pings = []
        if prev.get('status') and curr['status'] != prev['status']: pings.append(f"🚦 Status: {curr['status']}")
        if prev.get('comm') is not None and curr['commission'] != prev['comm']: pings.append(f"⚖️ Comm: {prev['comm']}% ➡️ {curr['commission']}%")
        
        last_not_skip = prev.get('notified_skip', 0)
        if curr_epoch > prev.get('epoch', 0): last_not_skip = 0
        if curr.get('skipped_slots_1_epochs', 0) >= skip_limit and curr.get('skipped_slots_1_epochs', 0) > last_not_skip:
            pings.append(f"⚠️ High Skips: {curr['skipped_slots_1_epochs']} blocks")
            last_not_skip = curr['skipped_slots_1_epochs']

        if pings:
            try: await context.bot.send_message(chat_id=user_id, text=f"🛰 <b>Alert: {curr.get('name', identity[:8])}</b>\n" + "\n".join(pings) + FOOTER, parse_mode='HTML')
            except: pass
        new_state = json.dumps({"status": curr['status'], "comm": curr['commission'], "notified_skip": last_not_skip, "epoch": curr_epoch})
        conn.execute("UPDATE subscriptions SET last_state = ? WHERE user_id = ? AND identity = ?", (new_state, user_id, identity))
    conn.commit(); conn.close()

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("trending", trending_cmd))
    app.add_handler(CommandHandler("all_nodes_rewards", all_nodes_rewards_cmd))
    app.add_handler(CommandHandler("set_limit", set_limit))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("list", lambda u, c: None)) # Placeholder: add list logic if needed
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.job_queue.run_repeating(check_data_job, interval=180, first=10)
    app.run_polling()

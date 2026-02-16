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

def find_validator_smart(query, validators):
    """
    Returns (best_match, suggestions_list)
    Prioritizes exact identity, then exact name. 
    If none, returns list of close name matches.
    """
    query = query.strip().lower()
    if not query: return None, []

    # 1. Check for Exact Identity
    for v in validators:
        if query == v.get('identity', '').lower():
            return v, []

    # 2. Check for Exact Name Match
    for v in validators:
        name = (v.get('name') or "").lower()
        if query == name:
            return v, []

    # 3. No Exact Match - Find all Partial Matches
    suggestions = []
    for v in validators:
        name = (v.get('name') or "").lower()
        if query in name:
            suggestions.append(v.get('name') or v['identity'])
    
    return None, suggestions

def format_xnt(lamports):
    if lamports is None: return "0"
    return f"{int(lamports / LAMPORTS):,}"

# --- BOT COMMANDS ---
async def post_init(application):
    commands = [
        BotCommand("start", "Help & Instructions"),
        BotCommand("stats", "Snapshot: /stats <name/id>"),
        BotCommand("calc", "ROI: /calc <amount> <name>"),
        BotCommand("set_limit", "Set skip alert limit"),
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
        "• /subscribe <code>[id]</code> - Get private alerts\n\n"
        "<i>Search requires an exact name match. If multiple are found, I will show options.</i>" + FOOTER
    )
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❓ Usage: /stats <code>[exact_name or identity]</code>", parse_mode='HTML')
        return
    
    query = " ".join(context.args)
    data = load_data()
    v_list = data.get('validators', [])
    
    best_match, suggestions = find_validator_smart(query, v_list)

    if not best_match:
        if suggestions:
            # Found close matches, list them
            suggestion_text = "\n".join([f"• <code>{s}</code>" for s in suggestions[:10]])
            await update.message.reply_text(
                f"🔍 I found multiple matches for '<b>{query}</b>'.\n\n"
                f"Please be more specific:\n{suggestion_text}", 
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text("❌ Validator not found. Try an exact name or identity address.")
        return

    # If we got here, we have an exact best_match
    sorted_vals = sorted(v_list, key=lambda x: x.get('activatedStake', 0), reverse=True)
    rank = next((i for i, val in enumerate(sorted_vals) if val['identity'] == best_match['identity']), 0) + 1
    
    balance_formatted = f"{best_match.get('voteBalanceLamports', 0) / LAMPORTS:,.2f} XNT"

    text = (
        f"🛰 <b>Validator Snapshot</b>\n"
        f"<b>Name:</b> {best_match.get('name', 'Unnamed Node')}\n"
        f"<b>ID:</b> <code>{best_match['identity']}</code>\n"
        f"----------------------------------\n"
        f"🏆 <b>Rank:</b> #{rank} / {len(sorted_vals)}\n"
        f"🚦 <b>Status:</b> {'🟢 Active' if best_match['status'] == 'Active' else '🔴 Delinquent'}\n"
        f"💰 <b>Active Stake:</b> {format_xnt(best_match.get('activatedStake', 0))} XNT\n"
        f"🏦 <b>Vote Balance:</b> {balance_formatted}\n"
        f"⚖️ <b>Commission:</b> {best_match.get('commission', '?')}%\n"
        f"📊 <b>Skips (Epoch):</b> {best_match.get('skipped_slots_1_epochs', 0)}\n"
        f"🎁 <b>Last Rewards:</b> +{best_match.get('rewards_last_1_epochs_xnt', 0):.2f} XNT"
        + FOOTER
    )
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def calc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❓ Usage: /calc [amount] [name]", parse_mode='HTML')
        return
    try: amount = float(context.args[0].replace(',', ''))
    except: await update.message.reply_text("❌ Invalid amount."); return
    
    query = " ".join(context.args[1:])
    data = load_data()
    v_list = data.get('validators', [])
    best_match, suggestions = find_validator_smart(query, v_list)
    
    if not best_match:
        await update.message.reply_text("❌ Validator not found. Please use the exact name.")
        return

    comm = best_match.get('commission', 10)
    est_apr = 0.07 * (1 - (comm/100))
    epoch_yield = (amount * est_apr) / 182 
    text = (f"💰 <b>ROI Estimate: {best_match.get('name', 'Node')}</b>\n"
            f"Principle: {amount:,.0f} XNT\n"
            f"----------------------------------\n"
            f"💎 <b>Per Epoch:</b> ~{epoch_yield:.4f} XNT\n"
            f"📈 <b>Net APY:</b> {(est_apr * 100):.2f}%" + FOOTER)
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

# (Remainder of handlers: set_limit, all_nodes_rewards, top, subscribe, list, unsubscribe, check_data_job remain the same as previous)

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("calc", calc_cmd))
    # ... Register other handlers as before ...
    app.job_queue.run_repeating(check_data_job, interval=180, first=10)
    app.run_polling()

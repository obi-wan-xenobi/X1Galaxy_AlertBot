import json
import os
import sqlite3
import logging
import math
from datetime import datetime
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- CONFIG ---
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
PUBLIC_CHANNEL_ID = "-1002361138833" # t.me/X1Galaxy_Alerts
DATA_FILE = "/var/www/app.x1galaxy.io/all_validator_data.json"
TPS_FILE = "/var/www/app.x1galaxy.io/epoch_tps_stats.json"
DB_FILE = "/root/xenobi_website/bot_users.db"

# Thresholds
WHALE_THRESHOLD = 50000 # XNT change to trigger whale alert
LAMPORTS = 1_000_000_000
FOOTER = "\n\n📊 <i>More data at <a href='https://x1galaxy.io'>x1galaxy.io</a></i>"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- DATABASE LOGIC ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (user_id TEXT, identity TEXT, last_state TEXT)''')
    # Table to track network-wide state (Epoch and Stakes)
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
    with open(DATA_FILE, 'r') as f: return json.load(f)

def find_validator(query, validators):
    """Finds a validator by name (partial) or identity (exact)."""
    query = query.lower()
    for v in validators:
        if query == v['identity'].lower(): return v
        if query in (v.get('name') or "").lower(): return v
    return None

def format_xnt(lamports):
    return f"{int(lamports / LAMPORTS):,}"

# --- COMMANDS ---
async def post_init(application):
    commands = [
        BotCommand("start", "Help & Instructions"),
        BotCommand("stats", "/stats <name> - Validator Snapshot"),
        BotCommand("calc", "/calc <amount> <name> - ROI Estimate"),
        BotCommand("top", "Current Leaderboard"),
        BotCommand("subscribe", "/subscribe <identity> - Private Alerts"),
        BotCommand("list", "Your subscriptions"),
        BotCommand("unsubscribe", "/unsubscribe <identity>")
    ]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🛰 <b>X1Galaxy Bot: Network Intelligence</b>\n\n"
        "<b>Commands:</b>\n"
        "• /stats <code>[name]</code> - Live performance card\n"
        "• /calc <code>[qty] [name]</code> - Estimated ROI\n"
        "• /top - Network Top 10\n"
        "• /subscribe <code>[id]</code> - Get private pings\n\n"
        "<i>I also post Whale Alerts and Epoch Reports to the public channel.</i>" + FOOTER
    )
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /stats <name or identity>")
        return
    
    data = load_data()
    v = find_validator(" ".join(context.args), data.get('validators', []))
    
    if not v:
        await update.message.reply_text("❌ Validator not found.")
        return

    # Calculate Rank
    sorted_vals = sorted(data['validators'], key=lambda x: x.get('activatedStake', 0), reverse=True)
    rank = next((i for i, val in enumerate(sorted_vals) if val['identity'] == v['identity']), 0) + 1

    text = (
        f"🛰 <b>{v.get('name', 'Unnamed Validator')}</b>\n"
        f"<code>{v['identity']}</code>\n\n"
        f"<b>Rank:</b> #{rank} / {len(sorted_vals)}\n"
        f"<b>Status:</b> {'🟢 Active' if v['status'] == 'Active' else '🔴 Delinquent'}\n"
        f"<b>Stake:</b> {format_xnt(v.get('activatedStake', 0))} XNT\n"
        f"<b>Commission:</b> {v.get('commission', '?')}%\n"
        f"<b>Skip Rate (1ep):</b> {v.get('skipRate1', 0):.2f}%\n"
        f"<b>Last Epoch Rewards:</b> +{v.get('rewards_last_1_epochs_xnt', 0):.2f} XNT"
        + FOOTER
    )
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def calc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /calc <amount> <validator_name>")
        return
    
    try:
        amount = float(context.args[0])
    except:
        await update.message.reply_text("Invalid amount.")
        return

    data = load_data()
    v = find_validator(" ".join(context.args[1:]), data.get('validators', []))
    if not v:
        await update.message.reply_text("Validator not found.")
        return

    # APY Calculation logic (simplified)
    # Using global_avg_credits_last_5_epochs as a proxy for network inflation
    avg_credits = data.get('global_avg_credits_last_5_epochs', 400000)
    comm = v.get('commission', 10)
    
    # Estimate: (Credits / Total Credits) is hard; we use a standard X1 yield estimate roughly
    # On most Solana-based chains, yield is ~5-8%. Let's assume 7% base.
    est_yield = 0.07 * (1 - (comm/100))
    epoch_yield = (amount * est_yield) / 182 # approx 182 epochs per year
    
    text = (
        f"💰 <b>Staking ROI Estimate: {v.get('name', 'Node')}</b>\n"
        f"Amount: {amount:,} XNT\n\n"
        f"<b>Est. per Epoch:</b> {epoch_yield:.4f} XNT\n"
        f"<b>Est. Annual:</b> {(amount * est_yield):.2f} XNT\n"
        f"<b>Effective APY:</b> {(est_yield * 100):.2f}%\n\n"
        f"<i>Note: Estimates based on current network performance.</i>" + FOOTER
    )
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    sorted_vals = sorted(data.get('validators', []), key=lambda x: x.get('activatedStake', 0), reverse=True)[:10]
    
    text = "🏆 <b>X1 Stake Leaderboard</b>\n\n"
    for i, v in enumerate(sorted_vals):
        name = v.get('name') or v['identity'][:8]
        text += f"{i+1}. <b>{name}</b> - {format_xnt(v.get('activatedStake', 0))} XNT\n"
    
    text += FOOTER
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

# --- THE ENGINE (Check Job) ---
async def check_data_job(context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data: return
    
    validators = {v['identity']: v for v in data.get('validators', [])}
    
    # --- 1. WHALE WATCHER ---
    # Load previous stake map
    prev_stakes_raw = get_net_state("stake_map", "{}")
    prev_stakes = json.loads(prev_stakes_raw)
    current_stakes = {idn: v.get('activatedStake', 0) for idn, v in validators.items()}
    
    for idn, stake in current_stakes.items():
        if idn in prev_stakes:
            diff = (stake - prev_stakes[idn]) / LAMPORTS
            if abs(diff) >= WHALE_THRESHOLD:
                name = validators[idn].get('name') or f"<code>{idn[:8]}</code>"
                emoji = "🐋" if diff > 0 else "📉"
                verb = "delegated to" if diff > 0 else "withdrawn from"
                alert = f"{emoji} <b>WHALE MOVE</b>\n\n{abs(diff):,.0f} XNT was {verb} <b>{name}</b>!"
                await context.bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=alert + FOOTER, parse_mode='HTML')
    
    set_net_state("stake_map", json.dumps(current_stakes))

    # --- 2. EPOCH REPORT ---
    current_epoch = 0
    if validators:
        # Get epoch from the first active validator's credits
        sample_v = next(iter(validators.values()))
        if sample_v.get('epochCreditsFull'):
            current_epoch = sample_v['epochCreditsFull'][-1][0]

    last_epoch = int(get_net_state("last_epoch", 0))
    if current_epoch > last_epoch and last_epoch != 0:
        # New Epoch detected!
        tps_data = {}
        if os.path.exists(TPS_FILE):
            with open(TPS_FILE, 'r') as f: tps_data = json.load(f)
        
        peak_tps = tps_data.get('all_time_max_tps', {}).get('value', 0)
        
        report = (
            f"🎆 <b>NEW EPOCH: {current_epoch}</b>\n\n"
            f"The network has successfully transitioned to a new epoch.\n\n"
            f"<b>Network Active Stake:</b> {format_xnt(data.get('active_stake', 0))} XNT\n"
            f"<b>Max Historic TPS:</b> {peak_tps:.1f}\n"
            f"<b>Total Validators:</b> {len(validators)}"
            + FOOTER
        )
        await context.bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=report, parse_mode='HTML')
        set_net_state("last_epoch", current_epoch)

    # --- 3. PRIVATE SUBSCRIPTION ALERTS ---
    # (Existing logic but with Friendly Name integration)
    conn = sqlite3.connect(DB_FILE)
    subscriptions = conn.execute("SELECT user_id, identity, last_state FROM subscriptions").fetchall()
    for user_id, identity, last_state_json in subscriptions:
        if identity not in validators: continue
        curr = validators[identity]
        prev = json.loads(last_state_json)
        name = curr.get('name') or f"<code>{identity[:8]}</code>"
        pings = []
        
        if prev.get('status') and curr['status'] != prev['status']:
            pings.append(f"Status changed to <b>{curr['status']}</b>")
        if prev.get('comm') is not None and curr['commission'] != prev['comm']:
            pings.append(f"Commission: {prev['comm']}% ➡️ {curr['commission']}%")
        
        if pings:
            alert = f"🛰 <b>Alert: {name}</b>\n" + "\n".join(pings) + FOOTER
            try: await context.bot.send_message(chat_id=user_id, text=alert, parse_mode='HTML')
            except: pass
            
        new_state = json.dumps({"status": curr['status'], "comm": curr['commission']})
        conn.execute("UPDATE subscriptions SET last_state = ? WHERE user_id = ? AND identity = ?", (new_state, user_id, identity))
    conn.commit()
    conn.close()

# --- MAIN ---
async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Usage: /subscribe <identity>")
        return
    identity = context.args[0]
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT INTO subscriptions (user_id, identity, last_state) VALUES (?, ?, ?)", (user_id, identity, "{}"))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Alert subscription active for <code>{identity}</code>", parse_mode='HTML')

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("calc", calc_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe))
    
    app.job_queue.run_repeating(check_data_job, interval=180, first=10)
    app.run_polling()

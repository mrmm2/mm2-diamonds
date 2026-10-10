# ============================================================
# MM2 — сервер для алмазов
# FastAPI + SQLite
# ============================================================

from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import sqlite3
import os
import hmac
import hashlib

# ============================================================
# НАСТРОЙКИ
# ============================================================
SECRET_KEY = os.getenv("CLOUDTIPS_SECRET", "TEST_MODE_NO_SECRET")
TEST_MODE = os.getenv("TEST_MODE", "1") == "1"
DB_PATH = os.getenv("DB_PATH", "mm2.db")

PRICE_TO_DIAMONDS = {
    50: 100,
    100: 250,
    200: 600,
}

app = FastAPI(title="MM2 Diamonds API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# БАЗА ДАННЫХ
# ============================================================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                nick TEXT PRIMARY KEY,
                diamonds INTEGER DEFAULT 0,
                total_paid INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nick TEXT,
                amount INTEGER,
                diamonds INTEGER,
                transaction_id TEXT,
                comment TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

init_db()

# ============================================================
# МОДЕЛИ
# ============================================================
class SpendRequest(BaseModel):
    nick: str
    amount: int

# ============================================================
# API ДЛЯ ИГРЫ
# ============================================================

@app.get("/")
def root():
    return {"status": "ok", "service": "MM2 Diamonds API", "test_mode": TEST_MODE}

@app.get("/api/balance")
def get_balance(nick: str):
    with db() as conn:
        row = conn.execute(
            "SELECT diamonds, total_paid FROM players WHERE nick = ?",
            (nick,)
        ).fetchone()
    if not row:
        return {"nick": nick, "diamonds": 0, "total_paid": 0}
    return {"nick": nick, "diamonds": row["diamonds"], "total_paid": row["total_paid"]}

@app.post("/api/register")
def register(nick: str = Form(...)):
    nick = nick.strip()
    if not nick or len(nick) < 2 or len(nick) > 20:
        raise HTTPException(400, "Ник от 2 до 20 символов")
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO players (nick) VALUES (?)", (nick,))
        conn.commit()
        row = conn.execute("SELECT diamonds FROM players WHERE nick = ?", (nick,)).fetchone()
    return {"nick": nick, "diamonds": row["diamonds"]}

@app.post("/api/spend")
def spend(req: SpendRequest):
    with db() as conn:
        row = conn.execute("SELECT diamonds FROM players WHERE nick = ?", (req.nick,)).fetchone()
        if not row:
            raise HTTPException(404, "Игрок не найден")
        if row["diamonds"] < req.amount:
            raise HTTPException(400, "Недостаточно алмазов")
        conn.execute(
            "UPDATE players SET diamonds = diamonds - ? WHERE nick = ?",
            (req.amount, req.nick)
        )
        conn.commit()
        new_diamonds = row["diamonds"] - req.amount
    return {"nick": req.nick, "diamonds": new_diamonds, "spent": req.amount}

# ============================================================
# WEBHOOK ОТ CLOUDTIPS
# ============================================================

@app.post("/webhook")
async def cloudtips_webhook(request: Request):
    body = await request.body()
    form = await request.form()

    if not TEST_MODE:
        signature = request.headers.get("X-Content-HMAC", "")
        expected = hmac.new(
            SECRET_KEY.encode(),
            body,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            print("❌ Неверная подпись webhook!")
            return JSONResponse({"status": "error", "reason": "invalid signature"}, status_code=403)

    success = form.get("success") == "True" or form.get("success") == "true"
    amount = float(form.get("amount", 0))
    comment = (form.get("comment") or "").strip()
    transaction_id = form.get("transactionid") or form.get("transaction_id") or ""

    print(f"💰 Webhook: success={success}, amount={amount}, comment='{comment}', tx={transaction_id}")

    if not success:
        return {"status": "ignored", "reason": "not success"}

    nick = comment
    if not nick:
        print("⚠️ Комментарий пуст — ник не указан")
        return {"status": "ignored", "reason": "no nick in comment"}

    amount_int = int(amount)
    diamonds = PRICE_TO_DIAMONDS.get(amount_int)
    if not diamonds:
        closest = min(PRICE_TO_DIAMONDS.keys(), key=lambda x: abs(x - amount_int))
        if abs(closest - amount_int) <= 5:
            diamonds = PRICE_TO_DIAMONDS[closest]
            amount_int = closest
        else:
            print(f"⚠️ Неизвестная сумма: {amount_int}")
            return {"status": "ignored", "reason": "unknown amount"}

    with db() as conn:
        if transaction_id:
            exists = conn.execute(
                "SELECT 1 FROM payments WHERE transaction_id = ?",
                (transaction_id,)
            ).fetchone()
            if exists:
                print(f"⚠️ Транзакция {transaction_id} уже обработана")
                return {"status": "ignored", "reason": "duplicate"}

        conn.execute("INSERT OR IGNORE INTO players (nick) VALUES (?)", (nick,))
        conn.execute(
            "UPDATE players SET diamonds = diamonds + ?, total_paid = total_paid + ? WHERE nick = ?",
            (diamonds, amount_int, nick)
        )
        conn.execute(
            "INSERT INTO payments (nick, amount, diamonds, transaction_id, comment) VALUES (?, ?, ?, ?, ?)",
            (nick, amount_int, diamonds, transaction_id, comment)
        )
        conn.commit()
        row = conn.execute("SELECT diamonds FROM players WHERE nick = ?", (nick,)).fetchone()

    print(f"✅ Начислено {diamonds} алмазов игроку {nick}. Теперь у него {row['diamonds']}.")
    return {"status": "ok", "nick": nick, "added": diamonds, "total": row["diamonds"]}

# ============================================================
# АДМИНКА
# ============================================================

@app.post("/admin/give")
def admin_give(nick: str = Form(...), amount: int = Form(...), admin_key: str = Form(...)):
    if admin_key != "tra45()/":
        raise HTTPException(403, "Нет доступа")
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO players (nick) VALUES (?)", (nick,))
        conn.execute("UPDATE players SET diamonds = diamonds + ? WHERE nick = ?", (amount, nick))
        conn.commit()
        row = conn.execute("SELECT diamonds FROM players WHERE nick = ?", (nick,)).fetchone()
    return {"nick": nick, "diamonds": row["diamonds"], "given": amount}
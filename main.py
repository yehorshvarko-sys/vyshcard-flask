import os
import random
from datetime import datetime

from flask import Flask, jsonify, request, session, render_template
from flask_cors import CORS
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, scoped_session
from passlib.context import CryptContext

# ==========================
# НАЛАШТУВАННЯ
# ==========================

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "CHANGE_ME_SUPER_SECRET")
CORS(app, supports_credentials=True)

CURRENCY = "V$"
START_BALANCE = 20.0

COMMISSION_SILVER = 0.02
COMMISSION_GOLD = 0.015
COMMISSION_PLATINUM = 0.01

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///vyshcard.db")

# ==========================
# БАЗА ДАНИХ
# ==========================

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

Base = declarative_base()
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    balance = Column(Float, default=START_BALANCE)
    points = Column(Integer, default=0)
    level = Column(String, default="Silver")
    avatar_url = Column(String, nullable=True)
    bio = Column(String, nullable=True)

    cards = relationship("Card", back_populates="owner")
    txns = relationship("Transaction", back_populates="user")


class Card(Base):
    __tablename__ = "cards"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    number = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="cards")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    amount = Column(Float)
    commission = Column(Float)
    direction = Column(String)  # incoming / outgoing
    other_user = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="txns")


class Partner(Base):
    __tablename__ = "partners"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    discount = Column(String)
    category = Column(String)
    note = Column(String)
    url = Column(String, nullable=True)
    logo_url = Column(String, nullable=True)


Base.metadata.create_all(bind=engine)

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==========================
# УТИЛІТИ
# ==========================

def generate_card_number() -> str:
    return f"VY-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}"


def commission_for_level(level: str) -> float:
    if level == "Gold":
        return COMMISSION_GOLD
    if level == "Platinum":
        return COMMISSION_PLATINUM
    return COMMISSION_SILVER


def recalc_level(user: User):
    pts = user.points
    if pts >= 1000:
        user.level = "Platinum"
    elif pts >= 200:
        user.level = "Gold"
    else:
        user.level = "Silver"


def seed_partners(db_session):
    if db_session.query(Partner).count() > 0:
        return
    demo = [
        Partner(
            name="Кавʼярня «Набережна»",
            discount="-10% на каву та десерти",
            category="кава",
            note="Знижка за QR-карткою, з 8:00 до 20:00.",
        ),
        Partner(
            name="Аптека «Здоровий Вишгород»",
            discount="-7% на товари",
            category="аптека",
            note="Не діє на акційні позиції.",
        ),
        Partner(
            name="Спортклуб «ВишГорАтлет»",
            discount="-15% на абонементи",
            category="спорт",
            note="Для власників картки вишгородчанина.",
        ),
    ]
    db_session.add_all(demo)
    db_session.commit()


@app.before_first_request
def init_data():
    db = SessionLocal()
    seed_partners(db)
    db.close()


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    db.close()
    return user


# ==========================
# ВИДАЧА HTML
# ==========================

@app.route("/")
def index():
    return render_template("index.html")


# ==========================
# API: АВТОРИЗАЦІЯ
# ==========================

@app.post("/api/register")
def api_register():
    username = request.form.get("username") or request.json.get("username")
    password = request.form.get("password") or request.json.get("password")
    if not username or not password:
        return jsonify({"detail": "Вкажи логін і пароль"}), 400

    db = SessionLocal()
    if db.query(User).filter(User.username == username).first():
        db.close()
        return jsonify({"detail": "Такий логін вже існує"}), 400

    user = User(
        username=username,
        password_hash=pwd_ctx.hash(password),
        balance=START_BALANCE,
        points=0,
        level="Silver",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    card = Card(user_id=user.id, number=generate_card_number())
    db.add(card)
    db.commit()

    session["user_id"] = user.id
    db.close()
    return jsonify({"status": "ok", "card": card.number})


@app.post("/api/login")
def api_login():
    username = request.form.get("username") or request.json.get("username")
    password = request.form.get("password") or request.json.get("password")
    if not username or not password:
        return jsonify({"detail": "Вкажи логін і пароль"}), 400

    db = SessionLocal()
    user = db.query(User).filter(User.username == username).first()
    if not user or not pwd_ctx.verify(password, user.password_hash):
        db.close()
        return jsonify({"detail": "Невірний логін або пароль"}), 400

    session["user_id"] = user.id
    db.close()
    return jsonify({"status": "ok"})


@app.post("/api/logout")
def api_logout():
    session.pop("user_id", None)
    return jsonify({"status": "ok"})


# ==========================
# API: ПРОФІЛЬ / КАРТКА
# ==========================

@app.get("/api/me")
def api_me():
    user = get_current_user()
    if not user:
        return jsonify({"detail": "Не авторизовано"}), 401

    db = SessionLocal()
    user = db.query(User).filter(User.id == user.id).first()
    data = {
        "username": user.username,
        "balance": user.balance,
        "points": user.points,
        "level": user.level,
        "avatar_url": user.avatar_url,
        "bio": user.bio,
        "cards": [c.number for c in user.cards],
        "currency": CURRENCY,
    }
    db.close()
    return jsonify(data)


@app.post("/api/profile")
def api_profile():
    user = get_current_user()
    if not user:
        return jsonify({"detail": "Не авторизовано"}), 401

    data = request.json or {}
    avatar_url = data.get("avatar_url")
    bio = data.get("bio")

    db = SessionLocal()
    u = db.query(User).filter(User.id == user.id).first()
    if avatar_url is not None:
        u.avatar_url = avatar_url
    if bio is not None:
        u.bio = bio
    db.commit()
    db.close()
    return jsonify({"status": "ok"})


# ==========================
# API: ПАРТНЕРИ
# ==========================

@app.get("/api/partners")
def api_partners():
    db = SessionLocal()
    partners = db.query(Partner).all()
    out = []
    for p in partners:
        out.append({
            "id": p.id,
            "name": p.name,
            "discount": p.discount,
            "category": p.category,
            "note": p.note,
            "url": p.url,
            "logo_url": p.logo_url,
        })
    db.close()
    return jsonify(out)


# ==========================
# API: ПЕРЕКАЗИ
# ==========================

@app.post("/api/transfer")
def api_transfer():
    user = get_current_user()
    if not user:
        return jsonify({"detail": "Не авторизовано"}), 401

    data = request.json or {}
    to_username = data.get("to_username")
    amount = data.get("amount")

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"detail": "Некоректна сума"}), 400

    if amount <= 0:
        return jsonify({"detail": "Сума має бути більшою за 0"}), 400

    db = SessionLocal()
    sender = db.query(User).filter(User.id == user.id).first()
    receiver = db.query(User).filter(User.username == to_username).first()

    if not receiver:
        db.close()
        return jsonify({"detail": "Отримувача не знайдено"}), 404

    rate = commission_for_level(sender.level)
    commission = round(amount * rate, 2)
    total = amount + commission

    if sender.balance < total:
        db.close()
        return jsonify({"detail": f"Недостатньо коштів (потрібно {total} {CURRENCY})"}), 400

    sender.balance -= total
    receiver.balance += amount

    earned_points = int(amount // 50)
    sender.points += earned_points
    recalc_level(sender)

    out_tx = Transaction(
        user_id=sender.id,
        amount=amount,
        commission=commission,
        direction="outgoing",
        other_user=receiver.username,
    )
    in_tx = Transaction(
        user_id=receiver.id,
        amount=amount,
        commission=0.0,
        direction="incoming",
        other_user=sender.username,
    )

    db.add(out_tx)
    db.add(in_tx)
    db.commit()

    result = {
        "status": "ok",
        "sent": amount,
        "commission": commission,
        "total_spent": total,
        "currency": CURRENCY,
        "new_balance": sender.balance,
        "earned_points": earned_points,
        "new_level": sender.level,
    }
    db.close()
    return jsonify(result)


# ==========================
# API: ІСТОРІЯ
# ==========================

@app.get("/api/history")
def api_history():
    user = get_current_user()
    if not user:
        return jsonify({"detail": "Не авторизовано"}), 401

    db = SessionLocal()
    u = db.query(User).filter(User.id == user.id).first()
    txns = sorted(u.txns, key=lambda t: t.timestamp, reverse=True)
    res = []
    for t in txns:
        res.append({
            "amount": t.amount,
            "commission": t.commission,
            "direction": t.direction,
            "other_user": t.other_user,
            "timestamp": t.timestamp.isoformat(),
        })
    db.close()
    return jsonify(res)


# ==========================
# API: КАРТКА (QR)
# ==========================

@app.get("/api/card/<number>")
def api_card(number):
    db = SessionLocal()
    card = db.query(Card).filter(Card.number == number).first()
    if not card:
        db.close()
        return jsonify({"detail": "Картку не знайдено"}), 404
    owner = card.owner
    data = {
        "username": owner.username,
        "level": owner.level,
        "avatar_url": owner.avatar_url,
        "bio": owner.bio,
        "card": card.number,
    }
    db.close()
    return jsonify(data)


if __name__ == "__main__":
    app.run(debug=True)

from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
import re
import unicodedata
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__)
app.secret_key = "CHANGE_THIS_SECRET_KEY"
DATABASE = "coffee_shop.db"

def normalize_secret_answer(answer: str) -> str:
    answer = unicodedata.normalize("NFKC", answer)
    answer = answer.casefold()
    answer = answer.replace("ё", "е")

    answer = re.sub(r"[^а-яa-z0-9]", "", answer)

    return answer

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()

    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        secret_question TEXT NOT NULL,
        secret_answer_hash TEXT NOT NULL
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL NOT NULL CHECK(price >= 0),
        category TEXT NOT NULL
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Новый',
        total REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        price REAL NOT NULL,
        FOREIGN KEY(order_id) REFERENCES orders(id),
        FOREIGN KEY(product_id) REFERENCES products(id)
    )""")

    # admin init
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        secret_answer = normalize_secret_answer("кофе")

        conn.execute(
            """INSERT INTO users
               (username, password_hash, secret_question, secret_answer_hash)
               VALUES (?, ?, ?, ?)""",
            (
                "admin",
                generate_password_hash("admin123"),
                "Как называется ваш любимый напиток?",
                generate_password_hash(secret_answer)
            )
        )

    if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO products (name, price, category) VALUES (?, ?, ?)",
            [
                ("Эспрессо", 150, "Кофе"),
                ("Американо", 170, "Кофе"),
                ("Капучино", 220, "Кофе"),
                ("Латте", 240, "Кофе"),
                ("Чай", 150, "Чай"),
                ("Чизкейк", 280, "Десерты"),
                ("Круассан", 190, "Выпечка")
            ]
        )

    conn.commit()
    conn.close()

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username=request.form.get("username","").strip()
        password=request.form.get("password","")
        conn=get_db()
        user=conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"]=user["id"]
            session["username"]=user["username"]
            return redirect(url_for("index"))
        flash("Неверный логин или пароль.","error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/forgot-password", methods=["GET","POST"])
def forgot_password():
    username=request.form.get("username","").strip()
    question=None
    if request.method=="POST":
        conn=get_db()
        user=conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not user:
            conn.close()
            flash("Пользователь не найден.","error")
            return render_template("forgot_password.html", question=None, username=username)
        question=user["secret_question"]
        if request.form.get("action")=="reset":
            answer = normalize_secret_answer(
            request.form.get("answer", ""))
            new_password=request.form.get("new_password","")
            if not check_password_hash(user["secret_answer_hash"], answer):
                flash("Неверный ответ на секретный вопрос.","error")
            elif len(new_password)<6:
                flash("Новый пароль должен содержать минимум 6 символов.","error")
            else:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                             (generate_password_hash(new_password), user["id"]))
                conn.commit(); conn.close()
                flash("Пароль успешно изменён.","success")
                return redirect(url_for("login"))
        conn.close()
    return render_template("forgot_password.html", question=question, username=username)

@app.route("/")
@login_required
def index():
    conn=get_db()
    stats=[
        conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM orders WHERE status!='Готов'").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM orders WHERE date(created_at)=date('now','localtime')").fetchone()[0]]
    conn.close()
    return render_template("index.html", stats=stats)

@app.route("/menu")
@login_required
def menu():
    conn=get_db()
    products=conn.execute("SELECT * FROM products ORDER BY category,name").fetchall()
    conn.close()
    return render_template("menu.html", products=products)

@app.route("/menu/add", methods=["POST"])
@login_required
def add_product():
    name=request.form.get("name","").strip()
    category=request.form.get("category","").strip()
    try: price=float(request.form.get("price","").replace(",","."))
    except ValueError: price=-1
    if not name or not category or price<0:
        flash("Заполните поля корректно.","error")
        return redirect(url_for("menu"))
    conn=get_db()
    conn.execute("INSERT INTO products(name,price,category) VALUES(?,?,?)",(name,price,category))
    conn.commit(); conn.close()
    flash("Товар добавлен.","success")
    return redirect(url_for("menu"))

@app.route("/menu/delete/<int:product_id>", methods=["POST"])
@login_required
def delete_product(product_id):
    conn=get_db()
    used=conn.execute("SELECT COUNT(*) FROM order_items WHERE product_id=?",(product_id,)).fetchone()[0]
    if used:
        flash("Нельзя удалить товар из истории заказов.","error")
    else:
        conn.execute("DELETE FROM products WHERE id=?",(product_id,))
        conn.commit(); flash("Товар удалён.","success")
    conn.close()
    return redirect(url_for("menu"))

@app.route("/orders")
@login_required
def orders():
    conn=get_db()
    orders=conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("orders.html", orders=orders)

@app.route("/orders/new", methods=["GET","POST"])
@login_required
def new_order():
    conn=get_db()
    products=conn.execute("SELECT * FROM products ORDER BY category,name").fetchall()
    if request.method=="POST":
        customer=request.form.get("customer_name","").strip() or "Гость"
        items=[]; total=0
        for p in products:
            try: q=int(request.form.get(f"quantity_{p['id']}","0"))
            except ValueError: q=0
            if q>0:
                total+=p["price"]*q
                items.append((p["id"],q,p["price"]))
        if not items:
            conn.close(); flash("Добавьте товар.","error")
            return render_template("new_order.html", products=products)
        cur=conn.execute("INSERT INTO orders(customer_name,status,total,created_at) VALUES(?, 'Новый', ?, ?)",
                         (customer,total,datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        order_id=cur.lastrowid
        conn.executemany("INSERT INTO order_items(order_id,product_id,quantity,price) VALUES(?,?,?,?)",
                         [(order_id,*x) for x in items])
        conn.commit(); conn.close()
        return redirect(url_for("order_detail",order_id=order_id))
    conn.close()
    return render_template("new_order.html", products=products)

@app.route("/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    conn=get_db()
    order=conn.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not order: conn.close(); return "Заказ не найден",404
    items=conn.execute("""SELECT order_items.*,products.name FROM order_items
                          JOIN products ON products.id=order_items.product_id
                          WHERE order_items.order_id=?""",(order_id,)).fetchall()
    conn.close()
    return render_template("order.html",order=order,items=items)

@app.route("/orders/<int:order_id>/status",methods=["POST"])
@login_required
def change_status(order_id):
    status=request.form.get("status")
    if status not in ["Новый","Готовится","Готов"]:
        flash("Некорректный статус.","error")
        return redirect(url_for("orders"))
    conn=get_db(); conn.execute("UPDATE orders SET status=? WHERE id=?",(status,order_id))
    conn.commit(); conn.close()
    return redirect(request.referrer or url_for("orders"))

@app.route("/orders/<int:order_id>/delete",methods=["POST"])
@login_required
def delete_order(order_id):
    conn=get_db()
    conn.execute("DELETE FROM order_items WHERE order_id=?",(order_id,))
    conn.execute("DELETE FROM orders WHERE id=?",(order_id,))
    conn.commit(); conn.close()
    return redirect(url_for("orders"))

@app.route("/display")
def display():
    conn=get_db()
    orders=conn.execute("""SELECT id,customer_name,status,created_at FROM orders
                           ORDER BY id DESC LIMIT 30""").fetchall()
    conn.close()
    return render_template("display.html",orders=orders)

if __name__=="__main__":
    init_db()
    app.run(debug=False)

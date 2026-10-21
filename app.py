from flask import (
    Flask, render_template, request,
    redirect, url_for, session
)
import sqlite3
import os
from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "local-development-key-change-before-deployment"
)

# Sample products for the homepage
products = [
    {
        "id": 1,
        "name": "Wireless Headphones",
        "category": "Electronics",
        "price": 300,
        "image": "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=600",
        "description": "Enjoy your favourite music."
    },
    {
        "id": 2,
        "name": "Classic Sneakers",
        "category": "Fashion",
        "price": 450,
        "image": "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=600",
        "description": "Everyday style and comfort."
    },
    {
        "id": 3,
        "name": "Smart Watch",
        "category": "Electronics",
        "price": 550,
        "image": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=600",
        "description": "Keep track of your day."
    },
    {
        "id": 4,
        "name": "Travel Backpack",
        "category": "Fashion",
        "price": 250,
        "image": "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=600",
        "description": "Carry your essentials anywhere."
    },
    {
        "id": 5,
        "name": "Camera",
        "category": "Electronics",
        "price": 1200,
        "image": "https://images.unsplash.com/photo-1516035069371-29a1b244cc32?w=600",
        "description": "Capture special moments."
    },
    {
        "id": 6,
        "name": "Sunglasses",
        "category": "Accessories",
        "price": 120,
        "image": "https://images.unsplash.com/photo-1511499767150-a48a237f0083?w=600",
        "description": "A simple finishing touch."
    }
]


@app.route("/")
def home():
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()

    filtered_products = products

    if search:
        filtered_products = [
            product for product in filtered_products
            if search.lower() in product["name"].lower()
            or search.lower() in product["category"].lower()
            or search.lower() in product["description"].lower()
        ]

    if category:
        filtered_products = [
            product for product in filtered_products
            if product["category"].lower() == category.lower()
        ]

    categories = ["Electronics", "Fashion", "Accessories"]

    return render_template(
        "index.html",
        products=filtered_products,
        categories=categories,
        search=search,
        selected_category=category
    )

# Database file stored alongside app.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "cash_store.db")


def get_db_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = get_db_connection()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('buyer', 'seller')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.commit()
    connection.close()


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    success = None

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "").strip().lower()

        if not name or not email or not password:
            error = "Please complete all required fields."

        elif len(password) < 8:
            error = "Your password must be at least 8 characters."

        elif role not in ("buyer", "seller"):
            error = "Please select a valid account type."

        else:
            password_hash = generate_password_hash(password)
            connection = get_db_connection()

            try:
                connection.execute(
                    """
                    INSERT INTO users
                    (name, email, password_hash, role)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name, email, password_hash, role)
                )
                connection.commit()
                success = "Account created successfully!"

            except sqlite3.IntegrityError:
                error = "That email address is already registered."

            finally:
                connection.close()

    return render_template(
        "register.html",
        error=error,
        success=success
    )
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        connection = get_db_connection()
        try:
            user = connection.execute(
                "SELECT * FROM users WHERE email = ?",
                (email,)
            ).fetchone()
        finally:
            connection.close()

        if user and check_password_hash(
            user["password_hash"], password
        ):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_role"] = user["role"]

        if user["role"] == "seller":
             return redirect(url_for("seller_dashboard"))

             return redirect(url_for("home"))

        error = "Invalid email address or password."

    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/seller/dashboard")
def seller_dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "seller":
        return redirect(url_for("home"))

    return render_template("seller_dashboard.html")


# Create the database and users table if they don't exist
init_db()

if __name__ == "__main__":
    app.run(debug=True)
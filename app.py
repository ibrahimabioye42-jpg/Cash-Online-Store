from itertools import product
from multiprocessing import connection

from flask import (
    Flask, render_template, request,
    redirect, url_for, session, abort
)
import sqlite3
import os
from urllib.parse import urlparse
from werkzeug.security import (
    generate_password_hash,
    check_password_hash,
)
import uuid
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "local-development-key-change-before-deployment"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "cash_store.db")
FREE_PRODUCT_LIMIT = 5

UPLOAD_FOLDER = os.path.join(
    BASE_DIR, "static", "uploads"
)

ALLOWED_EXTENSIONS = {
    "jpg", "jpeg", "png", "webp", "gif"
}

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_SIZE

def get_db_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = get_db_connection()

    # Keep existing accounts intact.
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

    # Add products and link each product to its seller.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL CHECK(price >= 0),
            quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity >= 0),
            image TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (seller_id) REFERENCES users(id)
        )
    """)

    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_products_seller
        ON products(seller_id)
    """)
    # Store orders placed by buyers.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1
                CHECK(quantity > 0),
            total_price REAL NOT NULL
                CHECK(total_price >= 0),
            status TEXT NOT NULL DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (buyer_id) REFERENCES users(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    """)
    connection.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        order_id INTEGER,
        message TEXT NOT NULL,
        is_read INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (order_id) REFERENCES orders(id)
    )
""")

    connection.commit()
    connection.close()


def safe_next_url(target):
    """Allow only local paths when redirecting after login."""
    if not target:
        return None

    parsed = urlparse(target)

    if parsed.scheme or parsed.netloc:
        return None

    if not target.startswith("/") or target.startswith("//"):
        return None

    return target


@app.route("/")
def home():
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()

    query = """
        SELECT
            p.*,
            u.name AS seller_name
        FROM products p
        JOIN users u ON u.id = p.seller_id
        WHERE u.role = 'seller'
    """
    parameters = []

    if search:
        query += """
            AND (
                p.name LIKE ?
                OR p.category LIKE ?
                OR p.description LIKE ?
                OR u.name LIKE ?
            )
        """
        term = f"%{search}%"
        parameters.extend([term, term, term, term])

    if category:
        query += " AND p.category = ?"
        parameters.append(category)

    query += " ORDER BY p.created_at DESC, p.id DESC"

    connection = get_db_connection()
    try:
        products = connection.execute(
            query, parameters
        ).fetchall()

        categories = connection.execute("""
            SELECT DISTINCT category
            FROM products
            WHERE quantity > 0
            ORDER BY category
        """).fetchall()
    finally:
        connection.close()

    return render_template(
        "index.html",
        products=products,
        categories=categories,
        search=search,
        selected_category=category
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    success = None
    next_url = safe_next_url(
    request.args.get("next") or request.form.get("next")
)

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
            connection = get_db_connection()
            try:
                connection.execute("""
                    INSERT INTO users
                    (name, email, password_hash, role)
                    VALUES (?, ?, ?, ?)
                """, (
                    name,
                    email,
                    generate_password_hash(password),
                    role
                ))
                connection.commit()
                success = "Account created successfully! You can now log in."
            except sqlite3.IntegrityError:
                error = "That email address is already registered."
            finally:
                connection.close()

    return render_template(
        "register.html",
        error=error,
        success=success,
        next_url=next_url
    )



@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    next_url = safe_next_url(
        request.args.get("next") or request.form.get("next")
    )

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
            # Do not let a seller use buyer-only purchase routes.
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_role"] = user["role"]

            if user["role"] == "seller":
                return redirect(url_for("seller_dashboard"))

            if next_url:
                return redirect(next_url)

            return redirect(url_for("home"))

        error = "Invalid email address or password."

    return render_template(
        "login.html",
        error=error,
        next_url=next_url
    )


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

    connection = get_db_connection()
    try:
        my_products = connection.execute("""
            SELECT *
            FROM products
            WHERE seller_id = ?
            ORDER BY created_at DESC, id DESC
        """, (session["user_id"],)).fetchall()

        count = connection.execute("""
            SELECT COUNT(*) AS total
            FROM products
            WHERE seller_id = ?
        """, (session["user_id"],)).fetchone()["total"]
    finally:
        connection.close()

    return render_template(
        "seller_dashboard.html",
        products=my_products,
        product_count=count,
        product_limit=FREE_PRODUCT_LIMIT
    )

@app.route("/seller/orders")
def seller_orders():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "seller":
        return redirect(url_for("home"))

    connection = get_db_connection()

    try:
        orders = connection.execute("""
            SELECT
                o.id AS order_id,
                o.quantity,
                o.total_price,
                o.status,
                o.created_at,
                p.name AS product_name,
                p.image AS product_image,
                u.name AS buyer_name,
                u.email AS buyer_email
            FROM orders o
            JOIN products p ON p.id = o.product_id
            JOIN users u ON u.id = o.buyer_id
            WHERE p.seller_id = ?
            ORDER BY o.created_at DESC, o.id DESC
        """, (session["user_id"],)).fetchall()
    finally:
        connection.close()

    return render_template("seller_orders.html", orders=orders)

@app.route("/seller/orders/<int:order_id>/status", methods=["POST"])
def update_order_status(order_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "seller":
        return redirect(url_for("home"))

    new_status = request.form.get("status", "").strip()

    allowed_statuses = {
        "Pending",
        "Confirmed",
        "Shipped",
        "Completed",
        "Cancelled"
    }

    if new_status not in allowed_statuses:
        return "Invalid order status.", 400

    connection = get_db_connection()

    try:
        result = connection.execute("""
            UPDATE orders
            SET status = ?
            WHERE id = ?
              AND product_id IN (
                  SELECT id
                  FROM products
                  WHERE seller_id = ?
              )
        """, (new_status, order_id, session["user_id"]))

        connection.commit()

        if result.rowcount == 0:
            abort(404)

    finally:
        connection.close()

    return redirect(url_for("seller_orders"))


@app.route("/seller/product/add", methods=["GET", "POST"])
def add_product():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "seller":
        return redirect(url_for("home"))

    error = None

    connection = get_db_connection()
    try:
        count = connection.execute("""
            SELECT COUNT(*) AS total
            FROM products
            WHERE seller_id = ?
        """, (session["user_id"],)).fetchone()["total"]
    finally:
        connection.close()

    if count >= FREE_PRODUCT_LIMIT:
        return render_template(
            "add_product.html",
            error="You have reached your free limit of 5 products."
        )

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        price_text = request.form.get("price", "").strip()
        quantity_text = request.form.get("quantity", "1").strip()

        image_file = request.files.get("image")
        image_path = ""

        try:
            price = float(price_text)
            quantity = int(quantity_text)

            if not name or not category:
                raise ValueError(
                    "Enter a product name and category."
                )

            if price < 0 or quantity < 0:
                raise ValueError(
                    "Price and quantity cannot be negative."
                )

            if not price_text or not quantity_text:
                raise ValueError(
                    "Enter a price and quantity."
                )

        except ValueError as exc:
            error = str(exc) or "Enter a valid price and quantity."

        # Validate the image if the seller selected one.
        if not error and image_file and image_file.filename:
            original_name = secure_filename(image_file.filename)

            if not original_name or "." not in original_name:
                error = "Please choose a valid image file."
            else:
                extension = original_name.rsplit(".", 1)[1].lower()

                if extension not in ALLOWED_EXTENSIONS:
                    error = (
                        "Image must be JPG, JPEG, PNG, WEBP or GIF."
                    )
                else:
                    unique_name = (
                        uuid.uuid4().hex + "." + extension
                    )

                    image_path = "/static/uploads/" + unique_name

        if not error:
            connection = get_db_connection()
            try:
                # Recheck the limit before inserting.
                current_count = connection.execute("""
                    SELECT COUNT(*) AS total
                    FROM products
                    WHERE seller_id = ?
                """, (session["user_id"],)).fetchone()["total"]

                if current_count >= FREE_PRODUCT_LIMIT:
                    error = (
                        "You have reached your free limit of 5 products."
                    )
                else:
                    # Save the image only after validation.
                    if image_file and image_file.filename:
                        image_file.seek(0)
                        image_file.save(
                            os.path.join(
                                UPLOAD_FOLDER, unique_name
                            )
                        )

                    connection.execute("""
                        INSERT INTO products
                        (seller_id, name, category, price,
                         quantity, image, description)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        session["user_id"],
                        name,
                        category,
                        price,
                        quantity,
                        image_path,
                        description
                    ))

                    connection.commit()

            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

            if not error:
                return redirect(url_for("seller_dashboard"))

    return render_template(
        "add_product.html",
        error=error
    )

@app.route("/product/<int:product_id>")
def product_detail(product_id):
    connection = get_db_connection()
    try:
        product = connection.execute("""
            SELECT p.*, u.name AS seller_name
            FROM products p
            JOIN users u ON u.id = p.seller_id
            WHERE p.id = ? AND u.role = 'seller'
        """, (product_id,)).fetchone()
    finally:
        connection.close()

    if product is None:
        abort(404)

    return render_template("product.html", product=product)


@app.route("/seller/<int:seller_id>")
def seller_profile(seller_id):
    connection = get_db_connection()
    try:
        seller = connection.execute("""
            SELECT id, name, created_at
            FROM users
            WHERE id = ? AND role = 'seller'
        """, (seller_id,)).fetchone()

        if seller is None:
            abort(404)

        products = connection.execute("""
            SELECT *
            FROM products
            WHERE seller_id = ? AND quantity > 0
            ORDER BY created_at DESC, id DESC
        """, (seller_id,)).fetchall()
    finally:
        connection.close()

    return render_template(
        "seller_profile.html",
        seller=seller,
        products=products
    )


@app.route("/buy/<int:product_id>")
def buy_product(product_id):
    if "user_id" not in session:
        return redirect(url_for(
            "login",
            next=url_for("buy_product", product_id=product_id)
        ))

    if session.get("user_role") != "buyer":
        return "Only buyer accounts can purchase products.", 403

    connection = get_db_connection()
    try:
        product = connection.execute("""
            SELECT p.*, u.name AS seller_name
            FROM products p
            JOIN users u ON u.id = p.seller_id
            WHERE p.id = ?
              AND p.quantity > 0
              AND u.role = 'seller'
        """, (product_id,)).fetchone()
    finally:
        connection.close()

    if product is None:
        abort(404)

    return render_template("checkout.html", product=product)

@app.route("/checkout/<int:product_id>", methods=["POST"])
def checkout(product_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "buyer":
        return "Only buyer accounts can place orders.", 403

    # Get and validate quantity
    try:
        quantity = int(request.form.get("quantity", "1"))
        if quantity < 1:
            raise ValueError
    except (ValueError, TypeError):
        return "Please enter a valid quantity.", 400

    connection = get_db_connection()

    try:
        # Retrieve the product and seller
        product = connection.execute("""
            SELECT p.id, p.name, p.price, p.quantity, p.seller_id
            FROM products p
            JOIN users u ON u.id = p.seller_id
            WHERE p.id = ? AND u.role = 'seller'
        """, (product_id,)).fetchone()

        if product is None:
            abort(404)

        if quantity > product["quantity"]:
            return "Sorry, there is not enough stock available.", 400

        # Calculate total price
        total_price = product["price"] * quantity

        # Insert the order
        cursor = connection.execute("""
            INSERT INTO orders
                (buyer_id, product_id, quantity, total_price)
            VALUES (?, ?, ?, ?)
        """, (
            session["user_id"],
            product_id,
            quantity,
            total_price
        ))

        order_id = cursor.lastrowid

        # Notify the seller
        connection.execute("""
            INSERT INTO notifications (user_id, order_id, message)
            VALUES (?, ?, ?)
        """, (
            product["seller_id"],
            order_id,
            f"New order received for {product['name']}."
        ))

        # Reduce available stock
        stock_update = connection.execute("""
            UPDATE products
            SET quantity = quantity - ?
            WHERE id = ? AND quantity >= ?
        """, (quantity, product_id, quantity))

        if stock_update.rowcount != 1:
            raise ValueError("Stock changed. Please try again.")

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    return redirect(url_for("order_success"))
@app.route("/order-success")
def order_success():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "buyer":
        return redirect(url_for("home"))

    return render_template("order_success.html")

@app.route("/my-orders")
def my_orders():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "buyer":
        return redirect(url_for("home"))

    connection = get_db_connection()
    try:
        orders = connection.execute("""
            SELECT
                o.id AS order_id,
                o.quantity,
                o.total_price,
                o.status,
                o.created_at,
                p.name AS product_name,
                p.image AS product_image,
                p.category AS product_category,
                u.name AS seller_name
            FROM orders o
            JOIN products p ON p.id = o.product_id
            JOIN users u ON u.id = p.seller_id
            WHERE o.buyer_id = ?
            ORDER BY o.created_at DESC, o.id DESC
        """, (session["user_id"],)).fetchall()
    finally:
        connection.close()

    return render_template("my_orders.html", orders=orders)

@app.route("/notifications")
def notifications():
    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_db_connection()

    try:
        items = connection.execute("""
            SELECT id, order_id, message, is_read, created_at
            FROM notifications
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
        """, (session["user_id"],)).fetchall()

        connection.execute("""
            UPDATE notifications
            SET is_read = 1
            WHERE user_id = ?
        """, (session["user_id"],))

        connection.commit()
    finally:
        connection.close()

    return render_template("notifications.html", notifications=items)



# Create missing tables without deleting existing records.
init_db()

if __name__ == "__main__":
    app.run(debug=True)
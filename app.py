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
from flask import flash

import os
import requests
from dotenv import load_dotenv

load_dotenv()

import uuid
from datetime import datetime, date
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")

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

    connection.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            reference TEXT NOT NULL UNIQUE,
            amount_kobo INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'initialized',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id)
        )
    """)

    columns = [      
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(orders)"
            ).fetchall()
        ]
        
    if "payment_status" not in columns:
            connection.execute("""
                ALTER TABLE orders
                ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'Unpaid'
            """)
        # Check existing order columns
    order_columns = [
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(orders)"
        ).fetchall()
    ]

    # Add delivery information without deleting existing orders
    delivery_columns = {
        "delivery_region": "TEXT",
        "delivery_town": "TEXT",
        "delivery_address": "TEXT",
        "delivery_phone": "TEXT",
        "expected_delivery_date": "TEXT",
        "delivery_start_time": "TEXT",
        "delivery_end_time": "TEXT",
        "delivery_status": "TEXT NOT NULL DEFAULT 'Pending'",
        "delivered_at": "TEXT"
    }

    for column_name, column_type in delivery_columns.items():
        if column_name not in order_columns:
            connection.execute(
                f"ALTER TABLE orders ADD COLUMN {column_name} {column_type}"
            )

    # Store buyer reviews of sellers
    connection.execute("""
        CREATE TABLE IF NOT EXISTS seller_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL UNIQUE,
            buyer_id INTEGER NOT NULL,
            seller_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id),
            FOREIGN KEY (buyer_id) REFERENCES users(id),
            FOREIGN KEY (seller_id) REFERENCES users(id)
        )
    """) 
    # Add seller profile fields without deleting existing data
    connection = get_db_connection()

    try:
        user_columns = [
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(users)"
            ).fetchall()
        ]

        if "profile_image" not in user_columns:
            connection.execute("""
                ALTER TABLE users
                ADD COLUMN profile_image TEXT
            """)

        if "shop_description" not in user_columns:
            connection.execute("""
                ALTER TABLE users
                ADD COLUMN shop_description TEXT
            """)

        connection.commit()

    finally:
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
    # Get search and category values from the homepage
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()

    # Create the database connection BEFORE using it
    connection = get_db_connection()

    try:
        # Main query: get products, seller names, and seller ratings
        query = """
            SELECT
                p.*,
                u.name AS seller_name,

                (
                    SELECT AVG(sr.rating)
                    FROM seller_reviews AS sr
                    WHERE sr.seller_id = p.seller_id
                ) AS seller_average_rating,

                (
                    SELECT COUNT(sr.id)
                    FROM seller_reviews AS sr
                    WHERE sr.seller_id = p.seller_id
                ) AS seller_review_count

            FROM products AS p
            JOIN users AS u ON u.id = p.seller_id
            WHERE u.role = 'seller'
              
        """

        parameters = []

        # Filter products by the search text
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

        # Filter products by category
        if category:
            query += " AND p.category = ?"
            parameters.append(category)

        # Show the newest products first
        query += " ORDER BY p.created_at DESC, p.id DESC"

        # Run the product query
        products = connection.execute(
            query, parameters
        ).fetchall()

        # Get categories for the category dropdown
        categories = connection.execute("""
            SELECT DISTINCT category
            FROM products
            WHERE quantity > 0
            ORDER BY category
        """).fetchall()

    finally:
        # Always close the database connection
        connection.close()

    # Send the results to the homepage
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
        # Count all orders for this seller's products
        stats = connection.execute("""
            SELECT
                COUNT(o.id) AS total_orders,

                COALESCE(SUM(
                    CASE
                        WHEN o.payment_status = 'Paid' THEN 1
                        ELSE 0
                    END
                ), 0) AS paid_orders,

                COALESCE(SUM(
                    CASE
                        WHEN o.payment_status IS NULL
                             OR o.payment_status != 'Paid'
                        THEN 1
                        ELSE 0
                    END
                ), 0) AS unpaid_orders,

                COALESCE(SUM(
                    CASE
                        WHEN o.payment_status = 'Paid'
                        THEN o.total_price
                        ELSE 0
                    END
                ), 0) AS total_revenue

            FROM orders o
            JOIN products p ON p.id = o.product_id
            WHERE p.seller_id = ?
        """, (session["user_id"],)).fetchone()

        # Keep the seller's existing product list available
        products = connection.execute("""
            SELECT *
            FROM products
            WHERE seller_id = ?
            ORDER BY id DESC
        """, (session["user_id"],)).fetchall()

    finally:
        connection.close()

    return render_template(
        "seller_dashboard.html",
        stats=stats,
        products=products
    )
@app.route("/seller-analytics")
def seller_analytics():
    # Only logged-in sellers can view their analytics
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "seller":
        flash("Only sellers can view seller analytics.", "error")
        return redirect(url_for("home"))

    seller_id = session["user_id"]
    connection = get_db_connection()

    try:
        # 1. Product and stock statistics
        inventory = connection.execute("""
            SELECT
                COUNT(*) AS total_products,
                COALESCE(SUM(quantity), 0) AS total_stock,
                COALESCE(SUM(
                    CASE WHEN quantity > 0 AND quantity <= 5
                         THEN 1 ELSE 0 END
                ), 0) AS low_stock_products,
                COALESCE(SUM(
                    CASE WHEN quantity = 0
                         THEN 1 ELSE 0 END
                ), 0) AS out_of_stock_products
            FROM products
            WHERE seller_id = ?
        """, (seller_id,)).fetchone()

        # 2. Order statistics for this seller
        sales = connection.execute("""
            SELECT
                COUNT(o.id) AS total_orders,
                COALESCE(SUM(
                    CASE WHEN LOWER(o.payment_status) = 'paid'
                         THEN o.total_price ELSE 0 END
                ), 0) AS paid_sales,
                COALESCE(SUM(
                    CASE WHEN LOWER(o.payment_status) = 'paid'
                         THEN 1 ELSE 0 END
                ), 0) AS paid_orders
            FROM orders AS o
            JOIN products AS p ON p.id = o.product_id
            WHERE p.seller_id = ?
        """, (seller_id,)).fetchone()

        # 3. Customer rating statistics
        ratings = connection.execute("""
            SELECT
                AVG(rating) AS average_rating,
                COUNT(*) AS review_count
            FROM seller_reviews
            WHERE seller_id = ?
        """, (seller_id,)).fetchone()

        # 4. Recent orders
        recent_orders = connection.execute("""
            SELECT
                o.id AS order_id,
                o.quantity,
                o.total_price,
                o.payment_status,
                o.created_at,
                p.name AS product_name
            FROM orders AS o
            JOIN products AS p ON p.id = o.product_id
            WHERE p.seller_id = ?
            ORDER BY o.created_at DESC, o.id DESC
            LIMIT 5
        """, (seller_id,)).fetchall()
        monthly_sales = [
    dict(row) for row in connection.execute("""
        SELECT
            strftime('%Y-%m', o.created_at) AS sales_month,
            COALESCE(SUM(
                CASE
                    WHEN LOWER(o.payment_status) = 'paid'
                    THEN o.total_price
                    ELSE 0
                END
            ), 0) AS revenue,
            COALESCE(SUM(
                CASE
                    WHEN LOWER(o.payment_status) = 'paid'
                    THEN 1
                    ELSE 0
                END
            ), 0) AS paid_orders
        FROM orders AS o
        JOIN products AS p ON p.id = o.product_id
        WHERE p.seller_id = ?
          AND o.created_at >= date(
              'now', '-11 months', 'start of month'
          )
        GROUP BY strftime('%Y-%m', o.created_at)
        ORDER BY sales_month
    """, (seller_id,)).fetchall()
]
        

    finally:
        connection.close()

    return render_template(
        "seller_analytics.html",
        inventory=inventory,
        sales=sales,
        ratings=ratings,
        recent_orders=recent_orders,
        monthly_sales=monthly_sales
    )
@app.route("/edit-seller-profile", methods=["GET", "POST"])
def edit_seller_profile():
    # Only logged-in sellers can edit their profiles
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "seller":
        flash("Only sellers can edit seller profiles.", "error")
        return redirect(url_for("home"))

    connection = get_db_connection()

    try:
        seller = connection.execute("""
            SELECT id, name, profile_image, shop_description
            FROM users
            WHERE id = ? AND role = 'seller'
        """, (session["user_id"],)).fetchone()

        if seller is None:
            flash("Seller account not found.", "error")
            return redirect(url_for("home"))

        if request.method == "POST":
            shop_name = request.form.get("shop_name", "").strip()
            shop_description = request.form.get(
                "shop_description", ""
            ).strip()

            if not shop_name:
                flash("Shop name cannot be empty.", "error")
                return render_template(
                    "edit_seller_profile.html",
                    seller=seller
                )

            if len(shop_name) > 100:
                flash("Shop name must be 100 characters or less.", "error")
                return render_template(
                    "edit_seller_profile.html",
                    seller=seller
                )

            if len(shop_description) > 1000:
                flash("Description must be 1000 characters or less.", "error")
                return render_template(
                    "edit_seller_profile.html",
                    seller=seller
                )

            # Keep the current image unless a new one is uploaded
            profile_image = seller["profile_image"]

            image = request.files.get("profile_image")

            if image and image.filename:
                allowed_extensions = {"png", "jpg", "jpeg", "webp"}
                filename = secure_filename(image.filename)
                extension = filename.rsplit(".", 1)[-1].lower()

                if "." not in filename or extension not in allowed_extensions:
                    flash(
                        "Upload a PNG, JPG, JPEG, or WEBP image.",
                        "error"
                    )
                    return render_template(
                        "edit_seller_profile.html",
                        seller=seller
                    )

                # Create the uploads folder if it does not exist
                upload_folder = os.path.join(
                    app.root_path, "static", "uploads"
                )
                os.makedirs(upload_folder, exist_ok=True)

                # Give the image a unique filename
                image_filename = f"seller_{session['user_id']}_{uuid.uuid4().hex}.{extension}"

                image_path = os.path.join(
                    upload_folder, image_filename
                )
                image.save(image_path)

                # Save a web path, not the computer's full path
                profile_image = f"uploads/{image_filename}"

            connection.execute("""
                UPDATE users
                SET name = ?,
                    shop_description = ?,
                    profile_image = ?
                WHERE id = ? AND role = 'seller'
            """, (
                shop_name,
                shop_description,
                profile_image,
                session["user_id"]
            ))

            connection.commit()

            # Keep the displayed session name up to date
            session["user_name"] = shop_name

            flash("Your seller profile was updated successfully!", "success")
            return redirect(url_for(
                "seller_profile",
                seller_id=session["user_id"]
            ))

        return render_template(
            "edit_seller_profile.html",
            seller=seller
        )

    finally:
        connection.close()

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
                o.payment_status,
                o.created_at,
                o.delivery_region,
                o.delivery_town,
                o.delivery_address,
                o.delivery_phone,
                o.expected_delivery_date,
                o.delivery_start_time,
                o.delivery_end_time,
                o.delivery_status,
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

@app.route("/seller/update-delivery/<int:order_id>", methods=["POST"])
def update_delivery(order_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "seller":
        return "Only sellers can update delivery details.", 403

    delivery_date = request.form.get(
        "expected_delivery_date", ""
    ).strip()
    start_time = request.form.get(
        "delivery_start_time", ""
    ).strip()
    end_time = request.form.get(
        "delivery_end_time", ""
    ).strip()
    delivery_status = request.form.get(
        "delivery_status", ""
    ).strip()

    allowed_statuses = [
        "Pending",
        "Scheduled",
        "Out for Delivery",
        "Delivered"
    ]

    if delivery_status not in allowed_statuses:
        return "Invalid delivery status.", 400

    # Validate the date if one was entered
    if delivery_date:
        try:
            parsed_date = datetime.strptime(
                delivery_date, "%Y-%m-%d"
            ).date()

            if parsed_date < date.today():
                return "The delivery date cannot be in the past.", 400

        except ValueError:
            return "Please enter a valid delivery date.", 400

    # Validate the time window if provided
    if bool(start_time) != bool(end_time):
        return "Enter both the start and end times.", 400

    if start_time and end_time:
        try:
            start = datetime.strptime(start_time, "%H:%M")
            end = datetime.strptime(end_time, "%H:%M")

            if start >= end:
                return "End time must be later than start time.", 400

        except ValueError:
            return "Please enter valid delivery times.", 400

    if delivery_status == "Scheduled":
        if not delivery_date or not start_time or not end_time:
            return (
                "Enter a delivery date, start time, and end time "
                "before scheduling.",
                400
            )

    connection = get_db_connection()

    try:
        # Ensure this order belongs to the logged-in seller
        order = connection.execute("""
            SELECT o.id, o.buyer_id, p.name AS product_name
            FROM orders o
            JOIN products p ON p.id = o.product_id
            WHERE o.id = ? AND p.seller_id = ?
        """, (order_id, session["user_id"])).fetchone()

        if order is None:
            abort(404)

        # Prevent changing a completed delivery
        current = connection.execute("""
            SELECT delivery_status
            FROM orders
            WHERE id = ?
        """, (order_id,)).fetchone()

        if current["delivery_status"] == "Delivered":
            return "This order is already marked as delivered.", 400

        delivered_at = (
            datetime.now().isoformat(timespec="seconds")
            if delivery_status == "Delivered"
            else None
        )

        connection.execute("""
            UPDATE orders
            SET expected_delivery_date = ?,
                delivery_start_time = ?,
                delivery_end_time = ?,
                delivery_status = ?,
                delivered_at = ?
            WHERE id = ?
        """, (
            delivery_date or None,
            start_time or None,
            end_time or None,
            delivery_status,
            delivered_at,
            order_id
        ))

        # Notify the buyer about the delivery update
        message = (
            f"Delivery update for {order['product_name']}: "
            f"{delivery_status}."
        )

        if delivery_date:
            message += f" Expected date: {delivery_date}."

        if start_time and end_time:
            message += f" Time window: {start_time}–{end_time}."

        connection.execute("""
            INSERT INTO notifications (user_id, order_id, message)
            VALUES (?, ?, ?)
        """, (
            order["buyer_id"],
            order_id,
            message
        ))

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    return redirect(url_for("seller_orders"))

@app.route("/review-seller/<int:order_id>", methods=["POST"])
def review_seller(order_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "buyer":
        return "Only buyers can review sellers.", 403

    # Validate the rating
    try:
        rating = int(request.form.get("rating", ""))
        if rating < 1 or rating > 5:
            raise ValueError
    except (ValueError, TypeError):
        return "Please select a rating from 1 to 5.", 400

    comment = request.form.get("comment", "").strip()

    if len(comment) > 1000:
        return "Your review must be 1000 characters or fewer.", 400

    connection = get_db_connection()

    try:
        # Confirm the order belongs to this buyer and was delivered
        order = connection.execute("""
            SELECT
                o.id,
                o.buyer_id,
                o.delivery_status,
                p.seller_id,
                p.name AS product_name
            FROM orders o
            JOIN products p ON p.id = o.product_id
            WHERE o.id = ? AND o.buyer_id = ?
        """, (order_id, session["user_id"])).fetchone()

        if order is None:
            abort(404)

        if order["delivery_status"] != "Delivered":
            return "You can review the seller after delivery.", 400

        # Prevent duplicate reviews for the same order
        existing_review = connection.execute("""
            SELECT id
            FROM seller_reviews
            WHERE order_id = ?
        """, (order_id,)).fetchone()

        if existing_review:
            return "You have already reviewed this order.", 400

        # Save the review
        connection.execute("""
            INSERT INTO seller_reviews
                (order_id, buyer_id, seller_id, rating, comment)
            VALUES (?, ?, ?, ?, ?)
        """, (
            order_id,
            session["user_id"],
            order["seller_id"],
            rating,
            comment or None
        ))

        # Notify the seller
        connection.execute("""
            INSERT INTO notifications (user_id, order_id, message)
            VALUES (?, ?, ?)
        """, (
            order["seller_id"],
            order_id,
            f"A buyer reviewed your product: {order['product_name']}."
        ))

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    return redirect(url_for("my_orders"))

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
        # Get the public details of this seller
        seller = connection.execute("""
            SELECT
                id,
                name,
                profile_image,
                shop_description,
                created_at
            FROM users
            WHERE id = ?
              AND role = 'seller'
        """, (seller_id,)).fetchone()

        # Show an error page if the seller does not exist
        if seller is None:
            abort(404)

        # Get this seller's products and rating information
        products = connection.execute("""
            SELECT
                p.*,
                (
                    SELECT AVG(sr.rating)
                    FROM seller_reviews AS sr
                    WHERE sr.seller_id = p.seller_id
                ) AS seller_average_rating,
                (
                    SELECT COUNT(sr.id)
                    FROM seller_reviews AS sr
                    WHERE sr.seller_id = p.seller_id
                ) AS seller_review_count
            FROM products AS p
            WHERE p.seller_id = ?
              AND p.quantity > 0
            ORDER BY p.created_at DESC, p.id DESC
        """, (seller_id,)).fetchall()

        # Calculate the seller's overall rating
        rating = connection.execute("""
            SELECT
                AVG(rating) AS average_rating,
                COUNT(*) AS review_count
            FROM seller_reviews
            WHERE seller_id = ?
        """, (seller_id,)).fetchone()
        # Get customer reviews for this seller
        reviews = connection.execute("""
            SELECT
                sr.rating,
                sr.comment,
                sr.created_at,
                buyer.name AS buyer_name
            FROM seller_reviews AS sr
            JOIN users AS buyer
                ON buyer.id = sr.buyer_id
            WHERE sr.seller_id = ?
            ORDER BY sr.created_at DESC, sr.id DESC
        """, (seller_id,)).fetchall()

    finally:
        connection.close()

    return render_template(
        "seller_profile.html",
        seller=seller,
        products=products,
        average_rating=rating["average_rating"],
        review_count=rating["review_count"],
        reviews=reviews
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

    # Validate quantity
    try:
        quantity = int(request.form.get("quantity", "1"))
        if quantity < 1:
            raise ValueError
    except (ValueError, TypeError):
        return "Please enter a valid quantity.", 400

    # Get delivery details from the checkout form
    delivery_region = request.form.get("delivery_region", "").strip()
    delivery_town = request.form.get("delivery_town", "").strip()
    delivery_address = request.form.get("delivery_address", "").strip()
    delivery_phone = request.form.get("delivery_phone", "").strip()

    if not all([
        delivery_region,
        delivery_town,
        delivery_address,
        delivery_phone
    ]):
        return "Please complete all delivery details.", 400

    connection = get_db_connection()

    try:
        # Retrieve product and seller
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

        total_price = product["price"] * quantity

        # Create order with delivery details
        cursor = connection.execute("""
            INSERT INTO orders (
                buyer_id,
                product_id,
                quantity,
                total_price,
                delivery_region,
                delivery_town,
                delivery_address,
                delivery_phone
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session["user_id"],
            product_id,
            quantity,
            total_price,
            delivery_region,
            delivery_town,
            delivery_address,
            delivery_phone
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

    # Continue to Paystack payment
    return redirect(
        url_for("initialize_payment", order_id=order_id)
    )
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
                o.id AS id,
                o.quantity,
                o.total_price,
                o.status,
                o.payment_status,
                o.created_at,

                o.delivery_region,
                o.delivery_town,
                o.delivery_address,
                o.delivery_phone,
                o.expected_delivery_date,
                o.delivery_start_time,
                o.delivery_end_time,
                o.delivery_status,
                o.delivered_at,

                p.name AS product_name,
                p.image AS product_image,
                p.price AS product_price,

                seller.name AS seller_name,

                (
                    SELECT sr.id
                    FROM seller_reviews AS sr
                    WHERE sr.order_id = o.id
                      AND sr.buyer_id = o.buyer_id
                ) AS review_id,

                (
                    SELECT sr.rating
                    FROM seller_reviews AS sr
                    WHERE sr.order_id = o.id
                      AND sr.buyer_id = o.buyer_id
                ) AS review_rating,

                (
                    SELECT sr.comment
                    FROM seller_reviews AS sr
                    WHERE sr.order_id = o.id
                      AND sr.buyer_id = o.buyer_id
                ) AS review_comment

            FROM orders AS o

            JOIN products AS p
                ON p.id = o.product_id

            JOIN users AS seller
                ON seller.id = p.seller_id

            WHERE o.buyer_id = ?

            ORDER BY o.created_at DESC, o.id DESC
        """, (session["user_id"],)).fetchall()

    finally:
        connection.close()

    return render_template(
        "my_orders.html",
        orders=orders
    )
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

@app.route("/pay/<int:order_id>")
def initialize_payment(order_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("user_role") != "buyer":
        return redirect(url_for("home"))

    if not PAYSTACK_SECRET_KEY:
        return "Paystack secret key is missing from .env.", 500

    connection = get_db_connection()

    try:
        order = connection.execute("""
            SELECT o.id, o.total_price, u.email
            FROM orders o
            JOIN users u ON u.id = o.buyer_id
            WHERE o.id = ? AND o.buyer_id = ?
        """, (order_id, session["user_id"])).fetchone()

        if order is None:
            abort(404)

        paid = connection.execute("""
            SELECT id FROM payments
            WHERE order_id = ? AND status = 'success'
        """, (order_id,)).fetchone()

        if paid:
            return "This order has already been paid.", 400

        amount_kobo = round(float(order["total_price"]) * 100)
        reference = "COS-" + uuid.uuid4().hex

        connection.execute("""
            INSERT INTO payments
                (order_id, reference, amount_kobo, status)
            VALUES (?, ?, ?, 'initialized')
        """, (order_id, reference, amount_kobo))

        connection.commit()

        payload = {
            "email": order["email"],
            "amount": amount_kobo,
            "currency": "GHS",
            "reference": reference,
            "callback_url": url_for(
                "verify_payment", _external=True
            )
        }

        response = requests.post(
            "https://api.paystack.co/transaction/initialize",
            headers={
                "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=30
        )

        result = response.json()

        if response.ok and result.get("status"):
            return redirect(
                result["data"]["authorization_url"]
            )

        connection.execute("""
            UPDATE payments
            SET status = 'failed'
            WHERE reference = ?
        """, (reference,))
        connection.commit()

        return "Could not start payment. Please try again.", 502

    except requests.RequestException:
        connection.rollback()
        return "Could not connect to Paystack. Please try again.", 502

    finally:
        connection.close()


@app.route("/payment/callback")
def verify_payment():
    reference = request.args.get("reference", "").strip()

    if not reference:
        return "Payment reference is missing.", 400

    if not PAYSTACK_SECRET_KEY:
        return "Paystack secret key is missing.", 500

    connection = get_db_connection()

    try:
        payment = connection.execute("""
            SELECT p.id, p.order_id, p.amount_kobo, p.status,
                   o.buyer_id
            FROM payments p
            JOIN orders o ON o.id = p.order_id
            WHERE p.reference = ?
        """, (reference,)).fetchone()

        if payment is None:
            abort(404)

        if payment["status"] == "success":
            return render_template(
                "payment_result.html",
                success=True,
                message="This payment has already been verified."
            )

        response = requests.get(
            "https://api.paystack.co/transaction/verify/"
            + requests.utils.quote(reference, safe=""),
            headers={
                "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"
            },
            timeout=30
        )

        result = response.json()

        if not response.ok or not result.get("status"):
            return "Could not verify payment. Please try again.", 502

        transaction = result.get("data", {})

        valid_payment = (
            transaction.get("status") == "success"
            and transaction.get("reference") == reference
            and transaction.get("amount") == payment["amount_kobo"]
            and transaction.get("currency") == "GHS"
        )

        if valid_payment:
            connection.execute("""
                UPDATE payments
                SET status = 'success'
                WHERE id = ?
            """, (payment["id"],))

            connection.execute("""
    UPDATE orders
    SET payment_status = 'Paid'
    WHERE id = ?
""", (payment["order_id"],))
            

            connection.commit()

            return render_template(
                "payment_result.html",
                success=True,
                message="Payment verified successfully!"
            )

        if transaction.get("status") in ("failed", "abandoned"):
            connection.execute("""
                UPDATE payments
                SET status = 'failed'
                WHERE id = ?
            """, (payment["id"],))
            connection.commit()

        return render_template(
            "payment_result.html",
            success=False,
            message="Payment is not confirmed. Do not pay again "
                    "until you check your order and payment status."
        )

    except requests.RequestException:
        connection.rollback()
        return "Could not contact Paystack to verify payment.", 502

    finally:
        connection.close()



# Create missing tables without deleting existing records.
init_db()

if __name__ == "__main__":
    app.run(debug=True)
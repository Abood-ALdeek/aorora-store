import os
import re
import secrets
import sqlite3
import time
import uuid
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
from PIL import Image, ImageOps

from translations import T

DATA_DIR = os.environ.get("DATA_DIR", "/data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "store.db")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
CATEGORIES = ["straightener", "dryer", "curler", "package", "other"]

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=300 * 1024 * 1024,  # bulk import can carry many photos
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

_failed_logins = {}


# ---------- database ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name_en TEXT NOT NULL,
            name_ar TEXT NOT NULL DEFAULT '',
            desc_en TEXT NOT NULL DEFAULT '',
            desc_ar TEXT NOT NULL DEFAULT '',
            price REAL NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT 'other',
            image TEXT NOT NULL DEFAULT '',
            in_stock INTEGER NOT NULL DEFAULT 1,
            featured INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            order_ref TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'complaint',
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        """
    )
    defaults = {
        "whatsapp": os.environ.get("WHATSAPP_NUMBER", "962785012395"),
        "instagram": "https://www.instagram.com/aorora__1",
        "logo": "",
        "promo_on": "1",
        "promo_en": "Delivery to all governorates · 1-year guarantee on every product",
        "promo_ar": "توصيل لجميع المحافظات · كفالة سنة كاملة على كل المنتجات",
        "hero_en": "Beauty starts with your hair",
        "hero_ar": "جمالك يبدأ من شعرك",
        "site_url": "",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0 and \
            os.environ.get("SEED_EXAMPLES", "1") == "1":
        examples = [
            ("Silk Glide Hair Straightener", "مكواة الشعر سيلك جلايد",
             "Ceramic-tourmaline plates for smooth, shiny hair without frizz. Heats up in 30 seconds, adjustable temperature 150–230 °C, auto shut-off.",
             "ألواح سيراميك وتورمالين لشعر ناعم ولامع بدون تجعّد. تسخن خلال 30 ثانية، حرارة قابلة للتعديل من 150 إلى 230 درجة، وإيقاف تلقائي.",
             35, "straightener", "/static/img/example-straightener.svg", 1),
            ("Cloud Ionic Hair Dryer", "مجفف الشعر كلاود أيوني",
             "2000 W ionic dryer that dries fast and protects your hair. 3 heat settings, cool-shot button and concentrator nozzle included.",
             "مجفف أيوني بقوة 2000 واط يجفف بسرعة ويحمي شعرك. 3 درجات حرارة، زر هواء بارد، وفوهة مركّزة مرفقة.",
             45, "dryer", "/static/img/example-dryer.svg", 1),
            ("Rose Wave Curler", "جهاز التمويج روز ويف",
             "Create soft beach waves or bouncy curls in minutes. 25 mm ceramic barrel with even heat and a cool tip for safe handling.",
             "احصلي على تموجات ناعمة أو تجعيدات مرحة خلال دقائق. أسطوانة سيراميك 25 ملم بحرارة متساوية وطرف بارد للاستخدام الآمن.",
             29.5, "curler", "/static/img/example-curler.svg", 1),
        ]
        conn.executemany(
            "INSERT INTO products(name_en,name_ar,desc_en,desc_ar,price,category,image,featured) VALUES (?,?,?,?,?,?,?,?)",
            examples,
        )
    # upgrade older databases
    cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    if "brand" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN brand TEXT NOT NULL DEFAULT ''")
    if "old_price" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN old_price REAL NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()


def all_brands():
    return [r[0] for r in db().execute(
        "SELECT brand FROM products WHERE brand != '' GROUP BY lower(brand) ORDER BY lower(brand)")]


def get_settings():
    return {r["key"]: r["value"] for r in db().execute("SELECT key, value FROM settings")}


def set_setting(key, value):
    db().execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))
    db().commit()


# ---------- language ----------
def current_lang():
    lang = request.cookies.get("lang", "en")
    return lang if lang in T else "en"


def has_arabic(text):
    return any("؀" <= ch <= "ۿ" for ch in (text or ""))


def pick_lang(en, ar, lang):
    """Return (text for this language, text for the other language).
    Works even if the Arabic and English boxes were filled the wrong way round."""
    en, ar = (en or "").strip(), (ar or "").strip()
    if has_arabic(en) and ar and not has_arabic(ar):
        en, ar = ar, en  # boxes swapped by mistake
    if lang == "ar":
        return (ar or en), (en if ar else "")
    return (en or ar), (ar if en else "")


def product_view(row, lang):
    p = dict(row)
    p["name"], p["name_alt"] = pick_lang(p["name_en"], p["name_ar"], lang)
    p["desc"], _ = pick_lang(p["desc_en"], p["desc_ar"], lang)
    old = p.get("old_price") or 0
    p["on_sale"] = bool(old and old > p["price"])
    p["discount"] = int(round((1 - p["price"] / old) * 100)) if p["on_sale"] else 0
    return p


@app.context_processor
def inject_globals():
    lang = current_lang()
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {
        "lang": lang,
        "t": T[lang],
        "dir": "rtl" if lang == "ar" else "ltr",
        "settings": get_settings(),
        "csrf": session["csrf"],
        "categories": CATEGORIES,
        "fmt_price": fmt_price,
        "site_base": (get_settings().get("site_url") or request.host_url.rstrip("/")),
        "new_complaints": (db().execute("SELECT COUNT(*) FROM complaints WHERE status = 'new'").fetchone()[0]
                           if session.get("admin") else 0),
    }


def price_number(n):
    """35 -> '35', 24.5 -> '24.50', 9.995 -> '9.995' (Jordanian dinar has 3 decimals)."""
    s = f"{float(n):.3f}".rstrip("0").rstrip(".")
    if "." in s and len(s.split(".")[1]) == 1:
        s += "0"
    return s


def fmt_price(n):
    return f"{price_number(n)} {T[current_lang()]['currency']}"


def _old_price(raw, price):
    """Price before discount; ignored unless it is higher than the current price."""
    v = parse_price(raw) if str(raw or "").strip() else None
    return v if v and price is not None and v > price else 0


def parse_price(raw):
    s = str(raw or "")
    for junk in ("JOD", "jod", "JD", "jd", "د.أ", "دينار", " "):
        s = s.replace(junk, "")
    s = s.replace("٫", ".").replace(",", ".").strip()
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    try:
        v = round(float(s), 3)
    except ValueError:
        return None
    return v if v >= 0 else None


@app.route("/lang/<code>")
def set_lang(code):
    if code not in T:
        code = "en"
    nxt = request.args.get("next", "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    resp = redirect(nxt)
    resp.set_cookie("lang", code, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp


# ---------- storefront ----------
@app.route("/")
def home():
    lang = current_lang()
    cat = request.args.get("category", "")
    brand = request.args.get("brand", "").strip()
    offers = request.args.get("offers") == "1"
    q = "SELECT * FROM products"
    where, args = [], []
    if offers:
        where.append("old_price > price")
    if cat in CATEGORIES:
        where.append("category = ?")
        args.append(cat)
    else:
        cat = ""
    if brand:
        where.append("lower(brand) = lower(?)")
        args.append(brand)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY in_stock DESC, featured DESC, created_at DESC"
    products = [product_view(r, lang) for r in db().execute(q, args)]
    showcase = [product_view(r, lang) for r in db().execute(
        """SELECT * FROM products WHERE image != '' AND in_stock = 1
           ORDER BY featured DESC, created_at DESC LIMIT 3""")]
    deals = [product_view(r, lang) for r in db().execute(
        """SELECT * FROM products WHERE old_price > price AND in_stock = 1
           ORDER BY (old_price - price) / old_price DESC LIMIT 8""")]
    return render_template("index.html", products=products, active_cat=cat, offers=offers,
                           active_brand=brand, brands=all_brands(), showcase=showcase, deals=deals)


@app.route("/product/<int:pid>")
def product(pid):
    row = db().execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if not row:
        abort(404)
    lang = current_lang()
    related = [product_view(r, lang) for r in db().execute(
        "SELECT * FROM products WHERE id != ? ORDER BY category = ? DESC, created_at DESC LIMIT 3",
        (pid, row["category"]))]
    return render_template("product.html", p=product_view(row, lang), related=related)


@app.route("/cart")
def cart():
    return render_template("cart.html")


# ---------- order summary page + one combined photo for WhatsApp previews ----------
ORDER_CODE = re.compile(r"^\d{1,7}x\d{1,2}(-\d{1,7}x\d{1,2}){0,19}$")


def order_items(code):
    """'8x2-9x1' -> [(product, qty), ...] for products that exist"""
    if not ORDER_CODE.match(code or ""):
        return None
    lang = current_lang()
    items = []
    for part in code.split("-"):
        pid, qty = (int(x) for x in part.split("x"))
        row = db().execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
        if row and 1 <= qty <= 20:
            items.append((product_view(row, lang), qty))
    return items


@app.route("/order/<code>")
def order_page(code):
    items = order_items(code)
    if not items:
        abort(404)
    total = sum(p["price"] * q for p, q in items)
    return render_template("order.html", items=items, total=total, code=code)


def _product_photo(p):
    """open a product's photo as a PIL image, or None (e.g. SVG examples)"""
    img = p.get("image") or ""
    path = None
    if img.startswith("/uploads/"):
        path = os.path.join(UPLOAD_DIR, os.path.basename(img))
    elif img.startswith("/static/"):
        path = os.path.join(app.static_folder, img[len("/static/"):])
    if not path or not os.path.exists(path) or path.endswith(".svg"):
        return None
    try:
        return ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except Exception:
        return None


@app.route("/order/<code>.jpg")
def order_image(code):
    items = order_items(code)
    if not items:
        abort(404)
    cache_dir = os.path.join(DATA_DIR, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    stamp = max(int(p.get("created_at") or 0) for p, _ in items)
    path = os.path.join(cache_dir, f"order-{code}-{stamp}.jpg")
    if not os.path.exists(path):
        from PIL import ImageDraw, ImageFont
        W, H = 1200, 630  # standard link-preview size
        canvas = Image.new("RGB", (W, H), (255, 240, 246))
        d = ImageDraw.Draw(canvas)
        shown = items[:6]
        n = len(shown)
        cols = n if n <= 3 else 3
        rows = 1 if n <= 3 else 2
        pad = 24
        tile = min((W - pad * (cols + 1)) // cols, (H - pad * (rows + 1)) // rows)
        x0 = (W - (cols * tile + (cols - 1) * pad)) // 2
        y0 = (H - (rows * tile + (rows - 1) * pad)) // 2
        try:
            fnt = ImageFont.truetype("DejaVuSans-Bold.ttf", max(28, tile // 7))
        except Exception:
            fnt = ImageFont.load_default(size=max(28, tile // 7))
        for k, (p, q) in enumerate(shown):
            x = x0 + (k % cols) * (tile + pad)
            y = y0 + (k // cols) * (tile + pad)
            d.rounded_rectangle((x - 4, y - 4, x + tile + 4, y + tile + 4), 26, fill=(255, 255, 255))
            photo = _product_photo(p)
            if photo:
                photo = ImageOps.fit(photo, (tile, tile))
                mask = Image.new("L", (tile, tile), 0)
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, tile - 1, tile - 1), 22, fill=255)
                canvas.paste(photo, (x, y), mask)
            else:
                d.text((x + tile // 2, y + tile // 2), (p["name_en"] or "?")[:14], fill=(201, 58, 114), anchor="mm", font=fnt)
            if q > 1:
                bw = max(70, tile // 3)
                d.rounded_rectangle((x + tile - bw - 10, y + 10, x + tile - 10, y + 10 + bw // 2 + 10), 30, fill=(255, 31, 142))
                d.text((x + tile - 10 - bw // 2, y + 10 + (bw // 2 + 10) // 2), f"x{q}", fill="white", anchor="mm", font=fnt)
        if len(items) > 6:
            d.text((W - 30, H - 24), f"+{len(items) - 6}", fill=(201, 58, 114), anchor="rd", font=fnt)
        canvas.save(path, "JPEG", quality=85)
    return send_from_directory(cache_dir, os.path.basename(path), max_age=60 * 60 * 24)


@app.route("/api/products")
def api_products():
    lang = current_lang()
    rows = db().execute("SELECT * FROM products").fetchall()
    return jsonify([
        {k: v for k, v in product_view(r, lang).items()
         if k in ("id", "name", "name_alt", "price", "image", "in_stock")}
        for r in rows
    ])


@app.route("/uploads/<path:name>")
def uploads(name):
    return send_from_directory(UPLOAD_DIR, name, max_age=60 * 60 * 24 * 30)


# ---------- admin ----------
def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*a, **kw)
    return wrapper


def check_csrf():
    if request.form.get("csrf") != session.get("csrf"):
        abort(400)


def save_image(file_storage, max_side=1400):
    if not file_storage or not file_storage.filename:
        return None
    try:
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)
        has_alpha = img.mode in ("RGBA", "LA", "P")
        img = img.convert("RGBA" if has_alpha else "RGB")
        img.thumbnail((max_side, max_side))
    except Exception:
        return False
    name = f"{uuid.uuid4().hex}.{'png' if has_alpha else 'jpg'}"
    path = os.path.join(UPLOAD_DIR, name)
    if has_alpha:
        img.save(path, "PNG", optimize=True)
    else:
        img.save(path, "JPEG", quality=86, optimize=True)
    return f"/uploads/{name}"


def delete_upload(url):
    if url and url.startswith("/uploads/"):
        p = os.path.join(UPLOAD_DIR, os.path.basename(url))
        if os.path.exists(p):
            os.remove(p)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        check_csrf()
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]
        fails = [t for t in _failed_logins.get(ip, []) if time.time() - t < 900]
        if len(fails) >= 8:
            flash(T[current_lang()]["too_many_attempts"], "error")
            return render_template("admin_login.html"), 429
        if ADMIN_PASSWORD and secrets.compare_digest(request.form.get("password", "").strip().encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")):
            session.clear()
            session["admin"] = True
            session["csrf"] = secrets.token_hex(16)
            return redirect(url_for("admin"))
        fails.append(time.time())
        _failed_logins[ip] = fails
        time.sleep(1)
        flash(T[current_lang()]["wrong_password"], "error")
    return render_template("admin_login.html")


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    check_csrf()
    session.clear()
    return redirect(url_for("home"))


@app.route("/admin")
@admin_required
def admin():
    lang = current_lang()
    products = [product_view(r, lang) for r in db().execute("SELECT * FROM products ORDER BY created_at DESC")]
    return render_template("admin.html", products=products)


@app.route("/admin/product/new", methods=["GET", "POST"])
@app.route("/admin/product/<int:pid>", methods=["GET", "POST"])
@admin_required
def admin_edit(pid=None):
    row = None
    if pid is not None:
        row = db().execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
        if not row:
            abort(404)
    if request.method == "POST":
        check_csrf()
        f = request.form
        t = T[current_lang()]
        name_en = f.get("name_en", "").strip()
        price = parse_price(f.get("price"))
        if not name_en or price is None:
            flash(t["fill_required"], "error")
            return render_template("admin_edit.html", p=dict(f) | {"id": pid, "image": row["image"] if row else ""})
        image = row["image"] if row else ""
        new_img = save_image(request.files.get("image"))
        if new_img is False:
            flash(t["bad_image"], "error")
            return render_template("admin_edit.html", p=dict(f) | {"id": pid, "image": image})
        if new_img:
            delete_upload(image)
            image = new_img
        values = (name_en, f.get("name_ar", "").strip(), f.get("desc_en", "").strip(),
                  f.get("desc_ar", "").strip(), price,
                  f.get("category") if f.get("category") in CATEGORIES else "other",
                  image, 1 if f.get("in_stock") else 0, 1 if f.get("featured") else 0,
                  " ".join(f.get("brand", "").split()),
                  _old_price(f.get("old_price"), price))
        if row:
            db().execute("""UPDATE products SET name_en=?, name_ar=?, desc_en=?, desc_ar=?, price=?,
                            category=?, image=?, in_stock=?, featured=?, brand=?, old_price=? WHERE id=?""", values + (pid,))
        else:
            db().execute("""INSERT INTO products(name_en,name_ar,desc_en,desc_ar,price,category,image,in_stock,featured,brand,old_price)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", values)
        db().commit()
        flash(t["saved"], "ok")
        if f.get("again"):
            return redirect(url_for("admin_edit", category=f.get("category"), brand=" ".join(f.get("brand", "").split())))
        return redirect(url_for("admin"))
    cat = request.args.get("category")
    p = dict(row) if row else {"in_stock": 1, "category": cat if cat in CATEGORIES else "straightener",
                               "brand": request.args.get("brand", "")}
    return render_template("admin_edit.html", p=p, brands=all_brands())


@app.route("/admin/product/<int:pid>/delete", methods=["POST"])
@admin_required
def admin_delete(pid):
    check_csrf()
    row = db().execute("SELECT image FROM products WHERE id = ?", (pid,)).fetchone()
    if row:
        delete_upload(row["image"])
        db().execute("DELETE FROM products WHERE id = ?", (pid,))
        db().commit()
        flash(T[current_lang()]["deleted"], "ok")
    return redirect(url_for("admin"))


@app.route("/admin/product/<int:pid>/stock", methods=["POST"])
@admin_required
def admin_toggle_stock(pid):
    check_csrf()
    db().execute("UPDATE products SET in_stock = 1 - in_stock WHERE id = ?", (pid,))
    db().commit()
    return redirect(url_for("admin"))


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == "POST":
        check_csrf()
        t = T[current_lang()]
        digits = "".join(ch for ch in request.form.get("whatsapp", "") if ch.isdigit())
        if digits.startswith("00"):
            digits = digits[2:]
        if len(digits) < 8:
            flash(t["bad_whatsapp"], "error")
            return redirect(url_for("admin_settings"))
        set_setting("whatsapp", digits)
        set_setting("instagram", request.form.get("instagram", "").strip())
        set_setting("promo_on", "1" if request.form.get("promo_on") else "0")
        set_setting("promo_en", request.form.get("promo_en", "").strip()[:200])
        set_setting("promo_ar", request.form.get("promo_ar", "").strip()[:200])
        set_setting("hero_en", request.form.get("hero_en", "").strip()[:120])
        set_setting("hero_ar", request.form.get("hero_ar", "").strip()[:120])
        site_url = request.form.get("site_url", "").strip().rstrip("/")
        if site_url and not site_url.startswith(("http://", "https://")):
            site_url = "https://" + site_url
        set_setting("site_url", site_url[:200])
        if request.form.get("remove_logo"):
            delete_upload(get_settings().get("logo"))
            set_setting("logo", "")
        logo = save_image(request.files.get("logo"), max_side=600)
        if logo is False:
            flash(t["bad_image"], "error")
            return redirect(url_for("admin_settings"))
        if logo:
            delete_upload(get_settings().get("logo"))
            set_setting("logo", logo)
        flash(t["saved"], "ok")
        return redirect(url_for("admin_settings"))
    return render_template("admin_settings.html")


# ---------- bulk import ----------
IMPORT_COLUMNS = ["name_en", "name_ar", "brand", "price", "old_price", "category", "desc_en", "desc_ar", "in_stock", "photo"]
CATEGORY_ALIASES = {
    "straightener": "straightener", "straighteners": "straightener", "مكواة": "straightener", "مكاوي": "straightener",
    "مكاوي الشعر": "straightener", "مكواة شعر": "straightener",
    "dryer": "dryer", "dryers": "dryer", "hair dryer": "dryer", "مجفف": "dryer", "مجففات": "dryer",
    "مجففات الشعر": "dryer", "سشوار": "dryer", "استشوار": "dryer",
    "curler": "curler", "curlers": "curler", "waver": "curler", "wavers": "curler", "تمويج": "curler",
    "مويجة": "curler", "أجهزة التمويج والتجعيد": "curler", "بيبي ليس": "curler",
    "package": "package", "packages": "package", "bundle": "package", "set": "package",
    "بكج": "package", "بكجات": "package", "باكج": "package", "باكيج": "package", "مجموعة": "package", "عروض": "package",
    "other": "other", "أخرى": "other", "اخرى": "other",
}
NO_WORDS = {"no", "n", "0", "false", "out", "لا", "غير متوفر", "نفذ", "نفذت"}


def _read_rows(file_storage):
    name = (file_storage.filename or "").lower()
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook
        wb = load_workbook(file_storage.stream, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = [["" if v is None else str(v).strip() for v in r] for r in ws.iter_rows(values_only=True)]
    elif name.endswith(".csv"):
        import csv
        import io
        raw = file_storage.read()
        for enc in ("utf-8-sig", "cp1256", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        sample = text[:2000]
        delim = ";" if sample.count(";") > sample.count(",") else ","
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]
    else:
        return None
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    header = [h.strip().lower().replace(" ", "_") for h in rows[0]]
    return [dict(zip(header, r)) for r in rows[1:]]


@app.route("/admin/import/template.xlsx")
@admin_required
def admin_import_template():
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "products"
    ws.append(IMPORT_COLUMNS)
    ws.append(["Silk Glide Hair Straightener", "مكواة الشعر سيلك جلايد", "BaByliss", 35, 45, "straightener",
               "Ceramic plates, 150–230 °C.", "ألواح سيراميك، حرارة من 150 إلى 230 درجة.", "yes", "straightener1.jpg"])
    ws.append(["Cloud Ionic Hair Dryer", "مجفف الشعر كلاود", "Dyson", 45, "", "dryer", "2000 W ionic dryer.", "مجفف أيوني 2000 واط.", "yes", "dryer1.jpg"])
    ws.append(["Rose Wave Curler", "جهاز التمويج روز ويف", "", 29.5, "", "curler", "", "", "no", ""])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="E8518A")
    for col, w in zip("ABCDEFGHIJ", (32, 32, 16, 10, 12, 14, 40, 40, 10, 20)):
        ws.column_dimensions[col].width = w
    help_ws = wb.create_sheet("help")
    for line in [
        ["Column", "What to write"],
        ["name_en", "Product name in English (required)"],
        ["name_ar", "Product name in Arabic (optional)"],
        ["brand", "Brand name, e.g. BaByliss, Dyson, Philips (optional)"],
        ["old_price", "Price before discount, for offers (optional). Must be higher than price."],
        ["price", "Price in Jordanian dinar (JD), numbers only, e.g. 35 or 24.5 (required)"],
        ["category", "straightener, dryer, curler, package or other"],
        ["desc_en / desc_ar", "Description in English / Arabic (optional)"],
        ["in_stock", "yes or no (empty = yes)"],
        ["photo", "File name of the photo, e.g. dryer1.jpg. Upload the photos together with this file."],
        ["", "Delete the example rows before importing. Each row = one product."],
    ]:
        help_ws.append(line)
    help_ws.column_dimensions["A"].width = 20
    help_ws.column_dimensions["B"].width = 80
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from flask import send_file
    return send_file(buf, as_attachment=True, download_name="aorora-products-template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/admin/import", methods=["GET", "POST"])
@admin_required
def admin_import():
    t = T[current_lang()]
    if request.method == "GET":
        return render_template("admin_import.html", result=None)
    check_csrf()
    sheet = request.files.get("sheet")
    rows = _read_rows(sheet) if sheet and sheet.filename else None
    if rows is None:
        flash(t["import_bad_file"], "error")
        return render_template("admin_import.html", result=None)

    photos = {}
    for f in request.files.getlist("photos"):
        if f and f.filename:
            photos[os.path.basename(f.filename).strip().lower()] = f

    added, errors, used_photos = 0, [], set()
    for i, r in enumerate(rows, start=2):
        name_en = (r.get("name_en") or "").strip()
        price = parse_price(r.get("price"))
        if not name_en or price is None:
            errors.append(f"{t['import_row']} {i}: {t['import_row_missing']}")
            continue
        cat = CATEGORY_ALIASES.get((r.get("category") or "").strip().lower(), "other")
        in_stock = 0 if (r.get("in_stock") or "").strip().lower() in NO_WORDS else 1
        image = ""
        photo_name = os.path.basename((r.get("photo") or "").strip()).lower()
        if photo_name:
            f = photos.get(photo_name)
            if f is None:
                errors.append(f"{t['import_row']} {i}: {t['import_photo_missing']} ({photo_name})")
            else:
                f.stream.seek(0)
                saved = save_image(f)
                if saved:
                    image = saved
                    used_photos.add(photo_name)
                else:
                    errors.append(f"{t['import_row']} {i}: {t['bad_image']} ({photo_name})")
        db().execute(
            """INSERT INTO products(name_en,name_ar,desc_en,desc_ar,price,category,image,in_stock,brand,old_price)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (name_en, (r.get("name_ar") or "").strip(), (r.get("desc_en") or "").strip(),
             (r.get("desc_ar") or "").strip(), price, cat, image, in_stock,
             " ".join((r.get("brand") or "").split()), _old_price(r.get("old_price"), price)))
        added += 1
    db().commit()
    unused = sorted(set(photos) - used_photos)
    return render_template("admin_import.html", result={"added": added, "errors": errors, "unused": unused})


# ---------- complaints & suggestions ----------
COMPLAINT_KINDS = ["complaint", "suggestion", "question"]
_complaint_posts = {}


@app.route("/complaints", methods=["GET", "POST"])
def complaints():
    t = T[current_lang()]
    if request.method == "POST":
        check_csrf()
        f = request.form
        if f.get("website"):  # hidden honeypot field: only bots fill it
            return redirect(url_for("complaints"))
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]
        recent = [x for x in _complaint_posts.get(ip, []) if time.time() - x < 3600]
        if len(recent) >= 5:
            flash(t["too_many_attempts"], "error")
            return render_template("complaints.html", form=f), 429
        name, phone, message = (f.get("name", "").strip()[:100], f.get("phone", "").strip()[:40],
                                f.get("message", "").strip()[:3000])
        if not name or not phone or len(message) < 5:
            flash(t["c_fill"], "error")
            return render_template("complaints.html", form=f)
        kind = f.get("kind") if f.get("kind") in COMPLAINT_KINDS else "complaint"
        cur = db().execute("INSERT INTO complaints(name, phone, order_ref, kind, message) VALUES (?,?,?,?,?)",
                           (name, phone, f.get("order_ref", "").strip()[:60], kind, message))
        db().commit()
        recent.append(time.time())
        _complaint_posts[ip] = recent
        return render_template("complaints.html", done=cur.lastrowid, form={})
    return render_template("complaints.html", form={})


@app.route("/admin/complaints")
@admin_required
def admin_complaints():
    show = request.args.get("show", "open")
    q = "SELECT * FROM complaints"
    if show != "all":
        q += " WHERE status = 'new'"
    rows = [dict(r) for r in db().execute(q + " ORDER BY created_at DESC")]
    for r in rows:
        r["when"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"]))
        digits = "".join(ch for ch in r["phone"] if ch.isdigit())
        if digits.startswith("00"):
            digits = digits[2:]
        elif digits.startswith("07") and len(digits) == 10:  # local Jordanian mobile
            digits = "962" + digits[1:]
        r["wa"] = digits
    return render_template("admin_complaints.html", rows=rows, show=show)


@app.route("/admin/complaints/<int:cid>/<action>", methods=["POST"])
@admin_required
def admin_complaint_action(cid, action):
    check_csrf()
    if action == "resolve":
        db().execute("UPDATE complaints SET status = 'resolved' WHERE id = ?", (cid,))
    elif action == "reopen":
        db().execute("UPDATE complaints SET status = 'new' WHERE id = ?", (cid,))
    elif action == "delete":
        db().execute("DELETE FROM complaints WHERE id = ?", (cid,))
    else:
        abort(404)
    db().commit()
    return redirect(url_for("admin_complaints", show=request.form.get("show", "open")))


@app.errorhandler(404)
def not_found(_e):
    return render_template("404.html"), 404


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)

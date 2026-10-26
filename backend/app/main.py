from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
import os
import asyncio
import httpx

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import engine, Base, AsyncSessionLocal, get_db
from app.core.config import get_settings
from app.core.security import get_password_hash, verify_password
from app.models import User, Store, Sale, LoginCode, InventoryTask, InventoryCount, InventoryLine
from app.models.store import StoreUser
from app.models.product import Category
from app.models.product import Product, ProductVariant
from app.services.excel_import import import_products_from_excel
from app.api import auth, stores, products, sales

settings = get_settings()
templates = Jinja2Templates(directory="app/templates")


async def create_owner_if_not_exists():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.role == "owner"))
        owner = result.scalar_one_or_none()
        if not owner:
            owner = User(
                full_name="Владелец",
                username="owner",
                role="owner",
                password_hash=get_password_hash(settings.OWNER_PASSWORD),
                is_active=True,
            )
            session.add(owner)
            await session.commit()
            print("✅ Создан владелец системы (логин: owner)")



async def keep_alive_loop():
    """Пинг своего /health, чтобы реже засыпать (нужен внешний URL)."""
    url = os.environ.get("KEEP_ALIVE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        print("ℹ️ KEEP_ALIVE_URL не задан — автопинг выключен")
        return
    health = url.rstrip("/") + "/health"
    print(f"💓 Keep-alive включён: {health}")
    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(health)
                print(f"💓 ping {r.status_code}")
        except Exception as e:
            print(f"💓 ping error: {e}")
        await asyncio.sleep(600)  # каждые 10 минут


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            from sqlalchemy import text as sql_text
            await conn.execute(sql_text("ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS barcode VARCHAR(64)"))
            await conn.execute(sql_text("ALTER TABLE inventory_counts ADD COLUMN IF NOT EXISTS applied BOOLEAN DEFAULT FALSE"))
        except Exception as e:
            print("barcode migrate:", e)
    await create_owner_if_not_exists()
    print("🚀 CloudVape backend запущен")
    asyncio.create_task(keep_alive_loop())
    yield
    await engine.dispose()


app = FastAPI(title="CloudVape", version="0.1.0", lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.include_router(auth.router, prefix="/api")
app.include_router(stores.router, prefix="/api")
app.include_router(products.router, prefix="/api")
app.include_router(sales.router, prefix="/api")


# ========== Helpers ==========

async def get_current_web_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    return result.scalar_one_or_none()


def login_required(user: User | None):
    if not user:
        return RedirectResponse("/login", status_code=303)
    return None


# ========== Web pages ==========

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.role == "seller":
        return RedirectResponse("/sell", status_code=303)

    stores_count = (await db.execute(select(func.count(Store.id)).where(Store.is_active == True))).scalar() or 0

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sales_q = await db.execute(
        select(func.count(Sale.id), func.coalesce(func.sum(Sale.total_amount), 0))
        .where(Sale.created_at >= today_start)
    )
    row = sales_q.one()
    sales_today = row[0] or 0
    revenue_today = int(row[1] or 0)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "stores_count": stores_count,
        "sales_today": sales_today,
        "revenue_today": revenue_today,
    })


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "user": None})


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.username == username, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "user": None,
            "error": "Неверный логин или пароль",
        })

    request.session["user_id"] = user.id
    # Продавцы сразу на веб-кассу
    if user.role == "seller":
        return RedirectResponse("/sell", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.post("/login/telegram")
async def login_telegram(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    from app.models.login_code import LoginCode
    code = code.strip()
    result = await db.execute(
        select(LoginCode).where(LoginCode.code == code, LoginCode.used == False)
    )
    lc = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    exp = lc.expires_at if lc.expires_at.tzinfo else lc.expires_at.replace(tzinfo=timezone.utc)
    if not lc or exp < now:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "user": None,
            "error": "Код неверный или истёк. Запроси новый в боте (🌐 Вход на сайт).",
        })
    lc.used = True
    user = await db.get(User, lc.user_id)
    if not user or not user.is_active:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "user": None,
            "error": "Пользователь не найден",
        })
    await db.commit()
    request.session["user_id"] = user.id
    if user.role == "seller":
        return RedirectResponse("/sell", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/stores", response_class=HTMLResponse)
async def stores_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    result = await db.execute(select(Store).where(Store.is_active == True).order_by(Store.name))
    stores_list = result.scalars().all()

    return templates.TemplateResponse("stores.html", {
        "request": request,
        "user": user,
        "stores": stores_list,
    })


@app.post("/stores/create")
async def stores_create(
    request: Request,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_web_user(request, db)
    if not user or user.role != "owner":
        return RedirectResponse("/login", status_code=303)

    store = Store(name=name.strip())
    db.add(store)
    await db.commit()
    return RedirectResponse("/stores", status_code=303)


@app.get("/products", response_class=HTMLResponse)
async def products_page(
    request: Request,
    store_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    stores_result = await db.execute(select(Store).where(Store.is_active == True).order_by(Store.name))
    stores_list = stores_result.scalars().all()

    items = []
    if store_id:
        result = await db.execute(
            select(ProductVariant)
            .join(Product)
            .where(Product.store_id == store_id, ProductVariant.is_active == True)
            .options(selectinload(ProductVariant.product).selectinload(Product.category))
            .order_by(Product.name)
        )
        variants = result.scalars().all()
        for v in variants:
            status = "ok"
            if v.stock <= 0:
                status = "out"
            elif v.stock <= v.low_stock_threshold:
                status = "low"
            items.append({
                "variant_id": v.id,
                "category": v.product.category.name if v.product and v.product.category else "",
                "product_name": v.product.name if v.product else "",
                "strength": v.strength,
                "flavor": v.flavor,
                "price": float(v.price),
                "stock": v.stock,
                "status": status,
            })

    return templates.TemplateResponse("products.html", {
        "request": request,
        "user": user,
        "stores": stores_list,
        "store_id": store_id,
        "items": items,
    })


@app.post("/products/import")
async def products_import(
    request: Request,
    store_id: int = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    content = await file.read()
    stats = await import_products_from_excel(db, store_id, user.id, content)
    await db.commit()

    # Можно добавить flash-сообщение через session
    request.session["flash_success"] = (
        f"Загружено. Новых товаров: {stats.get('created_products', 0)}, "
        f"вариантов: {stats.get('created_variants', 0)}, "
        f"обновлено: {stats.get('updated_variants', 0)}"
    )
    return RedirectResponse(f"/products?store_id={store_id}", status_code=303)



@app.get("/sales", response_class=HTMLResponse)
async def sales_page(
    request: Request,
    store_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    stores_result = await db.execute(select(Store).where(Store.is_active == True).order_by(Store.name))
    stores_list = stores_result.scalars().all()

    q = select(Sale).options(selectinload(Sale.items)).order_by(Sale.created_at.desc()).limit(200)

    if store_id:
        q = q.where(Sale.store_id == store_id)

    from datetime import date as date_cls, time as time_cls
    if date_from:
        try:
            d = date_cls.fromisoformat(date_from)
            q = q.where(Sale.created_at >= datetime.combine(d, time_cls.min, tzinfo=timezone.utc))
        except ValueError:
            pass
    if date_to:
        try:
            d = date_cls.fromisoformat(date_to)
            q = q.where(Sale.created_at <= datetime.combine(d, time_cls.max, tzinfo=timezone.utc))
        except ValueError:
            pass

    # если нет дат — сегодня по умолчанию
    if not date_from and not date_to:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        q = q.where(Sale.created_at >= today_start)
        date_from = today_start.strftime("%Y-%m-%d")

    result = await db.execute(q)
    sales_list = result.scalars().all()

    # имена магазинов
    store_map = {s.id: s.name for s in stores_list}
    for s in sales_list:
        s.store_name = store_map.get(s.store_id, "—")

    revenue = sum(float(s.total_amount) for s in sales_list)

    return templates.TemplateResponse("sales.html", {
        "request": request,
        "user": user,
        "stores": stores_list,
        "store_id": store_id,
        "date_from": date_from,
        "date_to": date_to,
        "sales": sales_list,
        "sales_count": len(sales_list),
        "revenue": revenue,
    })


# ========== Sellers ==========

@app.get("/sellers", response_class=HTMLResponse)
async def sellers_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    from app.models.store import StoreUser

    stores_result = await db.execute(select(Store).where(Store.is_active == True).order_by(Store.name))
    stores_list = stores_result.scalars().all()

    # Уже привязанные
    links_result = await db.execute(
        select(StoreUser)
        .options(selectinload(StoreUser.user), selectinload(StoreUser.store))
        .where(StoreUser.is_active == True)
    )
    links = links_result.scalars().all()

    # Пользователи с telegram_id, которые ещё не привязаны ни к одному магазину
    linked_user_ids = {l.user_id for l in links}
    users_result = await db.execute(
        select(User).where(User.telegram_id.isnot(None), User.is_active == True)
    )
    all_tg_users = users_result.scalars().all()
    available_users = [u for u in all_tg_users if u.id not in linked_user_ids]

    success = request.session.pop("flash_success", None)
    return templates.TemplateResponse("sellers.html", {
        "request": request,
        "user": user,
        "stores": stores_list,
        "links": links,
        "available_users": available_users,
        "success": success,
    })


@app.post("/sellers/assign")
async def sellers_assign(
    request: Request,
    user_id: int = Form(...),
    store_id: int = Form(...),
    role: str = Form("seller"),
    db: AsyncSession = Depends(get_db),
):
    current = await get_current_web_user(request, db)
    if not current:
        return RedirectResponse("/login", status_code=303)

    from app.models.store import StoreUser

    # Проверяем, нет ли уже привязки
    existing = await db.execute(
        select(StoreUser).where(StoreUser.user_id == user_id, StoreUser.store_id == store_id)
    )
    if existing.scalar_one_or_none():
        return RedirectResponse("/sellers", status_code=303)

    role = role if role in ("seller", "admin", "warehouse") else "seller"
    # warehouse видит все склады — глобальная роль на User
    u = await db.get(User, user_id)
    if u and role == "warehouse":
        u.role = "warehouse"
    elif u and role == "admin":
        u.role = "admin"
    link = StoreUser(user_id=user_id, store_id=store_id, role=role, is_active=True)
    db.add(link)
    await db.commit()
    return RedirectResponse("/sellers", status_code=303)


@app.post("/sellers/remove")
async def sellers_remove(
    request: Request,
    link_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    current = await get_current_web_user(request, db)
    if not current:
        return RedirectResponse("/login", status_code=303)

    from app.models.store import StoreUser
    result = await db.execute(select(StoreUser).where(StoreUser.id == link_id))
    link = result.scalar_one_or_none()
    if link:
        link.is_active = False
        await db.commit()
    return RedirectResponse("/sellers", status_code=303)


# ========== Manual product create ==========


@app.post("/sellers/set-role")
async def sellers_set_role(
    request: Request,
    link_id: int = Form(...),
    role: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    current = await get_current_web_user(request, db)
    if not current or current.role not in ("owner", "admin"):
        return RedirectResponse("/login", status_code=303)

    role = role if role in ("seller", "admin", "warehouse") else "seller"
    result = await db.execute(select(StoreUser).where(StoreUser.id == link_id))
    link = result.scalar_one_or_none()
    if not link:
        return RedirectResponse("/sellers", status_code=303)

    link.role = role
    u = await db.get(User, link.user_id)
    if u and u.role != "owner":
        # глобальная роль для доступа к разделам сайта
        if role == "warehouse":
            u.role = "warehouse"
        elif role == "admin":
            u.role = "admin"
        else:
            u.role = "seller"
    await db.commit()
    request.session["flash_success"] = "Роль обновлена"
    return RedirectResponse("/sellers", status_code=303)


@app.get("/products/add", response_class=HTMLResponse)
async def product_add_page(
    request: Request,
    store_id: int,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    from app.models.product import Category
    cats = await db.execute(
        select(Category).where(Category.store_id == store_id, Category.is_active == True).order_by(Category.name)
    )
    categories = cats.scalars().all()

    return templates.TemplateResponse("product_edit.html", {
        "request": request,
        "user": user,
        "store_id": store_id,
        "categories": categories,
    })


@app.post("/products/create")
async def product_create(
    request: Request,
    store_id: int = Form(...),
    category_id: str = Form(""),
    new_category: str = Form(""),
    name: str = Form(...),
    strength: str = Form(""),
    flavor: str = Form(""),
    price: float = Form(...),
    stock: int = Form(0),
    barcode: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    from app.models.product import Category, Product, ProductVariant
    from app.models.inventory import StockMovement
    from decimal import Decimal

    # Категория
    cat = None
    if category_id and category_id.isdigit():
        cat = await db.get(Category, int(category_id))
    if not cat and new_category.strip():
        cat = Category(store_id=store_id, name=new_category.strip())
        db.add(cat)
        await db.flush()
    if not cat:
        # fallback
        cat = Category(store_id=store_id, name="Без категории")
        db.add(cat)
        await db.flush()

    # Товар (ищем существующий с таким именем в этой категории)
    prod_result = await db.execute(
        select(Product).where(
            Product.store_id == store_id,
            Product.category_id == cat.id,
            Product.name == name.strip(),
        )
    )
    product = prod_result.scalar_one_or_none()
    if not product:
        product = Product(store_id=store_id, category_id=cat.id, name=name.strip())
        db.add(product)
        await db.flush()

    strength_val = strength.strip() or None
    flavor_val = flavor.strip() or None

    # Вариант
    var_result = await db.execute(
        select(ProductVariant).where(
            ProductVariant.product_id == product.id,
            ProductVariant.strength == strength_val,
            ProductVariant.flavor == flavor_val,
        )
    )
    variant = var_result.scalar_one_or_none()
    if variant:
        variant.price = Decimal(str(price))
        old_stock = variant.stock
        variant.stock = stock
        if stock != old_stock:
            db.add(StockMovement(
                variant_id=variant.id,
                user_id=user.id,
                store_id=store_id,
                movement_type="adjustment",
                quantity_change=stock - old_stock,
                stock_before=old_stock,
                stock_after=stock,
                comment="Ручное редактирование",
            ))
    else:
        variant = ProductVariant(
            product_id=product.id,
            strength=strength_val,
            flavor=flavor_val,
            price=Decimal(str(price)),
            stock=stock,
            barcode=(barcode.strip() or None),
        )
        db.add(variant)
        await db.flush()
        if stock > 0:
            db.add(StockMovement(
                variant_id=variant.id,
                user_id=user.id,
                store_id=store_id,
                movement_type="initial",
                quantity_change=stock,
                stock_before=0,
                stock_after=stock,
                comment="Ручное создание",
            ))

    await db.commit()
    return RedirectResponse(f"/products?store_id={store_id}", status_code=303)


@app.get("/products/template")
async def download_template(store_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Скачать шаблон Excel для загрузки товаров"""
    from openpyxl import Workbook
    from fastapi.responses import StreamingResponse
    from io import BytesIO

    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    wb = Workbook()
    ws = wb.active
    ws.title = "Товары"

    # Заголовки
    headers = ["Категория", "Название", "Характеристика 1", "Характеристика 2", "Цена", "Остаток", "Штрихкод"]
    ws.append(headers)

    # Примеры
    examples = [
        ["Жидкости", "Скала 5", "20 мг", "Mango", 650, 10],
        ["Жидкости", "Скала 5", "20 мг", "Blue Razz", 650, 8],
        ["Одноразки", "Elf Bar 1500", "", "Blue Razz", 890, 25],
        ["Одноразки", "Elf Bar 1500", "", "Mango", 890, 15],
        ["Табак", "Tangiers", "250г", "Cane Mint", 1200, 6],
        ["Табак", "Tangiers", "250г", "Horchata", 1200, 4],
        ["Снюс", "Lyft", "Strong", "Ice Cool", 450, 20],
        ["Устройства", "Voopoo Drag S", "", "", 3200, 3],
        ["Расходники", "Спирали", "0.3 Ом", "Alien", 150, 50],
    ]
    for row in examples:
        ws.append(row)

    # Ширина колонок
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=cloudvape_template.xlsx"},
    )


@app.post("/products/update-variant")
async def update_variant(
    request: Request,
    variant_id: int = Form(...),
    store_id: int = Form(...),
    price: float = Form(...),
    stock: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    from app.models.product import ProductVariant
    from app.models.inventory import StockMovement
    from decimal import Decimal

    variant = await db.get(ProductVariant, variant_id)
    if not variant:
        return RedirectResponse(f"/products?store_id={store_id}", status_code=303)

    old_stock = variant.stock
    variant.price = Decimal(str(price))
    variant.stock = stock

    if stock != old_stock:
        db.add(StockMovement(
            variant_id=variant.id,
            user_id=user.id,
            store_id=store_id,
            movement_type="adjustment",
            quantity_change=stock - old_stock,
            stock_before=old_stock,
            stock_after=stock,
            comment="Ручное изменение через веб",
        ))

    await db.commit()
    return RedirectResponse(f"/products?store_id={store_id}", status_code=303)


# ========== Seller web POS (/sell) ==========

async def get_seller_store(user: User, db: AsyncSession, preferred_store_id: int | None = None):
    if preferred_store_id and user.role == "owner":
        store = await db.get(Store, preferred_store_id)
        if store and store.is_active:
            return store.id, store.name
    result = await db.execute(
        select(StoreUser, Store)
        .join(Store, Store.id == StoreUser.store_id)
        .where(StoreUser.user_id == user.id, StoreUser.is_active == True, Store.is_active == True)
    )
    row = result.first()
    if row:
        return row.StoreUser.store_id, row.Store.name
    if user.role == "owner":
        r = await db.execute(select(Store).where(Store.is_active == True).order_by(Store.id).limit(1))
        store = r.scalar_one_or_none()
        if store:
            return store.id, store.name
    return None, None


async def ensure_barcode_column(conn):
    """Добавляем колонку barcode если её ещё нет"""
    from sqlalchemy import text as sql_text
    try:
        await conn.execute(sql_text(
            "ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS barcode VARCHAR(64)"
        ))
    except Exception:
        pass


@app.post("/sellers/create")
async def sellers_create(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    store_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    current = await get_current_web_user(request, db)
    if not current or current.role not in ("owner", "admin"):
        return RedirectResponse("/login", status_code=303)
    username = username.strip().lower()
    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none():
        return RedirectResponse("/sellers", status_code=303)
    user = User(
        full_name=full_name.strip(),
        username=username,
        password_hash=get_password_hash(password),
        role="seller",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    link = StoreUser(user_id=user.id, store_id=store_id, role="seller", is_active=True)
    db.add(link)
    await db.commit()
    return RedirectResponse("/sellers", status_code=303)


@app.get("/sell", response_class=HTMLResponse)
async def sell_page(
    request: Request,
    q: str | None = None,
    category_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    success = None
    error = request.session.pop("flash_error", None)
    ok = request.query_params.get("ok")
    check = request.query_params.get("check")
    if ok and check:
        success = f"✅ Чек №{check} · {ok} ₽"

    sid, store_name = await get_seller_store(user, db)
    cart = request.session.get("cart") or []
    cart_total = sum(float(i.get("price", 0)) * int(i.get("qty", 0)) for i in cart)

    # сумма смены
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    shift_q = await db.execute(
        select(func.coalesce(func.sum(Sale.total_amount), 0))
        .where(Sale.seller_id == user.id, Sale.created_at >= today_start)
    )
    shift_total = float(shift_q.scalar() or 0)

    if not sid:
        return templates.TemplateResponse("sell.html", {
            "request": request, "user": user, "store_name": "—",
            "categories": [], "products": [], "cart": cart, "cart_total": cart_total,
            "shift_total": shift_total, "q": q or "", "category_id": None,
            "error": error or "Нет магазина. Создай магазин и привяжи продавца.",
            "success": success,
        })

    # категории
    cats = (await db.execute(
        select(Category).where(Category.store_id == sid, Category.is_active == True)
        .order_by(Category.sort_order, Category.name)
    )).scalars().all()

    # товары в наличии
    vq = (
        select(ProductVariant)
        .join(Product)
        .where(Product.store_id == sid, ProductVariant.is_active == True, Product.is_active == True, ProductVariant.stock > 0)
        .options(selectinload(ProductVariant.product).selectinload(Product.category))
        .order_by(Product.name)
        .limit(200)
    )
    if category_id:
        vq = vq.where(Product.category_id == category_id)
    if q and q.strip():
        qq = f"%{q.strip()}%"
        from sqlalchemy import or_
        vq = vq.where(or_(
            Product.name.ilike(qq),
            ProductVariant.flavor.ilike(qq),
            ProductVariant.strength.ilike(qq),
            ProductVariant.barcode == q.strip(),
        ))

    variants = (await db.execute(vq)).scalars().all()
    products = []
    for v in variants:
        products.append({
            "variant_id": v.id,
            "product_name": v.product.name if v.product else "?",
            "strength": v.strength,
            "flavor": v.flavor,
            "price": float(v.price),
            "stock": v.stock,
            "barcode": v.barcode,
        })

    # если точный штрихкод — сразу в корзину
    if q and q.strip() and len(products) == 1 and products[0].get("barcode") == q.strip():
        item = products[0]
        cart.append({
            "variant_id": item["variant_id"],
            "name": f"{item['product_name']}" + (f" ({item['flavor'] or item['strength'] or ''})" if (item['flavor'] or item['strength']) else ""),
            "qty": 1,
            "price": item["price"],
        })
        request.session["cart"] = cart
        return RedirectResponse("/sell", status_code=303)

    return templates.TemplateResponse("sell.html", {
        "request": request,
        "user": user,
        "store_name": store_name,
        "categories": cats,
        "products": products,
        "cart": cart,
        "cart_total": cart_total,
        "shift_total": shift_total,
        "q": q or "",
        "category_id": category_id,
        "success": success,
        "error": error,
    })


@app.post("/sell/add")
async def sell_add_to_cart(
    request: Request,
    variant_id: int = Form(...),
    qty: int = Form(1),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    variant = await db.get(ProductVariant, variant_id)
    if not variant or qty < 1:
        return RedirectResponse("/sell", status_code=303)
    await db.refresh(variant, ["product"])
    parts = [p for p in [variant.strength, variant.flavor] if p]
    name = variant.product.name if variant.product else "Товар"
    if parts:
        name = f"{name} ({' / '.join(parts)})"
    cart = request.session.get("cart") or []
    # если уже в корзине — увеличим qty
    found = False
    for item in cart:
        if item["variant_id"] == variant_id:
            item["qty"] = int(item["qty"]) + int(qty)
            found = True
            break
    if not found:
        cart.append({"variant_id": variant_id, "name": name, "qty": int(qty), "price": float(variant.price)})
    request.session["cart"] = cart
    return RedirectResponse("/sell", status_code=303)


@app.post("/sell/cart-qty")
async def sell_cart_qty(request: Request, index: int = Form(...), action: str = Form(...)):
    cart = request.session.get("cart") or []
    if 0 <= index < len(cart):
        if action == "plus":
            cart[index]["qty"] = int(cart[index]["qty"]) + 1
        elif action == "minus":
            cart[index]["qty"] = int(cart[index]["qty"]) - 1
            if cart[index]["qty"] <= 0:
                cart.pop(index)
    request.session["cart"] = cart
    return RedirectResponse("/sell", status_code=303)


@app.post("/sell/cart-remove")
async def sell_cart_remove(request: Request, index: int = Form(...)):
    cart = request.session.get("cart") or []
    if 0 <= index < len(cart):
        cart.pop(index)
    request.session["cart"] = cart
    return RedirectResponse("/sell", status_code=303)


@app.post("/sell/checkout")
async def sell_checkout(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    sid, store_name = await get_seller_store(user, db)
    cart = request.session.get("cart") or []
    if not sid or not cart:
        return RedirectResponse("/sell", status_code=303)
    from app.services.inventory import create_sale, InsufficientStockError
    items = [{"variant_id": i["variant_id"], "quantity": int(i["qty"])} for i in cart]
    try:
        sale = await create_sale(session=db, store_id=sid, seller_id=user.id, items=items)
        await db.commit()
        request.session["cart"] = []
        request.session["last_sale_id"] = sale.id
        # low stock alerts
        try:
            from app.services.notify import notify_low_stock
            owners = (await db.execute(select(User).where(User.role == "owner", User.telegram_id.isnot(None)))).scalars().all()
            for it in items:
                v = await db.get(ProductVariant, it["variant_id"])
                if v and v.stock <= v.low_stock_threshold:
                    await db.refresh(v, ["product"])
                    label = (v.product.name if v.product else "") + " " + " ".join(filter(None, [v.strength, v.flavor]))
                    for o in owners:
                        await notify_low_stock(o.telegram_id, store_name or "", label.strip(), v.stock)
        except Exception as ex:
            print("low stock notify:", ex)
        return RedirectResponse(f"/sell?ok={int(sale.total_amount)}&check={sale.id}", status_code=303)
    except InsufficientStockError as e:
        await db.rollback()
        # show error via redirect query is messy — use session flash
        request.session["flash_error"] = f"Недостаточно «{e.variant_name}». Доступно: {e.available}"
        return RedirectResponse("/sell", status_code=303)
    except Exception as e:
        await db.rollback()
        request.session["flash_error"] = str(e)
        return RedirectResponse("/sell", status_code=303)


@app.post("/sell/clear-cart")
async def sell_clear_cart(request: Request):
    request.session["cart"] = []
    return RedirectResponse("/sell", status_code=303)


@app.get("/sell/shift", response_class=HTMLResponse)
async def sell_shift(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(Sale)
        .where(Sale.seller_id == user.id, Sale.created_at >= today_start)
        .order_by(Sale.created_at.desc())
    )
    sales_list = result.scalars().all()
    total = sum(float(s.total_amount) for s in sales_list)

    return templates.TemplateResponse("sell_shift.html", {
        "request": request,
        "user": user,
        "sales": sales_list,
        "count": len(sales_list),
        "total": total,
    })



@app.get("/inventory", response_class=HTMLResponse)
async def inventory_admin_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.role not in ("owner", "admin"):
        return RedirectResponse("/inventory/my", status_code=303)

    from app.models.inventory_task import InventoryTask, InventoryCount, InventoryLine
    from app.models.product import Category

    stores = (await db.execute(select(Store).where(Store.is_active == True).order_by(Store.name))).scalars().all()
    store_map = {s.id: s.name for s in stores}

    all_cats = (await db.execute(select(Category).where(Category.is_active == True).order_by(Category.name))).scalars().all()
    cat_map = {c.id: c.name for c in all_cats}
    all_categories = [type("C", (), {"id": c.id, "name": c.name, "store_name": store_map.get(c.store_id, "")})() for c in all_cats]

    def cats_label(task):
        if not task.category_ids:
            return "все группы"
        ids = [int(x) for x in task.category_ids.split(",") if x.strip().isdigit()]
        names = [cat_map[i] for i in ids if i in cat_map]
        return ", ".join(names) if names else "все группы"

    tasks = (await db.execute(select(InventoryTask).order_by(InventoryTask.created_at.desc()).limit(30))).scalars().all()
    for task in tasks:
        task.store_name = store_map.get(task.store_id, "—")
        task.cats_label = cats_label(task)

    # history of completed counts
    counts = (await db.execute(
        select(InventoryCount)
        .where(InventoryCount.status == "done")
        .order_by(InventoryCount.finished_at.desc().nullslast(), InventoryCount.id.desc())
        .limit(50)
    )).scalars().all()

    history = []
    for c in counts:
        await db.refresh(c, ["lines"])
        task = await db.get(InventoryTask, c.task_id)
        seller = await db.get(User, c.user_id)
        diff_count = sum(1 for L in c.lines if L.fact_stock is not None and L.fact_stock != L.system_stock)
        history.append(type("H", (), {
            "id": c.id,
            "finished_at": c.finished_at,
            "count_date": c.count_date,
            "period": f"{task.date_from.strftime('%d.%m.%y')}–{task.date_to.strftime('%d.%m.%y')}" if task else "—",
            "store_name": store_map.get(c.store_id, "—"),
            "cats_label": cats_label(task) if task else "—",
            "seller_name": (seller.full_name or seller.username) if seller else "—",
            "diff_count": diff_count,
        })())

    success = request.session.pop("flash_success", None)
    return templates.TemplateResponse("inventory_admin.html", {
        "request": request, "user": user, "stores": stores,
        "tasks": tasks, "all_categories": all_categories, "history": history,
        "success": success,
    })


@app.post("/inventory/create")
async def inventory_create(
    request: Request,
    title: str = Form("Инвентаризация"),
    store_id: int = Form(...),
    date_from: str = Form(...),
    date_to: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_web_user(request, db)
    if not user or user.role not in ("owner", "admin"):
        return RedirectResponse("/login", status_code=303)

    from app.models.inventory_task import InventoryTask
    from datetime import date as date_cls

    form = await request.form()
    cat_ids = form.getlist("category_ids")
    cat_str = ",".join(cat_ids) if cat_ids else None

    task = InventoryTask(
        store_id=store_id,
        title=title.strip() or "Инвентаризация",
        date_from=date_cls.fromisoformat(date_from),
        date_to=date_cls.fromisoformat(date_to),
        category_ids=cat_str,
        created_by=user.id,
        is_active=True,
    )
    db.add(task)
    await db.commit()
    return RedirectResponse("/inventory", status_code=303)


@app.get("/inventory/report/{count_id}", response_class=HTMLResponse)
async def inventory_report(request: Request, count_id: int, db: AsyncSession = Depends(get_db)):
    user = await get_current_web_user(request, db)
    if not user or user.role not in ("owner", "admin"):
        return RedirectResponse("/login", status_code=303)

    from app.models.inventory_task import InventoryCount, InventoryTask
    from app.models.product import Category

    count = await db.get(InventoryCount, count_id)
    if not count:
        return RedirectResponse("/inventory", status_code=303)
    await db.refresh(count, ["lines"])
    task = await db.get(InventoryTask, count.task_id)
    store = await db.get(Store, count.store_id)
    seller = await db.get(User, count.user_id)

    cats_label = "все группы"
    period = "—"
    if task:
        period = f"{task.date_from.strftime('%d.%m.%y')}–{task.date_to.strftime('%d.%m.%y')}"
        if task.category_ids:
            ids = [int(x) for x in task.category_ids.split(",") if x.strip().isdigit()]
            if ids:
                cr = await db.execute(select(Category).where(Category.id.in_(ids)))
                cats_label = ", ".join(c.name for c in cr.scalars().all()) or cats_label

    diffs = [L for L in count.lines if L.fact_stock is not None and L.fact_stock != L.system_stock]
    success = request.session.pop("flash_success", None)

    return templates.TemplateResponse("inventory_report.html", {
        "request": request, "user": user, "count": count,
        "period": period, "store_name": store.name if store else "—",
        "cats_label": cats_label,
        "seller_name": (seller.full_name or seller.username) if seller else "—",
        "diffs": diffs, "success": success,
    })


@app.post("/inventory/apply/{count_id}")
async def inventory_apply(request: Request, count_id: int, db: AsyncSession = Depends(get_db)):
    """Только owner/admin применяет остатки по факту"""
    user = await get_current_web_user(request, db)
    if not user or user.role not in ("owner", "admin"):
        return RedirectResponse("/login", status_code=303)

    from app.models.inventory_task import InventoryCount
    from app.models.inventory import StockMovement

    count = await db.get(InventoryCount, count_id)
    if not count or count.status != "done":
        return RedirectResponse("/inventory", status_code=303)
    if getattr(count, "applied", False):
        request.session["flash_success"] = "Уже применено ранее"
        return RedirectResponse(f"/inventory/report/{count_id}", status_code=303)

    await db.refresh(count, ["lines"])
    for line in count.lines:
        if line.fact_stock is None:
            continue
        variant = await db.get(ProductVariant, line.variant_id)
        if not variant:
            continue
        old = variant.stock
        if line.fact_stock == old:
            continue
        variant.stock = line.fact_stock
        db.add(StockMovement(
            variant_id=variant.id,
            user_id=user.id,
            store_id=count.store_id,
            movement_type="inventory",
            quantity_change=line.fact_stock - old,
            stock_before=old,
            stock_after=line.fact_stock,
            comment=f"Применение инвентаризации #{count.id} админом",
        ))
    count.applied = True
    await db.commit()
    request.session["flash_success"] = "Остатки обновлены по факту инвентаризации"
    return RedirectResponse(f"/inventory/report/{count_id}", status_code=303)



@app.get("/warehouse", response_class=HTMLResponse)
async def warehouse_page(
    request: Request,
    store_id: int | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.role not in ("owner", "admin", "warehouse"):
        return RedirectResponse("/", status_code=303)

    stores = (await db.execute(select(Store).where(Store.is_active == True).order_by(Store.name))).scalars().all()
    store_map = {s.id: s.name for s in stores}

    vq = (
        select(ProductVariant)
        .join(Product)
        .where(ProductVariant.is_active == True, Product.is_active == True)
        .options(selectinload(ProductVariant.product).selectinload(Product.category))
        .order_by(Product.store_id, Product.name)
        .limit(500)
    )
    if store_id:
        vq = vq.where(Product.store_id == store_id)
    if q and q.strip():
        from sqlalchemy import or_
        qq = f"%{q.strip()}%"
        vq = vq.where(or_(
            Product.name.ilike(qq),
            ProductVariant.barcode == q.strip(),
            ProductVariant.flavor.ilike(qq),
        ))

    variants = (await db.execute(vq)).scalars().all()
    items = []
    for v in variants:
        parts = [p for p in [v.strength, v.flavor] if p]
        items.append({
            "store_name": store_map.get(v.product.store_id if v.product else 0, "—"),
            "category": v.product.category.name if v.product and v.product.category else "",
            "product_name": v.product.name if v.product else "?",
            "variant_info": " / ".join(parts) if parts else None,
            "stock": v.stock,
            "price": float(v.price),
        })

    return templates.TemplateResponse("warehouse.html", {
        "request": request, "user": user, "stores": stores,
        "store_id": store_id, "q": q or "", "items": items,
    })


# ========== Inventory seller flow ==========

@app.get("/inventory/my", response_class=HTMLResponse)
async def inventory_seller_page(request: Request, db: AsyncSession = Depends(get_db)):
    from datetime import date as date_cls
    from app.models.inventory_task import InventoryTask, InventoryCount, InventoryLine

    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    sid, store_name = await get_seller_store(user, db)
    today = date_cls.today()
    error = request.session.pop("flash_error", None)
    success = request.session.pop("flash_success", None)

    tasks_out = []
    active_count = None

    if sid:
        tasks = (await db.execute(
            select(InventoryTask).where(
                InventoryTask.store_id == sid,
                InventoryTask.is_active == True,
                InventoryTask.date_from <= today,
                InventoryTask.date_to >= today,
            ).order_by(InventoryTask.date_from)
        )).scalars().all()

        for task in tasks:
            done = (await db.execute(
                select(InventoryCount).where(
                    InventoryCount.task_id == task.id,
                    InventoryCount.user_id == user.id,
                    InventoryCount.count_date == today,
                    InventoryCount.status == "done",
                )
            )).scalar_one_or_none()
            in_prog = (await db.execute(
                select(InventoryCount).where(
                    InventoryCount.task_id == task.id,
                    InventoryCount.user_id == user.id,
                    InventoryCount.count_date == today,
                    InventoryCount.status == "in_progress",
                )
            )).scalar_one_or_none()

            task.store_name = store_name
            task.done_today = done is not None
            task.can_start = done is None and in_prog is None
            tasks_out.append(task)

            if in_prog and not active_count:
                await db.refresh(in_prog, ["lines"])
                in_prog.task_title = task.title
                active_count = in_prog

    return templates.TemplateResponse("inventory_seller.html", {
        "request": request, "user": user, "tasks": tasks_out,
        "active_count": active_count, "error": error, "success": success,
    })


@app.post("/inventory/start")
async def inventory_start(request: Request, task_id: int = Form(...), db: AsyncSession = Depends(get_db)):
    from datetime import date as date_cls
    from app.models.inventory_task import InventoryTask, InventoryCount, InventoryLine

    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    sid, _ = await get_seller_store(user, db)
    task = await db.get(InventoryTask, task_id)
    today = date_cls.today()
    if not task or not sid or task.store_id != sid or not task.is_active:
        request.session["flash_error"] = "Задание недоступно"
        return RedirectResponse("/inventory/my", status_code=303)
    if not (task.date_from <= today <= task.date_to):
        request.session["flash_error"] = "Вне периода инвентаризации"
        return RedirectResponse("/inventory/my", status_code=303)

    existing = (await db.execute(
        select(InventoryCount).where(
            InventoryCount.task_id == task_id,
            InventoryCount.user_id == user.id,
            InventoryCount.count_date == today,
        )
    )).scalar_one_or_none()
    if existing:
        request.session["flash_error"] = "Сегодня уже есть инвентаризация (лимит 1 раз в сутки)"
        return RedirectResponse("/inventory/my", status_code=303)

    # variants for categories
    vq = (
        select(ProductVariant)
        .join(Product)
        .where(Product.store_id == sid, Product.is_active == True, ProductVariant.is_active == True)
        .options(selectinload(ProductVariant.product))
    )
    if task.category_ids:
        ids = [int(x) for x in task.category_ids.split(",") if x.strip().isdigit()]
        if ids:
            vq = vq.where(Product.category_id.in_(ids))
    variants = (await db.execute(vq)).scalars().all()

    count = InventoryCount(
        task_id=task_id, store_id=sid, user_id=user.id,
        count_date=today, status="in_progress",
    )
    db.add(count)
    await db.flush()
    for v in variants:
        parts = [p for p in [v.strength, v.flavor] if p]
        db.add(InventoryLine(
            count_id=count.id,
            variant_id=v.id,
            system_stock=v.stock,
            fact_stock=None,
            product_name=v.product.name if v.product else "?",
            variant_info=" / ".join(parts) if parts else None,
        ))
    await db.commit()
    return RedirectResponse("/inventory/my", status_code=303)


@app.post("/inventory/submit")
async def inventory_submit(
    request: Request,
    count_id: int = Form(...),
    action: str = Form("save"),
    db: AsyncSession = Depends(get_db),
):
    """Продавец только фиксирует факт. Остатки НЕ меняются."""
    from app.models.inventory_task import InventoryCount, InventoryLine, InventoryTask
    from app.services.notify import send_telegram_message

    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    count = await db.get(InventoryCount, count_id)
    if not count or count.user_id != user.id or count.status == "done":
        return RedirectResponse("/inventory/my", status_code=303)

    form = await request.form()
    await db.refresh(count, ["lines"])
    for line in count.lines:
        key = f"fact_{line.id}"
        if key in form:
            try:
                line.fact_stock = int(form[key])
            except (TypeError, ValueError):
                pass

    if action == "finish":
        count.status = "done"
        count.finished_at = datetime.now(timezone.utc)
        await db.commit()
        request.session["flash_success"] = "Инвентаризация сдана. Остатки не изменены — ждёт проверки админа."

        # Отчёт админам/владельцу в Telegram
        task = await db.get(InventoryTask, count.task_id)
        store = await db.get(Store, count.store_id)
        diffs = []
        for line in count.lines:
            if line.fact_stock is None:
                continue
            if line.fact_stock != line.system_stock:
                diffs.append(
                    f"• {line.product_name} {line.variant_info or ''}\n"
                    f"  план {line.system_stock} → факт {line.fact_stock} (Δ {line.fact_stock - line.system_stock})"
                )
        period = ""
        cats = "все категории"
        if task:
            period = f"{task.date_from.strftime('%d.%m.%y')}–{task.date_to.strftime('%d.%m.%y')}"
            if task.category_ids:
                from app.models.product import Category
                ids = [int(x) for x in task.category_ids.split(",") if x.strip().isdigit()]
                if ids:
                    cr = await db.execute(select(Category).where(Category.id.in_(ids)))
                    cats = ", ".join(c.name for c in cr.scalars().all()) or cats

        report = (
            f"📋 <b>Инвентаризация сдана</b>\n"
            f"Период: {period}\n"
            f"Магазин: {store.name if store else '—'}\n"
            f"Группы: {cats}\n"
            f"Продавец: {user.full_name or user.username}\n\n"
        )
        if diffs:
            report += f"<b>Расхождения ({len(diffs)}):</b>\n" + "\n".join(diffs[:30])
            if len(diffs) > 30:
                report += f"\n… ещё {len(diffs) - 30}"
        else:
            report += "✅ Расхождений нет"

        recipients = (await db.execute(
            select(User).where(User.role.in_(["owner", "admin"]), User.telegram_id.isnot(None))
        )).scalars().all()
        for o in recipients:
            await send_telegram_message(o.telegram_id, report)
    else:
        await db.commit()
        request.session["flash_success"] = "Черновик сохранён"
    return RedirectResponse("/inventory/my", status_code=303)



@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_web_user(request, db)
    if not user or user.role != "owner":
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("settings.html", {"request": request, "user": user})


@app.post("/settings/telegram")
async def settings_telegram(
    request: Request,
    telegram_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_web_user(request, db)
    if not user or user.role != "owner":
        return RedirectResponse("/", status_code=303)
    try:
        tid = int(telegram_id.strip())
    except ValueError:
        return RedirectResponse("/settings", status_code=303)
    user.telegram_id = tid
    await db.commit()
    return RedirectResponse("/settings", status_code=303)

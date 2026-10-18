from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
import os

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import engine, Base, AsyncSessionLocal, get_db
from app.core.config import get_settings
from app.core.security import get_password_hash, verify_password
from app.models import User, Store, Sale
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await create_owner_if_not_exists()
    print("🚀 CloudVape backend запущен")
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
async def sales_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_web_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(Sale)
        .where(Sale.created_at >= today_start)
        .options(selectinload(Sale.items))
        .order_by(Sale.created_at.desc())
        .limit(50)
    )
    sales_list = result.scalars().all()

    return templates.TemplateResponse("sales.html", {
        "request": request,
        "user": user,
        "sales": sales_list,
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

    return templates.TemplateResponse("sellers.html", {
        "request": request,
        "user": user,
        "stores": stores_list,
        "links": links,
        "available_users": available_users,
    })


@app.post("/sellers/assign")
async def sellers_assign(
    request: Request,
    user_id: int = Form(...),
    store_id: int = Form(...),
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

    link = StoreUser(user_id=user_id, store_id=store_id, role="seller", is_active=True)
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

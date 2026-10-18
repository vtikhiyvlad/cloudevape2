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

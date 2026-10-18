from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Numeric, Integer, Text, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("store_id", "name", name="uq_store_category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # Жидкости, Одноразки...
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    store: Mapped["Store"] = relationship("Store", back_populates="categories")
    products: Mapped[list["Product"]] = relationship("Product", back_populates="category")


class Product(Base):
    """Товар (например Nasty Juice Mango)"""
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    store: Mapped["Store"] = relationship("Store", back_populates="products")
    category: Mapped["Category | None"] = relationship("Category", back_populates="products")
    variants: Mapped[list["ProductVariant"]] = relationship(
        "ProductVariant", back_populates="product", cascade="all, delete-orphan"
    )


class ProductVariant(Base):
    """Конкретный вариант товара: крепость + вкус + цена + остаток"""
    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint("product_id", "strength", "flavor", name="uq_variant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    
    # Параметры варианта (для жидкостей)
    strength: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 3 мг, 6 мг, 20 мг...
    flavor: Mapped[str | None] = mapped_column(String(100), nullable=True)   # Mango, Blue Razz...
    
    # Дополнительные атрибуты (можно использовать гибко)
    attr1: Mapped[str | None] = mapped_column(String(100), nullable=True)  # объём, цвет и т.д.
    attr2: Mapped[str | None] = mapped_column(String(100), nullable=True)

    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=3)

    sku: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product: Mapped["Product"] = relationship("Product", back_populates="variants")
    movements: Mapped[list["StockMovement"]] = relationship("StockMovement", back_populates="variant")

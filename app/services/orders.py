"""Order operations: placing, paying and cancelling purchases."""
from datetime import datetime
from typing import Dict

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Book, Member, MemberTier, Order, OrderItem, OrderStatus
from app.schemas import OrderCreate
from app.services.members import ensure_can_access_restricted

# Percentage discount granted by each membership tier.
TIER_DISCOUNT_PERCENT: Dict[str, int] = {
    MemberTier.APPRENTICE.value: 0,
    MemberTier.ADEPT.value: 5,
    MemberTier.MASTER.value: 10,
    MemberTier.SUPREME.value: 15,
}

# Extra discount when the total quantity across all items reaches the threshold.
BULK_QUANTITY_THRESHOLD = 10
BULK_DISCOUNT_PERCENT = 5


def _is_sqlite(db: Session) -> bool:
    return db.get_bind().dialect.name == "sqlite"


def _begin_order_transaction(db: Session) -> None:
    """Reserve SQLite's single writer before stock is read.

    PostgreSQL and similar databases use row locks in ``_load_order_books``.
    SQLite ignores ``SELECT FOR UPDATE``, so ``BEGIN IMMEDIATE`` serializes the
    short stock-check/reservation transaction instead. Its normal busy timeout
    lets a second order wait, then observe the committed stock level.
    """
    if _is_sqlite(db):
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _load_order_books(db: Session, data: OrderCreate) -> list[Book]:
    """Load every requested book, locking rows where the database supports it."""
    book_ids = [item.book_id for item in data.items]
    query = select(Book).where(Book.id.in_(book_ids)).order_by(Book.id)
    if not _is_sqlite(db):
        query = query.with_for_update()

    books_by_id = {book.id: book for book in db.scalars(query)}
    books: list[Book] = []
    for book_id in book_ids:
        book = books_by_id.get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        books.append(book)
    return books


def _reserve_stock(db: Session, data: OrderCreate) -> None:
    """Atomically decrement every requested book or fail the transaction."""
    # A stable lock order avoids deadlocks on databases with row-level locking.
    items = sorted(data.items, key=lambda item: item.book_id)
    for item in items:
        result = db.execute(
            update(Book)
            .where(Book.id == item.book_id, Book.stock >= item.quantity)
            .values(stock=Book.stock - item.quantity)
        )
        if result.rowcount != 1:
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for book {item.book_id}",
            )


def calculate_discount_percent(member: Member, total_quantity: int) -> int:
    """Tier discount, plus the bulk discount when total quantity >= threshold."""
    discount_percent = TIER_DISCOUNT_PERCENT[member.tier]
    if total_quantity >= BULK_QUANTITY_THRESHOLD:
        discount_percent += BULK_DISCOUNT_PERCENT
    return discount_percent


def create_order(db: Session, data: OrderCreate, now: datetime) -> Order:
    """Place a pending order and reserve stock.

    Checks, in order (422 for empty items / bad quantity / duplicate books is done by the schema):
    1. 404 member not found; 404 any book not found
    2. 403 any book restricted and member tier below master
    3. 409 any book has insufficient stock (all-or-nothing: nothing is changed)
    Then stock is decremented for every item and prices are snapshotted.
    Pricing: discount_cents = subtotal * percent // 100; total = subtotal - discount.
    """
    try:
        _begin_order_transaction(db)

        member = db.get(Member, data.member_id)
        if member is None:
            raise HTTPException(status_code=404, detail="Member not found")

        books = _load_order_books(db, data)
        if any(book.restricted for book in books):
            ensure_can_access_restricted(member)

        for item, book in zip(data.items, books):
            if book.stock < item.quantity:
                raise HTTPException(status_code=409, detail=f"Insufficient stock for book {book.id}")

        total_quantity = sum(item.quantity for item in data.items)
        subtotal_cents = sum(
            book.price_cents * item.quantity for item, book in zip(data.items, books)
        )
        discount_percent = calculate_discount_percent(member, total_quantity)
        discount_cents = subtotal_cents * discount_percent // 100

        order = Order(
            member_id=member.id,
            status=OrderStatus.PENDING.value,
            subtotal_cents=subtotal_cents,
            discount_percent=discount_percent,
            discount_cents=discount_cents,
            total_cents=subtotal_cents - discount_cents,
            created_at=now,
            items=[
                OrderItem(
                    book_id=book.id,
                    quantity=item.quantity,
                    unit_price_cents=book.price_cents,
                )
                for item, book in zip(data.items, books)
            ],
        )

        _reserve_stock(db, data)
        db.add(order)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise

    db.refresh(order)
    return order


def get_order(db: Session, order_id: int) -> Order:
    """Return an order by id, or raise 404."""
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def pay_order(db: Session, order_id: int) -> Order:
    """Mark a pending order as paid. 404 if missing; 409 if not pending."""
    order = get_order(db, order_id)
    if order.status != OrderStatus.PENDING.value:
        raise HTTPException(status_code=409, detail=f"Cannot pay an order that is {order.status}")
    order.status = OrderStatus.PAID.value
    db.commit()
    db.refresh(order)
    return order


def cancel_order(db: Session, order_id: int) -> Order:
    """Cancel a pending order and restore the reserved stock. 404 if missing; 409 if not pending."""
    order = get_order(db, order_id)
    if order.status != OrderStatus.PENDING.value:
        raise HTTPException(status_code=409, detail=f"Cannot cancel an order that is {order.status}")

    try:
        for item in order.items:
            item.book.stock += item.quantity
        order.status = OrderStatus.CANCELLED.value
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    db.refresh(order)
    return order

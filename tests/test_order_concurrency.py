from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Book, Member, MemberTier, Order
from app.schemas import OrderCreate
from app.services.orders import create_order


def test_only_one_concurrent_order_claims_the_last_copy(tmp_path):
    """Two real SQLite connections must not both reserve stock=1."""
    database_path = tmp_path / "order-race.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with SessionLocal() as db:
        member = Member(
            name="Concurrent Member",
            email="concurrent@example.com",
            tier=MemberTier.APPRENTICE.value,
            created_at=datetime(2026, 1, 1, 12, 0, 0),
        )
        book = Book(
            title="The Last Copy",
            author="Test Author",
            isbn="9780000000019",
            price_cents=1000,
            stock=1,
            restricted=False,
        )
        db.add_all([member, book])
        db.commit()
        member_id = member.id
        book_id = book.id

    requests_ready = Barrier(2)

    def place_order():
        with SessionLocal() as db:
            requests_ready.wait(timeout=5)
            try:
                order = create_order(
                    db,
                    OrderCreate(
                        member_id=member_id,
                        items=[{"book_id": book_id, "quantity": 1}],
                    ),
                    datetime(2026, 1, 1, 12, 0, 0),
                )
                return "created", order.id
            except HTTPException as exc:
                return "rejected", exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: place_order(), range(2)))

    assert sorted(results) == [("created", 1), ("rejected", 409)]

    with SessionLocal() as db:
        assert db.get(Book, book_id).stock == 0
        assert db.scalar(select(func.count()).select_from(Order)) == 1

    engine.dispose()

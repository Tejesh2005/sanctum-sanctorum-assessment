import pytest


@pytest.mark.parametrize(
    "query, matching_title",
    [("%", "100% Reliable"), ("_", "Under_score")],
)
def test_catalog_search_treats_sql_wildcards_as_literal(
    client, make_book, query, matching_title
):
    matching = make_book(title=matching_title)
    make_book(title="Ordinary Title")

    response = client.get("/books", params={"q": query})

    assert response.status_code == 200
    assert [book["id"] for book in response.json()["items"]] == [matching["id"]]


def test_book_patch_ignores_unknown_fields(client, make_book):
    book = make_book()

    response = client.patch(
        f"/books/{book['id']}",
        json={"description": "This field is outside the API contract."},
    )

    assert response.status_code == 200
    assert response.json() == book


def test_late_fee_cap_uses_book_price_at_return_time(client, clock, make_member, make_book):
    member = make_member()
    book = make_book(price_cents=1000)
    loan = client.post(
        "/loans",
        json={"member_id": member["id"], "book_id": book["id"]},
    ).json()

    client.patch(f"/books/{book['id']}", json={"price_cents": 100})
    clock.advance(days=20)
    response = client.post(f"/loans/{loan['id']}/return")

    assert response.status_code == 200
    # Six late days would cost 150 cents, but the current book price caps it at 100.
    assert response.json()["late_fee_cents"] == 100

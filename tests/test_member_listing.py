import pytest


class TestListMembers:
    def test_empty_store(self, client):
        response = client.get("/members")

        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}

    def test_lists_members_by_id(self, client, make_member):
        first = make_member(name="First")
        second = make_member(name="Second")

        response = client.get("/members")

        assert response.status_code == 200
        body = response.json()
        assert [member["id"] for member in body["items"]] == [first["id"], second["id"]]
        assert body["total"] == 2

    def test_paginates_without_changing_total(self, client, make_member):
        members = [make_member() for _ in range(4)]

        response = client.get("/members", params={"limit": 2, "offset": 1})

        assert response.status_code == 200
        body = response.json()
        assert [member["id"] for member in body["items"]] == [members[1]["id"], members[2]["id"]]
        assert body == {
            "items": body["items"],
            "total": 4,
            "limit": 2,
            "offset": 1,
        }

    def test_offset_past_end_returns_empty_page(self, client, make_member):
        make_member()

        response = client.get("/members", params={"offset": 10})

        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 1, "limit": 20, "offset": 10}

    @pytest.mark.parametrize("limit", [1, 100])
    def test_limit_bounds_are_accepted(self, client, limit):
        assert client.get("/members", params={"limit": limit}).status_code == 200

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
    def test_invalid_pagination_returns_422(self, client, params):
        assert client.get("/members", params=params).status_code == 422

from app import db
from app.models.changelog import Changelog


def auth(client, staff_id, password="password123"):
    response = client.post("/auth/login", json={"staff_id": staff_id, "password": password})
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def add_entry(title, published):
    entry = Changelog(version="1.0.0", title=title, description="details", is_published=published)
    db.session.add(entry)
    db.session.commit()
    return entry


class TestReading:
    def test_public_list_only_shows_published(self, client):
        add_entry("Live", True)
        add_entry("Draft", False)

        titles = [c["title"] for c in client.get("/changelog/").get_json()]
        assert titles == ["Live"]

    def test_unpublished_entry_is_hidden_by_id(self, client):
        draft = add_entry("Draft", False)
        assert client.get(f"/changelog/{draft.id}").status_code == 404

    def test_drafts_are_not_readable_without_logging_in(self, client):
        add_entry("Draft", False)
        response = client.get("/changelog/?include_unpublished=true")
        assert response.status_code in (401, 403)
        assert "Draft" not in response.get_data(as_text=True)

    def test_drafts_are_not_readable_by_staff(self, client, test_user):
        add_entry("Draft", False)
        response = client.get("/changelog/?include_unpublished=true", headers=auth(client, "TEST001"))
        assert response.status_code == 403
        assert "Draft" not in response.get_data(as_text=True)

    def test_admin_can_read_drafts(self, client, test_admin):
        add_entry("Live", True)
        add_entry("Draft", False)

        response = client.get("/changelog/?include_unpublished=true", headers=auth(client, "ADMIN001"))
        assert response.status_code == 200
        assert {c["title"] for c in response.get_json()} == {"Live", "Draft"}

    def test_revoked_token_cannot_read_drafts(self, client, test_admin):
        headers = auth(client, "ADMIN001")
        client.post("/auth/logout", headers=headers)
        assert client.get("/changelog/?include_unpublished=true", headers=headers).status_code == 401


class TestAdminWorkflow:
    entry = {"version": "2.0.0", "title": "New thing", "description": "It does stuff", "category": "Feature"}

    def test_staff_cannot_create_publish_or_delete(self, client, test_user):
        headers = auth(client, "TEST001")
        draft = add_entry("Draft", False)

        assert client.post("/changelog/create", json=self.entry, headers=headers).status_code == 403
        assert client.patch(f"/changelog/{draft.id}/publish", headers=headers).status_code == 403
        assert client.delete(f"/changelog/{draft.id}", headers=headers).status_code == 403

    def test_create_validates_fields_and_category(self, client, test_admin):
        headers = auth(client, "ADMIN001")
        missing = client.post("/changelog/create", json={"version": "1"}, headers=headers)
        assert missing.status_code == 400
        assert "version, title, and description are required" in missing.get_json()["error"]

        bad_category = client.post("/changelog/create", json={**self.entry, "category": "Gossip"}, headers=headers)
        assert bad_category.status_code == 400
        assert "Invalid category" in bad_category.get_json()["error"]

    def test_create_then_publish_then_delete(self, client, test_admin):
        headers = auth(client, "ADMIN001")

        created = client.post("/changelog/create", json=self.entry, headers=headers)
        assert created.status_code == 201
        entry_id = created.get_json()["changelog"]["id"]
        assert created.get_json()["changelog"]["is_published"] is False
        assert client.get(f"/changelog/{entry_id}").status_code == 404  # still a draft

        assert client.patch(f"/changelog/{entry_id}/publish", headers=headers).status_code == 200
        assert client.get(f"/changelog/{entry_id}").get_json()["title"] == "New thing"

        assert client.delete(f"/changelog/{entry_id}", headers=headers).status_code == 200
        assert client.get(f"/changelog/{entry_id}").status_code == 404

    def test_unknown_entry_is_404(self, client, test_admin):
        headers = auth(client, "ADMIN001")
        assert client.patch("/changelog/999/publish", headers=headers).status_code == 404
        assert client.delete("/changelog/999", headers=headers).status_code == 404

import csv
import io
from datetime import date, time

import pytest

from app import db
from app.models.bookings import Booking
from app.models.users import User


def auth(client, staff_id, password="password123"):
    response = client.post("/auth/login", json={"staff_id": staff_id, "password": password})
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def add(user_id, day, start, end, status="Approved", location="Tema", purpose="Visit"):
    booking = Booking(
        user_id=user_id,
        booking_date=day,
        start_time=start,
        end_time=end,
        location=location,
        purpose=purpose,
        status=status,
    )
    db.session.add(booking)
    db.session.commit()
    return booking


@pytest.fixture
def data(app, test_user, test_admin):
    finance = User(
        staff_id="FIN001",
        full_name="Ama Finance",
        email="fin@example.com",
        password="password123",
        department="Finance",
    )
    db.session.add(finance)
    db.session.commit()

    add("TEST001", date(2030, 1, 7), time(9, 0), time(11, 0), "Approved", "Tema", "Client visit")  # 2h, IT
    add("TEST001", date(2030, 1, 8), time(9, 0), time(10, 0), "Pending", "Tema", "Client visit")  # 1h, IT
    add("FIN001", date(2030, 1, 8), time(14, 0), time(15, 30), "Declined", "Airport", "Pickup")  # 1.5h, Finance
    add("FIN001", date(2030, 2, 1), time(9, 0), time(10, 0), "Cancelled", "Tema", "Audit")  # 1h, Finance


class TestAccess:
    @pytest.mark.parametrize("url", ["/reports/summary", "/reports/bookings", "/reports/export.csv"])
    def test_requires_login(self, client, url):
        assert client.get(url).status_code == 401

    @pytest.mark.parametrize("url", ["/reports/summary", "/reports/bookings", "/reports/export.csv"])
    def test_staff_are_refused(self, client, data, url):
        assert client.get(url, headers=auth(client, "TEST001")).status_code == 403


class TestSummary:
    def get(self, client, query=""):
        response = client.get(f"/reports/summary{query}", headers=auth(client, "ADMIN001"))
        assert response.status_code == 200, response.get_json()
        return response.get_json()

    def test_totals_and_rates(self, client, data):
        totals = self.get(client)["totals"]
        assert totals["total"] == 4
        assert (totals["approved"], totals["pending"], totals["declined"], totals["cancelled"]) == (1, 1, 1, 1)
        assert totals["total_hours"] == 5.5
        assert totals["average_duration_hours"] == 1.38  # 5.5 / 4, rounded to 2 places
        assert totals["approval_rate"] == 25.0
        assert totals["decline_rate"] == 25.0
        assert totals["unique_staff"] == 2
        assert totals["bookings_per_staff"] == 2.0
        assert totals["backlog_hours"] == 1.0  # only the pending booking

    def test_cancelled_is_its_own_status_bucket(self, client, data):
        by_status = {row["status"]: row["value"] for row in self.get(client)["by_status"]}
        assert by_status == {"Approved": 1, "Pending": 1, "Declined": 1, "Cancelled": 1}

    def test_top_location_peak_hour_and_breakdowns(self, client, data):
        result = self.get(client)
        assert result["top_location"] == {"name": "Tema", "count": 3}
        assert result["peak_hour"] == {"hour": 9, "count": 3}
        assert result["by_purpose"][0] == {"name": "Client visit", "value": 2}
        assert {row["name"]: row["value"] for row in result["by_department"]} == {"IT": 2, "Finance": 2}

        hourly = {row["hour"]: row["count"] for row in result["hourly_demand"]}
        assert hourly[9] == 3 and hourly[14] == 1 and hourly[6] == 0
        assert len(result["hourly_demand"]) == 13

    def test_daily_hours(self, client, data):
        daily = {row["date"]: row["hours"] for row in self.get(client)["daily_hours"]}
        assert daily == {"2030-01-07": 2.0, "2030-01-08": 2.5, "2030-02-01": 1.0}

    def test_filters_apply_to_every_figure(self, client, data):
        result = self.get(client, "?from=2030-01-08&to=2030-01-31&department=Finance")
        assert result["totals"]["total"] == 1
        assert result["totals"]["declined"] == 1
        assert result["top_location"]["name"] == "Airport"

    def test_department_list_ignores_the_current_filters(self, client, data):
        result = self.get(client, "?department=Finance")
        assert "IT" in result["departments"] and "Finance" in result["departments"]

    def test_empty_result_is_all_zeros(self, client, data):
        result = self.get(client, "?from=2031-01-01")
        assert result["totals"]["total"] == 0
        assert result["totals"]["approval_rate"] == 0
        assert result["top_location"] == {"name": "", "count": 0}
        assert result["peak_hour"] == {"hour": None, "count": 0}

    def test_bad_filters_are_400(self, client, data):
        headers = auth(client, "ADMIN001")
        assert client.get("/reports/summary?from=nope", headers=headers).status_code == 400
        bad_status = client.get("/reports/summary?status=Maybe", headers=headers)
        assert bad_status.status_code == 400
        assert "status must be one of" in bad_status.get_json()["error"]


class TestBookingRows:
    def test_paged_newest_first_with_duration(self, client, data):
        response = client.get("/reports/bookings?page=1&page_size=2", headers=auth(client, "ADMIN001"))
        body = response.get_json()
        assert body["total"] == 4 and body["total_pages"] == 2
        assert [b["booking_date"] for b in body["bookings"]] == ["2030-02-01", "2030-01-08"]
        assert body["bookings"][0]["duration_hours"] == 1.0

    def test_defaults_to_a_page_even_without_paging_params(self, client, data):
        body = client.get("/reports/bookings", headers=auth(client, "ADMIN001")).get_json()
        assert body["page"] == 1 and body["page_size"] == 25

    def test_status_filter(self, client, data):
        body = client.get("/reports/bookings?status=Pending", headers=auth(client, "ADMIN001")).get_json()
        assert body["total"] == 1


class TestCsvExport:
    def export(self, client, query=""):
        response = client.get(f"/reports/export.csv{query}", headers=auth(client, "ADMIN001"))
        assert response.status_code == 200
        return response, list(csv.reader(io.StringIO(response.get_data(as_text=True))))

    def test_headers_and_rows(self, client, data):
        response, rows = self.export(client)
        assert response.mimetype == "text/csv"
        assert "attachment; filename=" in response.headers["Content-Disposition"]
        assert rows[0] == [
            "Booking Date",
            "Staff",
            "Department",
            "Start",
            "End",
            "Duration (h)",
            "Location",
            "Purpose",
            "Status",
        ]
        assert len(rows) == 5  # header + 4 bookings
        assert rows[1][0] == "2030-02-01"  # newest first

    def test_export_respects_filters(self, client, data):
        _, rows = self.export(client, "?status=Declined")
        assert len(rows) == 2
        assert rows[1][1] == "Ama Finance" and rows[1][5] == "1.50"

    def test_spreadsheet_formulas_are_neutralised(self, client, data):
        add("TEST001", date(2030, 3, 4), time(9, 0), time(10, 0), purpose='=HYPERLINK("http://evil.example","click")')
        add("TEST001", date(2030, 3, 5), time(9, 0), time(10, 0), location="+1234", purpose="@SUM(A1)")

        _, rows = self.export(client)
        purposes = {row[7] for row in rows[1:]}
        assert '\'=HYPERLINK("http://evil.example","click")' in purposes
        assert "'@SUM(A1)" in purposes
        assert "'+1234" in {row[6] for row in rows[1:]}
        assert not any(cell.startswith(("=", "@", "+")) for row in rows[1:] for cell in row)

"""Guns lent to friends: shots per gun, ammo cost, separate from your own record. Synthetic data only."""
import pytest
from flask import Flask

import diary
from diary import loans, users

H = {"X-Requested-With": "fetch"}


@pytest.fixture()
def client():
    users.ensure("osku", "test-password-1", admin=True, db_file="diary.db")
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "t"
    diary.init_app(app)
    conn = diary.connect()
    conn.executescript("DELETE FROM gun_loans; DELETE FROM kv WHERE key='ammo_prices';")
    conn.commit()
    conn.close()
    c = app.test_client()
    assert c.post("/diary/login", data={"username": "osku", "password": "test-password-1"}).status_code == 302
    return c


LOAN = {"date": "2026-10-18", "event": "Club match", "paid": ["Mikko"], "lines": [
    {"who": "Mikko", "gunId": "g-ar", "gun": "WWSD AR-15", "cal": ".223 Rem", "rounds": 120, "price": 0.6},
    {"who": "Ville", "gunId": "g-ar", "gun": "WWSD AR-15", "cal": ".223 Rem", "rounds": 80, "price": "0,6"},
    {"who": "Ville", "gunId": "g-t3", "gun": "T3x", "cal": "6.5 CM", "rounds": 20, "price": 2}]}


def test_prices_default_to_the_ammo_calculator(client):
    p = client.get("/diary/api/ammo-prices").get_json()
    assert p[".223 Rem"] == 0.60 and p["6.5 CM"] == 2.00
    assert client.put("/diary/api/ammo-prices", json={".223 Rem": "0,65", "6.5 CM": 2.1}).get_json()["prices"] == {".223 Rem": 0.65, "6.5 CM": 2.1}
    assert client.get("/diary/api/ammo-prices").get_json() == {".223 Rem": 0.65, "6.5 CM": 2.1}
    assert client.put("/diary/api/ammo-prices", json={".223": "x"}).status_code == 400


def test_loan_crud_cost_and_backup(client):
    assert client.put("/diary/api/loans/n-1", json=LOAN).status_code == 200
    got = client.get("/diary/api/loans").get_json()
    assert len(got) == 1 and got[0]["lines"][1]["price"] == 0.6 and got[0]["paid"] == ["Mikko"]
    # Unpaid: Ville 80 × 0.60 + 20 × 2.00 = 88.00 (Mikko paid)
    assert client.get("/diary/api/home").get_json()["diary"]["unpaid"] == 88.0
    # Not part of your own record: the visits list is untouched
    assert all(s.get("event") != "Club match" for s in client.get("/diary/api/sessions").get_json())
    data = client.get("/diary/api/account/export").get_json()
    assert data["loans"][0]["event"] == "Club match" and data["ammo_prices"][".223 Rem"] == 0.6
    conn = diary.connect()
    conn.execute("DELETE FROM gun_loans")
    assert loans.import_rows(conn, data, replace=True) == 1
    conn.commit()
    conn.close()
    assert len(client.get("/diary/api/loans").get_json()) == 1
    # Validation, CSRF, delete
    assert client.put("/diary/api/loans/n-2", json=dict(LOAN, date="18.10.2026")).status_code == 400
    assert client.put("/diary/api/loans/n-2", json=dict(LOAN, lines=[{"who": "x", "rounds": -5}])).status_code == 400
    assert client.put("/diary/api/loans/n-2", data="x").status_code == 415
    assert client.delete("/diary/api/loans/n-1").status_code == 403
    assert client.delete("/diary/api/loans/n-1", headers=H).status_code == 200
    assert client.get("/diary/api/loans").get_json() == []

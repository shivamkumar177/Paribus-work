import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

VALID_CSV = (
    "name,address,phone\n"
    "General Hospital,123 Main St,555-1234\n"
    "City Clinic,45 Oak Ave,\n"
    "Riverbend Medical,9 River Rd,555-9999\n"
).encode()


class FakeHospitalClient:
    """Stand-in for the real Hospital Directory API."""

    def __init__(self, fail_names=()):
        self.fail_names = set(fail_names)
        self.created: list[dict] = []
        self.activated: list[str] = []
        self._next_id = 100

    def create_hospital(self, payload: dict) -> dict:
        if payload["name"] in self.fail_names:
            raise RuntimeError(f"directory rejected {payload['name']}")
        self.created.append(payload)
        self._next_id += 1
        return {"id": self._next_id, "name": payload["name"], "active": False,
                "creation_batch_id": payload["creation_batch_id"]}

    def activate_batch(self, batch_id: str) -> dict:
        self.activated.append(batch_id)
        return {"batch_id": batch_id, "activated": True}


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
def fake(monkeypatch):
    fake = FakeHospitalClient()
    monkeypatch.setattr("app.services.hospital_service.hospital_client", fake)
    return fake


def _files(data: bytes, name="hospitals.csv"):
    return {"file": (name, data, "text/csv")}


@pytest.mark.asyncio
async def test_validate_valid_csv(client, fake):
    res = await client.post("/api/v1/hospitals/bulk/validate", files=_files(VALID_CSV))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["valid"] is True
    assert body["valid_rows"] == 3
    assert body["columns"] == ["name", "address", "phone"]


@pytest.mark.asyncio
async def test_validate_missing_required_column(client, fake):
    bad = b"name,phone\nGeneral,555-1234\n"
    res = await client.post("/api/v1/hospitals/bulk/validate", files=_files(bad))
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is False
    assert any(e["field"] == "address" for e in body["errors"])


@pytest.mark.asyncio
async def test_bulk_create_activates_batch(client, fake):
    res = await client.post("/api/v1/hospitals/bulk", files=_files(VALID_CSV))
    assert res.status_code == 201, res.text
    body = res.json()

    assert body["total_hospitals"] == 3
    assert body["processed_hospitals"] == 3
    assert body["failed_hospitals"] == 0
    assert body["batch_activated"] is True
    assert body["status"] == "completed"
    assert all(h["status"] == "created_and_activated" for h in body["hospitals"])
    assert body["batch_id"] in fake.activated

    # Every hospital was sent with the batch id, and phone is optional.
    assert {c["name"] for c in fake.created} == {"General Hospital", "City Clinic", "Riverbend Medical"}
    assert all(c["creation_batch_id"] == body["batch_id"] for c in fake.created)
    city = next(c for c in fake.created if c["name"] == "City Clinic")
    assert "phone" not in city


@pytest.mark.asyncio
async def test_bulk_partial_then_resume(client, fake):
    fake.fail_names = {"City Clinic"}

    first = (await client.post("/api/v1/hospitals/bulk", files=_files(VALID_CSV))).json()
    assert first["status"] == "partial"
    assert first["failed_hospitals"] == 1
    assert first["batch_activated"] is False
    assert fake.activated == []

    # The directory recovers; resuming should only retry the failed row.
    fake.fail_names = set()
    created_before = len(fake.created)

    resumed = (await client.post(f"/api/v1/hospitals/bulk/{first['batch_id']}/resume")).json()
    assert resumed["status"] == "completed"
    assert resumed["failed_hospitals"] == 0
    assert resumed["batch_activated"] is True
    assert len(fake.created) - created_before == 1  # only the missing row re-created


@pytest.mark.asyncio
async def test_status_endpoint(client, fake):
    created = (await client.post("/api/v1/hospitals/bulk", files=_files(VALID_CSV))).json()
    res = await client.get(f"/api/v1/hospitals/bulk/{created['batch_id']}")
    assert res.status_code == 200
    assert res.json()["batch_id"] == created["batch_id"]


@pytest.mark.asyncio
async def test_too_many_hospitals_rejected(client, fake, monkeypatch):
    monkeypatch.setattr("app.services.hospital_service.settings.BULK_MAX_HOSPITALS", 2)
    res = await client.post("/api/v1/hospitals/bulk", files=_files(VALID_CSV))
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_non_csv_rejected(client, fake):
    res = await client.post("/api/v1/hospitals/bulk", files=_files(VALID_CSV, name="hospitals.txt"))
    assert res.status_code == 400

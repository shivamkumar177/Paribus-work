import io

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

CSV = (
    "name,address,phone\n"
    "General Hospital,123 Main St,555-1234\n"
    "City Clinic,45 Oak Ave,\n"
    "Riverbend Medical,9 River Rd,555-9999\n"
).encode()


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def split(data: bytes, size: int) -> list[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


async def _initiate(client, chunks: int, size: int) -> str:
    res = await client.post(
        "/api/v1/uploads/initiate",
        json={
            "filename": "hospitals.csv",
            "file_size": len(CSV),
            "total_chunks": chunks,
            "chunk_size": size,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["upload_id"]


async def _send_chunk(client, upload_id: str, index: int, blob: bytes):
    return await client.post(
        f"/api/v1/uploads/{upload_id}/chunk",
        data={"chunk_index": str(index)},
        files={"file": (f"part{index}", blob, "application/octet-stream")},
    )


@pytest.mark.asyncio
async def test_full_upload_and_data(client):
    parts = split(CSV, 40)
    upload_id = await _initiate(client, len(parts), 40)

    for i, blob in enumerate(parts):
        res = await _send_chunk(client, upload_id, i, blob)
        assert res.status_code == 200, res.text

    done = (await client.post(f"/api/v1/uploads/{upload_id}/complete")).json()
    assert done["status"] == "completed"
    assert done["row_count"] == 3
    assert done["columns"] == ["name", "address", "phone"]

    data = (await client.get(f"/api/v1/data/{upload_id}?page=1&page_size=10")).json()
    assert data["total"] == 3
    assert data["data"][0]["name"] == "General Hospital"


@pytest.mark.asyncio
async def test_resume_flow(client):
    parts = split(CSV, 40)
    upload_id = await _initiate(client, len(parts), 40)

    # Upload only the first half, then simulate a disconnect.
    for i in range(len(parts) // 2):
        assert (await _send_chunk(client, upload_id, i, parts[i])).status_code == 200

    resume = (await client.get(f"/api/v1/uploads/{upload_id}/resume")).json()
    assert len(resume["received_chunks"]) == len(parts) // 2
    assert len(resume["missing_chunks"]) == len(parts) - len(parts) // 2
    assert resume["progress_pct"] < 100

    # Completing early must fail.
    assert (await client.post(f"/api/v1/uploads/{upload_id}/complete")).status_code == 400

    # Send only the missing chunks (as a resuming client would).
    for i in resume["missing_chunks"]:
        assert (await _send_chunk(client, upload_id, i, parts[i])).status_code == 200

    done = (await client.post(f"/api/v1/uploads/{upload_id}/complete")).json()
    assert done["status"] == "completed"
    assert done["row_count"] == 3


@pytest.mark.asyncio
async def test_chunk_is_idempotent(client):
    parts = split(CSV, 40)
    upload_id = await _initiate(client, len(parts), 40)

    for i, blob in enumerate(parts):
        assert (await _send_chunk(client, upload_id, i, blob)).status_code == 200
    # Re-sending an existing chunk must not break anything.
    res = await _send_chunk(client, upload_id, 0, parts[0])
    assert res.status_code == 200
    assert res.json()["all_chunks_received"] is True


@pytest.mark.asyncio
async def test_initiate_rejects_non_csv(client):
    res = await client.post(
        "/api/v1/uploads/initiate",
        json={"filename": "data.txt", "file_size": 10, "total_chunks": 1, "chunk_size": 10},
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_unknown_session_returns_404(client):
    res = await client.get("/api/v1/uploads/does-not-exist/resume")
    assert res.status_code == 404

# Hospital Bulk Processing System

A FastAPI service that takes a CSV of hospitals, creates each one in the
[Hospital Directory API](https://hospital-directory.onrender.com/docs) under one
batch ID, and activates the batch once every hospital has been created.
Progress can be followed live over Server-Sent Events, and a batch that fails
part-way can be resumed without creating duplicate hospitals.

- **Live URL:** <https://hospital-bulk-processing-5wrn.onrender.com> (UI at `/`, Swagger at `/docs`)
- **Stack:** Python 3.11, FastAPI, httpx, in-memory storage, Docker

---

## Quick start (Docker)

```bash
docker compose up --build
```

Open <http://localhost:8000> for the upload UI, or <http://localhost:8000/docs> for Swagger.

If port 8000 is already in use on your machine, choose another host port:

```bash
HOST_PORT=8080 docker compose up --build
```

Without Compose:

```bash
docker build -t hospital-bulk .
docker run --rm -p 8000:8000 hospital-bulk
```

Try it with the sample files: `sample_hospitals.csv` (3 rows) or
`sample_hospitals_20.csv` (20 rows, the maximum, which is useful for watching
live progress):

```bash
curl -F file=@sample_hospitals.csv http://localhost:8000/hospitals/bulk
```

## Running without Docker

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env        # optional; the defaults work
python run.py               # http://localhost:8000
pytest                      # runs the tests; the Directory API is mocked
```

## Configuration

All settings are environment variables (see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `HOSPITAL_API_BASE_URL` | `https://hospital-directory.onrender.com` | Upstream Directory API |
| `HOSPITAL_API_TIMEOUT` | `30` | Per-request timeout (seconds) |
| `HOSPITAL_API_CONCURRENCY` | `5` | Number of parallel `POST /hospitals/` calls per batch |
| `HOSPITAL_API_MAX_RETRIES` | `2` | Retries for each row after the first attempt |
| `BULK_MAX_HOSPITALS` | `20` | Maximum number of rows per CSV |
| `PORT` | `8000` | Port the server listens on (Render sets this) |

---

## API

Routes are served from the root, so the bulk endpoint is exactly the
`POST /hospitals/bulk` that the brief specifies.

### Hospital bulk processing

| Method | Path | Description |
|---|---|---|
| `POST` | `/hospitals/bulk` | Upload a CSV, create every hospital, then activate the batch. Add `?wait=false` to get `202` straight away and follow progress live |
| `POST` | `/hospitals/bulk/validate` | Check a CSV without creating anything |
| `GET` | `/hospitals/bulk/{batch_id}` | Batch status and per-row results |
| `GET` | `/hospitals/bulk/{batch_id}/events` | Live progress as Server-Sent Events (see [Live progress](#live-progress-sse)) |
| `POST` | `/hospitals/bulk/{batch_id}/resume` | Resume a batch that did not finish: retry the unfinished rows and/or the activation (see [Resume](#resume)). Also accepts `?wait=false` |
| `GET` | `/hospitals/batches` | List all batches |
| `GET` | `/health/` | Health check |

**CSV format:** `name,address,phone`. `name` and `address` are required, `phone`
is optional, and a file can have at most 20 rows. The delimiter (`,`, `;`, tab or
`|`) and the encoding (UTF-8 with or without a BOM, or Latin-1) are detected
automatically.

```bash
# Validate first (optional)
curl -F file=@sample_hospitals.csv http://localhost:8000/hospitals/bulk/validate

# Process
curl -F file=@sample_hospitals.csv http://localhost:8000/hospitals/bulk
```

```json
{
  "batch_id": "ddb7eec0-75ff-435b-a140-ae04f24df876",
  "filename": "sample_hospitals.csv",
  "status": "completed",
  "total_hospitals": 3,
  "processed_hospitals": 3,
  "failed_hospitals": 0,
  "pending_hospitals": 0,
  "progress_pct": 100.0,
  "processing_time_seconds": 6.68,
  "batch_activated": true,
  "error": null,
  "hospitals": [
    { "row": 1, "name": "General Hospital", "hospital_id": 170, "status": "created_and_activated", "error": null }
  ]
}
```

**Batch `status`:** `processing` → `activating` → `completed`. If some rows fail,
the batch ends as `partial`. If every row fails, it ends as `failed`.

**Row `status`:** `pending`, `created`, `created_and_activated` or `failed`.

**Errors** use a single format:
`{"success": false, "status_code": ..., "message": ..., "details": ...}`.
They return `400` for a non-CSV file, `422` for a CSV that fails validation or
has too many rows, `404` for an unknown batch, and `409` when you try to resume
a batch that is already running.

### Resume

A batch that ends as `partial` or `failed` stays inactive in the directory. To
finish it, call:

```bash
curl -X POST http://localhost:8000/hospitals/bulk/<batch_id>/resume
```

1. **Rows that already succeeded are skipped.** Each row's outcome is saved as
   soon as it finishes, so only the rows that failed or never ran are attempted
   again.
2. **The directory is checked before retrying.** A row can be recorded as
   failed even though the hospital was created, for example when the request
   timed out after the directory had saved it. Before retrying anything, resume
   calls `GET /hospitals/batch/{batch_id}`. Any failed row whose name and
   address match a hospital already in that batch is marked as created with that
   hospital's ID, and is not created again. If this check itself fails, resume
   stops without retrying any rows, so it never risks creating duplicates.
3. **Activation is retried as well.** If every hospital was created but the
   activation call failed, resume runs only the activation.
4. **Only one run per batch at a time.** A second request while a batch is
   processing gets `409`, so two runs can't create the same rows.

Calling resume on a batch that is already activated returns it unchanged.

The upload page has a **Resume** button on every batch that can be resumed.

### Live progress (SSE)

By default, `POST /hospitals/bulk` responds only when the whole batch has
finished, with the result format the brief shows. To follow progress while it
runs:

1. **Start the batch without waiting.** Send
   `POST /hospitals/bulk?wait=false` (or `.../resume?wait=false`). It responds
   `202 Accepted` straight away with the `batch_id` and `status: "processing"`,
   and creates the hospitals in the background.
2. **Subscribe to the stream.** Open
   `GET /hospitals/bulk/{batch_id}/events` (`text/event-stream`). Each time the
   batch changes, the server sends an event whose `data` is the full batch: the
   same JSON as `GET /hospitals/bulk/{batch_id}`, including counts,
   `progress_pct` and per-row statuses. It also sends a keep-alive comment every
   15 seconds so proxies don't close a quiet connection.
3. **Wait for the final event.** The server closes the stream after sending a
   batch whose status is `completed`, `partial` or `failed`.

```bash
ID=$(curl -s -F file=@sample_hospitals.csv "http://localhost:8000/hospitals/bulk?wait=false" | jq -r .batch_id)
curl -N http://localhost:8000/hospitals/bulk/$ID/events
```

```
data: {"batch_id": "...", "status": "processing", "processed_hospitals": 0, "progress_pct": 0.0, ...}

data: {"batch_id": "...", "status": "processing", "processed_hospitals": 2, "progress_pct": 66.67, ...}

data: {"batch_id": "...", "status": "completed", "processed_hospitals": 3, "progress_pct": 100.0, "batch_activated": true, ...}
```

In the browser this is a plain `EventSource`. The upload page uses it to drive
its progress bar and to update the per-row table as each hospital is created:

```js
const es = new EventSource(`/hospitals/bulk/${batchId}/events`);
es.onmessage = (e) => {
  const batch = JSON.parse(e.data);
  render(batch);
  if (["completed", "partial", "failed"].includes(batch.status)) es.close();
};
```

The batch is claimed before the `202` is sent. So a resume request for a batch
that is already running gets its `409` immediately, and the stream never reports
a batch as finished just before it starts again.

---

## How bulk processing works

```
CSV upload
  │  1. validate (required columns, required values, ≤ 20 rows)
  │  2. create batch: UUID + one record per row, in the in-memory store
  ▼
ThreadPoolExecutor (HOSPITAL_API_CONCURRENCY workers)
  │  3. POST /hospitals/ {name, address, phone?, creation_batch_id}
  │     each row is retried up to HOSPITAL_API_MAX_RETRIES times;
  │     each row's outcome is saved as it finishes
  ▼
All rows created?
  ├─ yes → 4. PATCH /hospitals/batch/{id}/activate → status "completed"
  └─ no  → leave the batch inactive → status "partial"/"failed"
            POST /bulk/{id}/resume: match failed rows against the directory,
            retry only the rest, then activate
  ▼
5. Return the full result (counts, timing, per-row outcome)
   with ?wait=false: 202 immediately, progress streamed on /bulk/{id}/events
```

### Design decisions

- **Concurrent creation with a fixed limit.** A 20-row batch runs about
  5× faster than creating hospitals one at a time, and the limit keeps the load
  on the upstream API predictable. The work runs in a thread pool
  (`asyncio.to_thread`), so the event loop stays free to answer status and
  health requests while a batch runs.
- **Activate only when every row succeeded.** This is what the brief asks for.
  A partly created batch stays inactive, so nothing half-finished becomes
  visible in the directory.
- **Resume at row level, checked against the directory.** Resume treats the
  Directory API as the source of truth for what was actually created, so
  running it again never creates duplicates (see [Resume](#resume)).
- **Progress over SSE rather than WebSockets.** Progress only flows from the
  server to the client, so SSE is enough. It is plain HTTP, works through
  Render's proxy, and the browser's `EventSource` reconnects on its own. The
  stream reads the in-memory store every 0.5 seconds and sends an event only
  when the batch has changed. Reading the store is cheap, and it avoids having
  to pass notifications from the worker threads to the event loop.
- **Blocking by default, background on request.** Without `?wait=false`,
  `POST /hospitals/bulk` behaves exactly as the brief describes. With it, the
  batch runs as a FastAPI background task and the caller follows the stream.
- **Upstream client in its own module.** `HospitalClient` is a small wrapper
  around `httpx`. The tests replace it with a fake, so the test suite does not
  touch the network.
- **In-memory storage** behind a single `InMemoryStore` class with one lock.
  The brief allows this, and it keeps the service free of external
  dependencies.

### Known limitations

- Batch state is lost on restart, and the service cannot run as more than one
  instance. Moving the store to Redis or Postgres would fix both.
- Background runs (`?wait=false`) live inside the API process. If the process
  restarts mid-batch, that batch is lost. A job queue such as Celery or RQ would
  survive restarts.
- Each open event stream checks the store every 0.5 seconds. That is fine for a
  handful of viewers. Many concurrent viewers would call for push notifications
  instead, for example Redis pub/sub.
- During a single run, the automatic per-row retries don't check the directory
  first, so a timeout after the hospital was actually created can still produce
  a duplicate. The Directory API has no idempotency key that would prevent this.
  Resume does check the directory first.
- The Directory API runs on Render's free tier. The first call after it has been
  idle can take about 50 seconds while it wakes up, and the per-row retries
  cover that delay.

---

## Testing

```bash
pytest
```

The tests cover:

- CSV validation, the 20-row limit and rejection of non-CSV files
- a full create-and-activate run
- resume after a partial failure, which retries only the failed row
- resume when a hospital was created but the response was lost, which matches
  the existing hospital instead of creating a duplicate
- resume when only the activation call failed, which re-runs only the activation
- `409` when a batch is already running, and `404` for an unknown batch
- `?wait=false` returning `202` and the batch finishing in the background
- the event stream sending the batch and closing once it finishes, and `404`
  for the stream of an unknown batch

The Directory API is replaced with a fake in all tests.

## Deployment (Render)

The repository includes a Render Blueprint (`render.yaml`) that describes the
whole service: a Docker web service on the free plan, with `/health/` as its
health check.

1. In Render, choose **New → Blueprint** and connect this repository.
2. Click **Apply**. Render builds the `Dockerfile` and redeploys on every push
   to `main`.

Render injects `$PORT`, and the container's start command listens on it.

The free tier sleeps after 15 minutes without traffic, so the first request
after that takes about 30–60 seconds.

## Project layout

```
app/
  api/v1/endpoints/   hospitals.py (bulk, validate, status, resume), health.py
  services/           hospital_service.py (bulk flow + resume), hospital_client.py (upstream HTTP),
                      csv_parser.py
  storage/memory.py   thread-safe in-memory store for batch jobs
  schemas/            Pydantic response models
  static/index.html   upload UI
tests/                pytest suite (upstream API mocked)
Dockerfile, docker-compose.yml
```

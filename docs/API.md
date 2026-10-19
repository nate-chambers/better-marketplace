# HTTP API

The server exposes OpenAPI at `/openapi.json`. Use the schema for field types and validation bounds. All write requests, including queries sent with POST, require:

```http
Content-Type: application/json
X-Marketplace-Client: 1
```

Browser requests must use an allowed `Origin`. Keep this single-user API on localhost or a trusted private network.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Version and health |
| GET | `/api/bootstrap` | Default criteria, saved searches, provider connection status, limits, recent jobs |
| GET | `/api/apify/usage` | Sanitized Apify account usage, spending limit, plan allowance, and cycle |
| POST | `/api/listings/query` | Re-evaluate observations and return a filtered page |
| POST | `/api/listings/import` | Import a raw listing array with optional original observation timestamp |
| POST | `/api/listings/export/csv` | Export the current view across all pages |
| POST | `/api/listings/export/json` | Export the current view with evidence |
| PUT | `/api/listings/{id}/bookmark` | Set `{ "saved": true }` or false |
| POST | `/api/searches` | Save a validated search |
| PUT | `/api/searches/{id}` | Update a saved search |
| DELETE | `/api/searches/{id}` | Delete a saved search |
| POST | `/api/collections/plan` | Preview a bounded collection; starts no provider runs |
| POST | `/api/collections` | Start paid collection with an idempotency key |
| GET | `/api/collections` | Get recent jobs, individual runs, costs, notices |
| POST | `/api/collections/{id}/stop` | Stop unsubmitted searches; active runs can finish |
| POST | `/api/collections/{id}/resume` | Resume a previously interrupted collection |

## Query example

```python
import httpx

base = "http://127.0.0.1:8768"
headers = {"X-Marketplace-Client": "1"}
search = httpx.get(f"{base}/api/bootstrap").json()["default_search"]
search.update(makes=["Toyota"], allowed_models=["Camry"], min_price=15000, max_price=25000)

response = httpx.post(
    f"{base}/api/listings/query",
    headers=headers,
    json={"search": search, "status": "match", "sort": "price_asc", "page": 1},
)
response.raise_for_status()
print(response.json()["items"])
```

The response includes `items`, `total`, `counts`, model/reason facets, page, and page size. Eligibility counts apply to the full evaluated dataset; `total` applies to the selected view, text, model, and reason filters. Each item includes evidence, reasons, observation time, source, and displayed-price history.

## Collection identity and recovery

The bootstrap search has empty make/model/city lists and null numeric bounds. Blank criteria leave that constraint unrestricted. Collection plans require at least one search city and a make, model, or custom search term. A blank plan returns HTTP 400 without creating jobs. Direct listing refreshes do not require city or make criteria.

Collection requests include a `request_id` of 8–80 letters, digits, underscores, or hyphens. A repeated request ID returns the original job and does not submit duplicate work. Reusing a key does not change the original search.

The server reserves an actor-run journal before POSTing to Apify. It retries bounded GET requests for selected rate-limit and server errors. It does not retry POST requests. If a network interruption leaves the outcome uncertain, the job records `unknown` or `needs_check` and stops submitting queued searches. Check Apify Console and the journal before starting another collection.

`listing_ids` optionally replaces search queries with a direct detail refresh for up to 50 numeric Facebook listing IDs. Detail refreshes also use the budget cap and require an explicit collection request.

## Apify account usage

`GET /api/apify/usage` returns `used_usd`, `monthly_limit_usd`, `remaining_to_limit_usd`, `plan_included_credits_usd`, `plan_name`, `cycle_start_at`, `cycle_end_at`, `checked_at`, `stale`, `status`, and `message`. `credit_balance_usd` is null because the supported account endpoints do not expose a reliable purchased/promotional-credit balance. Remaining-to-limit is spending headroom, not a wallet balance.

Amounts and limits come from the connected account. Unknown fields stay null; zero is a valid reported amount. Status is `ready`, `partial`, `not_configured`, `unavailable`, or `stale`. On refresh failure, last verified values retain their original `checked_at` and gain the stale marker. Caches are isolated by token and never written to the database.

The normal cache lasts 60 seconds, with a 20-second backoff on failure. `?refresh=true` requests fresh values, coalescing requests within five seconds. The endpoint makes bounded read-only requests and never starts an Actor, changes an account limit, or exposes the token, email, profile, or proxy credentials.

# Local Product demo (`deployments/demo/`)

A **local Product demo** you can run with one command and use in your browser. It is
**not a production installation and not a real integration**.

```bash
./scripts/demo.sh up        # build, start, and print the URL, Product API key and Store UUID
```

Then open **http://127.0.0.1:3000** and paste the printed Product API key and Store UUID
into the Operations Console session settings.

## What it is

- **The real Product.** The demo runs the same Product API and Web images
  (`apps/api/Dockerfile`, `apps/web/Dockerfile`) and the same `postgres → migrate → api →
  web` services as the deployment template. `compose.override.yaml` is layered on the
  unchanged `deployments/template/compose.yaml`; there is no separate demo API, demo
  endpoint, or demo frontend.
- **Every business action flows through the Product:** browser → Web BFF → private
  Product API → Product authentication, services, governance, the Daily Operations
  Workflow and the Operations Agent → the mock commerce backend → PostgreSQL (durable
  commands and audit).
- **Internal deterministic mock commerce data.** The business backend is the existing
  deterministic `mock` fixture. It is not a demo-specific adapter. The canonical dataset
  date is **2026-03-03**, which always has one failed shipment to investigate. It never
  depends on today's date.
- **A deterministic local demo model.** `APP_DEFAULT_MODEL_PROVIDER=demo` selects a
  LOCAL-DEMO-ONLY native Agno model (`apps/api/app/runtime/demo_model.py`).
  - For a message that contains an exact date (`YYYY-MM-DD`), it calls the Product's own
    `get_daily_operations_report` tool once and summarizes that tool's result.
  - Every figure, finding and recommended action comes from the Product workflow. The
    model calculates nothing and invents nothing.
  - It never creates tickets: Operations analysis stays read-only.
  - For any other message, it answers with a short usage hint.
- **No external calls.** There are no model-provider or commerce-provider calls, and no
  API keys from OpenAI, Anthropic or anyone else are needed. In the demo, the API
  container is attached only to the private internal networks (no outbound network).
- **Real Product authentication.** Product API-key auth stays on:
  - `scripts/demo.sh` generates a fresh random Product API key per demo environment.
  - The API receives only its SHA-256 (`APP_PRODUCT_API_KEYS`).
  - The raw key is shown to you and kept in your browser tab's memory only. It is never
    given to the API or Web containers.

## Commands

| Command | What it does |
|---|---|
| `./scripts/demo.sh up` | Checks Docker/Compose, builds both images, generates credentials on first use, validates the composed config, starts the stack, waits for the migration, API and Web health, and prints what you need. |
| `./scripts/demo.sh credentials` | Prints only the Web URL, Product API key, Store UUID and demo date. |
| `./scripts/demo.sh status` | Shows service state, the Product API health through the Web BFF, whether a key is configured, and the Store UUID and date. |
| `./scripts/demo.sh down` | Stops the demo. **The database is kept**: `up` resumes it with the same credentials, and earlier ticket commands are still there. |
| `./scripts/demo.sh reset` | Stops the demo and **deletes** its database volume and credentials (`docker compose down -v`). The next `up` starts fresh with new credentials. |

**Prerequisites:** Docker with Docker Compose v2, plus standard shell tools (`od` and
`sha256sum` or `shasum`). You don't need Python, Node, `uv` or `npm`: the image builds
bring their own dependencies. The demo uses host port 3000 (127.0.0.1 only).

## Try it

1. **Operations analysis:** `Analyze operations for 2026-03-03.`
2. **Daily report:** the business date `2026-03-03`.
3. **Operational ticket:**
   - Title: `Investigate failed shipment`
   - Description: `Review the failed shipment found in the demo operations report.`
4. **Command status:** look up the returned command ID. It stays `verified` across
   `down`/`up`.

## Where the credentials live

`deployments/demo/.runtime/` is created by the launcher. It is git-ignored, the directory
is mode `700`, and its files are mode `600`:

- `runtime.env` is the Compose input for the demo:
  - the database password,
  - the AgentOS `OS_SECURITY_KEY`,
  - the Product API key **hash**,
  - the canonical company ID.

  The launcher never prints the password or the OS key.
- `credentials` holds what you paste into the console: the raw Product API key, the Store
  UUID, the demo date and the Web URL. It is never passed to a container.

The canonical Store UUID and company ID are derived by the Product's own mock-fixture
algorithm inside the API image, not copied by hand.

## Not production

- The demo pins `APP_ENVIRONMENT=local`. Both the mock backend and the demo model are
  **refused in staging and production**, and the API refuses to start if either is
  configured there.
- The production-shaped `deployments/template/` is unchanged. It still defaults to the
  `disabled` business backend and the `disabled` model, and it stays **fail-closed** until
  a reviewed real business backend exists.
- See [`docs/MVP_RELEASE_ACCEPTANCE.md`](../../docs/MVP_RELEASE_ACCEPTANCE.md).
  Production business use is still blocked: there is no real business-system integration.

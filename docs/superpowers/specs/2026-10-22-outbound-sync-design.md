# FHIR Gateway — Configurable Outbound Sync (OpenHIM, extensible to others)

## Context

`fhir_gateway` today is read-only: external systems `GET` FHIR resources we map on demand from
Healthcare's doctypes (Patient, Encounter, Observation federated with Vital Signs, Practitioner,
MedicationRequest, DiagnosticReport). Nothing is pushed out.

This design adds the opposite direction: when specific clinical records are submitted, build the
FHIR resource and push it to one or more configured external destinations in near-real-time —
starting with OpenHIM, generalized so "any other system" is a new destination row, not new code.

**Grounding reference.** A Postman collection (`CCE Emitter Adaptor — Dev Environment`) documents a
real, separately-deployed OpenHIM instance (`openhim.cce.mdtlabs.org`) with a channel already
provisioned and named `spice-facility` (`{{openhim_host}}/spice-facility/fhir/<ResourceType>`,
HTTP Basic Auth, `X-Facility-Id` / `X-Source-Event-Id` / `X-Correlation-Id` headers). The push
customization that currently feeds a channel in that same OpenHIM instance (the `ebuzima` channel)
lives in a different repo/deployment we don't have access to — so this design is built fresh, kept
compatible with the documented channel contract (URL shape, Basic Auth, header names as
configuration), not copied from code we can't see.

The collection's resource set is Encounter, Observation, Condition, MedicationRequest,
Immunization, ServiceRequest. Checked against Healthcare's actual doctypes: Immunization has no
source doctype in Healthcare at all (confirmed by search — no such doctype exists), so it's out of
scope entirely; everything else is sourceable.

## Goals

- Real-time push of newly-submitted clinical records, as FHIR R4 resources, to configurable
  external destinations.
- Reuse the existing read-side mappers (`encounter.py`, `observation.py`, `medication_request.py`)
  rather than duplicating FHIR-shaping logic; two new mappers needed (`service_request.py`,
  `condition.py`).
- Destination configuration (URL, auth, headers, which resource types) is data, not code — a
  second destination of the same auth type is a new row; a new auth type is an additive module.
- Durable: a record that successfully submits is never silently un-queued by a crash or a
  transient destination outage.

## Non-goals (see "Out of scope" for the full list)

Cancellation/amendment propagation, historical backfill, auth schemes beyond Basic, a replay UI,
multi-facility routing, and Prometheus-style metrics are deliberately not built in this design.

## Architecture overview

```
doc_events (on_submit)
  -> mapper.to_fhir(doc)                         [reused from the read side; 2 new mappers]
  -> enqueue_sync_event(...)                      [same DB transaction as the submit]
       -> FHIR Sync Event   (payload snapshot, event_id, correlation_id)
       -> FHIR Sync Delivery (one per enabled destination matching this resource type)
  -> background job, enqueued after commit        [near-real-time first attempt]

scheduler sweep (every 5 min)
  -> FHIR Sync Delivery where status in (Pending, Failed) and next_attempt_at <= now
  -> attempt_delivery(delivery_name)               [same function the immediate job calls]

attempt_delivery(delivery_name)
  -> apply_auth(destination, request_kwargs)       [auth_type registry, Basic only today]
  -> HTTP POST {destination.base_url}/{resource_type}
  -> 2xx              -> Delivered
  -> 4xx except 429    -> Dead-lettered (no retry; a repeat won't change a client error)
  -> 429/5xx/timeout  -> Failed, next_attempt_at = backoff(attempt_count); past max -> Dead-lettered
  -> Dead-lettered     -> POST summary to ALERT_WEBHOOK_URL

daily job
  -> purge FHIR Sync Event (+ its Deliveries) where every Delivery is Delivered
     and older than FHIR Sync Settings.retention_days
     (Dead-lettered deliveries are never auto-purged)
```

## Data model

All four doctypes live in `fhir_gateway`.

### `FHIR Sync Destination`

One row per target (OpenHIM's `spice-facility` channel today).

| Field | Type | Notes |
|---|---|---|
| `destination_name` | Data, unique | e.g. "OpenHIM - CCE" |
| `enabled` | Check, default 1 | |
| `base_url` | Data, required | dispatcher appends `/{resource_type}`; `validate()` requires `https://` unless host is `localhost`/`127.0.0.1`/`*.localhost` |
| `auth_type` | Select, default `Basic` | only `Basic` is a real option today |
| `client_id` | Data | |
| `client_secret` | Password | encrypted at rest; read only via `get_password()` |
| `event_id_header_name` | Data, default `X-Source-Event-Id` | blank omits the header |
| `correlation_id_header_name` | Data, default `X-Correlation-Id` | blank omits the header |
| `timeout_seconds` | Int, default 10 | |
| `max_retry_attempts` | Int, default 6 | matches the 6-step backoff schedule below |
| `extra_headers` | Table → `FHIR Sync Destination Header` (`header_name` Data, `header_value` Data) | static per-destination headers, e.g. `X-Facility-Id` |
| `resource_types` | Table → `FHIR Sync Destination Resource Type` (`resource_type` Select: Encounter / Observation / Condition / MedicationRequest / ServiceRequest) | which types this destination receives |

`track_changes: 1` at the doctype level — Frappe's built-in version history on credential/URL
changes, no new audit subsystem needed.

### `FHIR Sync Event`

One per submitted source record — the outbox row. `autoname: field:event_id`.

| Field | Type | Notes |
|---|---|---|
| `event_id` | Data, unique (UUID) | also the docname |
| `correlation_id` | Data (UUID) | shared by every record of one Patient Encounter (UUIDv5 of the encounter name); random otherwise |
| `resource_type` | Select: Encounter / Observation / Condition / MedicationRequest / ServiceRequest | |
| `source_doctype` | Data | traceability only, never re-read to regenerate the payload |
| `source_name` | Data | |
| `fhir_payload` | Long Text (JSON string) | the mapped FHIR resource, snapshotted at enqueue time |

### `FHIR Sync Delivery`

Its own top-level doctype (not a child table of `FHIR Sync Event`) — one row per `(event,
destination)` pair, so the scheduler sweep is one flat query regardless of which event or
destination a row belongs to. `autoname: hash`.

| Field | Type | Notes |
|---|---|---|
| `sync_event` | Link → FHIR Sync Event, required | |
| `destination` | Link → FHIR Sync Destination, required | |
| `status` | Select: Pending / Delivered / Failed / Dead-lettered, default Pending | |
| `attempt_count` | Int, default 0 | |
| `next_attempt_at` | Datetime | |
| `last_error` | Small Text | concise reason only, never a secret or full traceback |
| `delivered_at` | Datetime | |

### `FHIR Sync Settings` (Single)

| Field | Type | Notes |
|---|---|---|
| `retention_days` | Int, default 30 | how long a fully-`Delivered` event is kept before purge |

Registered as `scheduler_events["daily"] = ["fhir_gateway.sync.retention.purge_delivered_events"]` —
same explicit-path convention as the dispatch sweep below.

## Trigger mechanism

`hooks.py` `doc_events`, one `on_submit` hook per source doctype:

```
Patient Encounter   -> on_submit  (also emits one Condition event per diagnosis child row present)
Observation         -> on_submit
Vital Signs         -> on_submit
Medication Request  -> on_submit
Service Request     -> on_submit
```

Child tables don't get their own `doc_events`, so `Condition` (sourced from `Patient Encounter
Diagnosis` rows) has to ride on `Patient Encounter`'s own `on_submit` — it builds its own `Encounter`
event and then walks its diagnosis rows, one `Condition` event each, via the new `condition.py`
mapper.

**Fault isolation (load-bearing correctness rule).** Mapping and enqueueing are wrapped in a narrow
try/except inside the `on_submit` hook. A failure there is logged (`frappe.log_error`, full
traceback) but never re-raised — a bug in the sync path must never block the clinical submit it's
attached to. On the success path, enqueueing one `FHIR Sync Event` plus its `FHIR Sync Delivery`
rows is atomic with the submit (same DB transaction), so a submit that commits is guaranteed to have
queued its event; only the *failure* path is deliberately non-blocking.

Each `FHIR Sync Delivery` row is created only for destinations that are `enabled` and whose
`resource_types` includes this resource type, as of enqueue time — a destination added later only
sees future events, never a retroactive backfill of an already-queued event.

Right after the transaction commits, a background job is enqueued to attempt delivery for the rows
just created (near-real-time); the scheduler sweep below is the safety net.

## Dispatch mechanism

One shared `attempt_delivery(delivery_name)`, called by both the immediate post-commit job and the
scheduler sweep (`scheduler_events["cron"]["*/5 * * * *"]`, querying `FHIR Sync Delivery` where
`status in (Pending, Failed) and next_attempt_at <= now`).

Per-attempt outcome:

| Response | Outcome |
|---|---|
| 2xx | `Delivered`, `delivered_at` set |
| 4xx except 429 | `Dead-lettered` immediately — retrying an identical bad-auth/bad-payload request won't change the outcome |
| 429, 5xx, timeout, connection error | `attempt_count += 1`, `next_attempt_at = now + backoff[attempt_count]`; past `max_retry_attempts` → `Dead-lettered` |

Backoff schedule (seconds): `[60, 300, 1800, 7200, 21600, 86400]` — 1m, 5m, 30m, 2h, 6h, 24h,
matching the default `max_retry_attempts = 6`.

Exceptions are caught narrowly: `requests.exceptions.RequestException` (network/timeout) is
distinguished from inspecting `response.status_code` for HTTP-level outcomes. `last_error` holds a
short reason, never a full traceback or a secret.

Race handling (revised after code review, 2026-10-29): each attempt first *claims* the row in its
own short transaction — it increments `attempt_count` and pushes `next_attempt_at` out by a lease
(600 s, longer than the `short` queue's 300 s job timeout), then commits. No lock is held during the
HTTP call. A second job for the same row sees the lease and backs off; a job killed mid-call leaves
the row to be re-picked when the lease expires, and since the attempt was already counted, a row that
keeps dying is dead-lettered ("Attempt did not complete") instead of retried forever. Both the
immediate job and the sweep enqueue under one deterministic RQ job id with `deduplicate`.
`X-Source-Event-Id` still lets the destination be idempotent on whatever slips through.

Queue safety: the immediate job is scheduled from our own after-commit callback and
`enqueue_attempt` never raises (Frappe runs after-commit callbacks unguarded inside `commit()`, so a
Redis/queue error would otherwise surface to the clinician after their record was saved). It also
skips enqueueing while the shared `short` queue is past half of Frappe's `MAX_QUEUED_JOBS`; skipped
rows are already due, so the sweep picks them up. The sweep takes at most 50 rows per tick.

Granularity is one background job per delivery attempt, not batched.

On transition to `Dead-lettered`, POST a short summary (resource_type, destination, event_id, error)
to the site's existing `ALERT_WEBHOOK_URL` (already configured for deploy alerts in this pipeline) —
reuses existing infra instead of a new notification channel.

## Auth abstraction

`fhir_gateway/sync/auth.py` exposes `apply_auth(destination, request_kwargs) -> request_kwargs`, a
registry keyed on `destination.auth_type`. Today it has exactly one branch:

```
"Basic" -> request_kwargs["auth"] = (destination.client_id, destination.get_password("client_secret"))
```

Any other value raises a clear error — not a real code path today (the `Select` field only offers
`Basic`), just a defensive guard against a destination row created directly via API/fixture with a
stale value. Adding `API Key` or `OAuth2 Client Credentials` later means one new function here and
one new `Select` option; the dispatcher, retry logic, and everything else is untouched.

`client_secret` is resolved via `get_password()` only at the moment of the request — never cached in
plaintext — and no log or `last_error` value ever contains the resolved credential or the raw
`Authorization` header.

## Security, permissions, fault isolation

- **Transport**: `base_url` validation requires `https://` except for the explicit
  `localhost`/`127.0.0.1`/`*.localhost` dev exception.
- **Permissions**: a new `FHIR Sync Administrator` role (same purpose-built pattern as
  `Receptionist`/`Finance Staff` in `spice_facility`), scoped to exactly the 4 new doctypes.
  `FHIR Sync Destination` holds credentials; `FHIR Sync Event`/`FHIR Sync Delivery` hold raw
  PHI-bearing FHIR payloads — neither should be readable by ordinary facility roles.
- **Fault isolation**: see Trigger mechanism above — sync failures never block the clinical submit.

## Testing strategy

Follows the existing `fhir_gateway` test patterns (real `frappe.get_doc().insert()`, "every real
option is mapped" tests) with one deliberate exception for the dispatcher:

- **New mappers** (`service_request.py`, `condition.py`): same shape as existing mapper tests, plus
  an "every real status option is mapped" test each.
- **Triggers**: submitting a `Patient Encounter` with diagnosis rows creates the expected `Encounter`
  + one `Condition` per diagnosis, with `Delivery` fan-out matching each destination's
  `resource_types`. A fault-isolation test forces a mapper exception and asserts the encounter still
  submits successfully, an Error Log entry exists, and no `Sync Event` was created.
- **Dispatcher**: a local loopback HTTP test server stands in for the destination (real HTTP over
  the wire, just not a third party we don't control) — 200 → `Delivered`; 401 → immediate
  `Dead-lettered`, no retry; 500 → `Failed` with the correct `next_attempt_at`; repeated 500s past
  `max_retry_attempts` → `Dead-lettered` and a second local test server (standing in for
  `ALERT_WEBHOOK_URL`) receives the alert. Basic Auth header and the configurable header names
  (including blank-to-omit) are asserted directly.
- **Scheduler sweep**: a `Failed` delivery with a past `next_attempt_at` is picked up; one with a
  future `next_attempt_at` is skipped.
- **Retention**: a fully-`Delivered` event past `retention_days` is purged; a `Dead-lettered` one
  never is, regardless of age.
- **Permissions**: a user without `FHIR Sync Administrator` cannot read/write any of the 4 new
  doctypes.

All of this runs through the `bench run-tests --app fhir_gateway` call already wired into
`spice_facility`'s CI/CD pipeline — no new CI surface.

## Out of scope (flagged, not silently dropped)

- **Immunization** — no source doctype in Healthcare.
- **Cancellation/amendment propagation** — only the original `on_submit` is synced; a later
  cancellation or amendment doesn't push an update/retraction.
- **Auth schemes beyond Basic** — the registry is built to add them; nothing beyond `Basic` is
  implemented.
- **Manual replay/retry UI** — a `Dead-lettered` row can be manually flipped back to `Pending` from
  the plain doctype form (which resets its retry budget and makes it due now) and the sweep will pick
  it up; no dedicated retry UI is built.
- **Historical backfill** — nothing retroactively pushes records submitted before this feature
  existed.
- **Multi-facility routing** — `X-Facility-Id` (or equivalent) is a static value per destination,
  not computed per-record; this assumes one facility per site, consistent with how this deployment
  is already set up (one `SITE_NAME` per environment).
- **Prometheus-style metrics** — the reference Emitter Adaptor exposes `cce_emitter_*` counters;
  this design's observability is the doctype list views (pending/failed/dead-lettered counts) plus
  the dead-letter webhook alert.

## Assumptions carried forward (confirm if they turn out wrong)

- The backoff schedule and `retention_days` default are reasonable starting points, not values
  derived from any SLA. `retention_days`, `max_retry_attempts` and `timeout_seconds` are
  configuration; the backoff schedule itself is a code constant (`dispatcher.BACKOFF_SECONDS`).
- `X-Facility-Id`, header names, and the exact channel URL shape are configured per destination
  (not hardcoded), so if the real production OpenHIM contract differs in some detail from the
  reference collection, it's a configuration change, not a design change.

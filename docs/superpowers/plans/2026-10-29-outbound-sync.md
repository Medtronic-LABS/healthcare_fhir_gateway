# FHIR Gateway Outbound Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push newly-submitted clinical records, as FHIR R4 resources, to configurable external destinations (OpenHIM `spice-facility` channel first) via a durable outbox with retry, dead-letter alerting and retention.

**Architecture:** `doc_events.on_submit` → existing/new mapper → `FHIR Sync Event` + one `FHIR Sync Delivery` per matching destination, written in the submit's transaction (savepoint-guarded, never blocking the submit) → after-commit background job and a 5-minute sweep both call one `attempt_delivery()` → `requests.post` with a pluggable auth registry. Daily purge of fully-delivered events.

**Tech Stack:** Frappe v16, Healthcare, Python 3.14, `requests`, Frappe RQ (`frappe.enqueue`), `unittest` via `bench run-tests`.

**Spec:** `docs/superpowers/specs/2026-10-22-outbound-sync-design.md`

## Global Constraints

- Resource types in scope: `Encounter`, `Observation`, `Condition`, `MedicationRequest`, `ServiceRequest` (no Immunization).
- Backoff seconds: `[60, 300, 1800, 7200, 21600, 86400]`; `max_retry_attempts` default `6`; `timeout_seconds` default `10`; `retention_days` default `30`.
- Default header names: `X-Source-Event-Id`, `X-Correlation-Id`; blank name omits the header.
- `base_url` must be `https://` unless host is `localhost`, `127.0.0.1` or `*.localhost`.
- Only `auth_type = "Basic"` exists; secret read via `get_password("client_secret")` at request time only.
- `last_error` holds a short reason — never a traceback, credential, or `Authorization` header.
- A sync-path failure never blocks the clinical submit (logged via `frappe.log_error`).
- Dead-lettered deliveries are never auto-purged.
- All 4 top-level doctypes (+2 child tables) readable/writable only by role `FHIR Sync Administrator`.
- Module: `FHIR Gateway`. Code style: tabs, ruff line-length 110, module docstrings explaining *why*, like the existing mappers.
- Tests run with: `docker exec frappe-uhis-next-backend-1 bash -c 'export PATH=$HOME/.local/bin:$PATH; cd ~/frappe-bench; bench --site frappehealth.localhost run-tests --app fhir_gateway --module <module>'` (abbreviated below as `RUN <module>`). Doctype changes need `bench --site frappehealth.localhost migrate` first.

## Review Focus

1. **Duplicate/late job on an already-final row** — immediate job and sweep race, or a delivery was dead-lettered/delivered meanwhile: `attempt_delivery` must be a no-op on `Delivered`/`Dead-lettered` and on a deleted row. Test in Task 5.
2. **Destination disabled after enqueue** — no HTTP call, delivery left untouched (re-enabling resumes it). Test in Task 5.
3. **Alert webhook unset or unreachable** — dead-letter still recorded, no exception escapes. Test in Task 5.
4. **`base_url` with trailing slash** — URL must be `.../fhir/Encounter`, not `.../fhir//Encounter`. Test in Task 5.
5. **Encounter with zero diagnoses / mapper raising mid-fan-out** — exactly one Encounter event; on a failure, no partial Event/Delivery rows survive (savepoint rollback) and the submit still succeeds. Test in Task 4.

---

### Task 1: ServiceRequest + Condition mappers

**Files:**
- Modify: `fhir_gateway/mappers/_shared.py` (move `_fhir_enum_code` here as `fhir_enum_code`)
- Modify: `fhir_gateway/mappers/medication_request.py` (import the shared one)
- Create: `fhir_gateway/mappers/service_request.py`, `fhir_gateway/mappers/condition.py`
- Test: `fhir_gateway/tests/test_service_request_mapper.py`, `fhir_gateway/tests/test_condition_mapper.py`

**Interfaces:**
- Produces: `service_request.to_fhir(doc) -> dict`, `service_request.DOCTYPE = "Service Request"`, `RESOURCE_TYPE = "ServiceRequest"`; `condition.to_fhir(encounter_doc, diagnosis_row) -> dict`, `condition.RESOURCE_TYPE = "Condition"`; `_shared.fhir_enum_code(value) -> str | None`.
- Push-only: neither mapper is added to `router.SUPPORTED_RESOURCES` (read API unchanged).

- [ ] **Step 1: Write failing tests** (`frappe._dict` stand-ins, real `Code Value` names as in `test_medication_request_mapper.py`):
  - `test_to_fhir_basic_shape`: `status="active-Request Status"`, `intent="Original Order-Intent"`, `priority="Routine-Priority"`, `template_dt="Lab Test Template"`, `template_dn="CBC"`, `order_date="2026-01-01"`, `order_time="10:00:00"`, `order_group="HLC-ENC-1"`, `order_group_doctype="Patient Encounter"` → `status=="active"`, `intent=="original-order"`, `priority=="routine"`, `code=={"text":"CBC"}` (no codification), `subject`, `requester=Practitioner/..`, `authoredOn=="2026-01-01T10:00:00"`, `encounter=={"reference":"Encounter/HLC-ENC-1"}`.
  - `test_missing_status_falls_back_to_unknown`: `status=None` → `"unknown"`, `intent=="order"`, no `priority`, no `encounter` when `order_group` empty.
  - `test_every_real_status_option_is_mapped`: for every `Code Value` whose `code_system == "Request Status"`, `fhir_enum_code(code_value_string(name))` ∈ FHIR request-status set `{"draft","active","on-hold","revoked","completed","entered-in-error","unknown"}`.
  - Condition `test_to_fhir_basic_shape`: encounter `_dict(name="HLC-ENC-1", patient="P1", practitioner="PR1", encounter_date="2026-01-01", encounter_time="10:00:00")`, row `_dict(name="row1", diagnosis="Dengue Fever")` with a real `Diagnosis` record → `id=="row1"`, `clinicalStatus.coding[0].code=="active"`, `verificationStatus...code=="confirmed"`, `category[0].coding[0].code=="encounter-diagnosis"`, `code.text=="Dengue Fever"`, `subject`, `encounter`, `recorder`, `recordedDate=="2026-01-01T10:00:00"`.
  - Condition `test_codes_come_from_diagnosis_codification`: Diagnosis with a codification row (`system="http://hl7.org/fhir/sid/icd-10"`, `code="A90"`) → `code.coding[0]["code"]=="A90"`.
- [ ] **Step 2:** `RUN fhir_gateway.tests.test_service_request_mapper` / `test_condition_mapper` → FAIL (ImportError).
- [ ] **Step 3:** Implement. ServiceRequest `code`: `codification_to_coding(doc.codification_table)` + `text = doc.template_dn or doc.name`. `encounter` only when `order_group_doctype == "Patient Encounter"` (it's a Dynamic Link). Condition codes: `codification_to_coding` of the `Diagnosis` doc's table (`frappe.db.exists` guard).
- [ ] **Step 4:** Both modules + `test_medication_request_mapper` PASS.
- [ ] **Step 5:** Commit `feat(mappers): ServiceRequest and Condition mappers for outbound sync`.

### Task 2: Sync doctypes, role, destination validation

**Files:**
- Create doctypes under `fhir_gateway/fhir_gateway/doctype/`: `fhir_sync_destination`, `fhir_sync_destination_header` (istable), `fhir_sync_destination_resource_type` (istable), `fhir_sync_event`, `fhir_sync_delivery`, `fhir_sync_settings` (issingle) — fields exactly per spec Data model.
- Modify: `fhir_gateway/fixtures/role.json`, `hooks.py` fixtures filter → `["name", "in", ["FHIR Integration", "FHIR Sync Administrator"]]`
- Test: `fhir_gateway/tests/test_sync_doctypes.py`

**Interfaces:**
- Produces: `RESOURCE_TYPES` Select options string identical across Destination Resource Type + Event; `FHIRSyncDestination.validate()` enforcing https rule; `FHIRSyncDestination.get_resource_types() -> set[str]`.
- Autonames: Destination `field:destination_name`; Event `field:event_id`; Delivery `hash`. Destination `track_changes: 1`. Delivery indexes on `status`, `next_attempt_at`, `sync_event` (`search_index: 1`).

- [ ] **Step 1: Write failing tests**: `test_http_rejected_for_remote_host` (`http://openhim.example.org/fhir` → `frappe.ValidationError`); `test_http_allowed_for_localhost` for `http://localhost:8080`, `http://127.0.0.1:1`, `http://openhim.localhost`; `test_defaults` (header names, timeout 10, retries 6, Settings.retention_days 30); `test_user_without_role_cannot_access` — a System User with no FHIR role gets `frappe.has_permission(dt, "read") is False` for all 4 doctypes; one with `FHIR Sync Administrator` gets True.
- [ ] **Step 2:** migrate, `RUN fhir_gateway.tests.test_sync_doctypes` → FAIL.
- [ ] **Step 3:** Write doctype JSON + controllers; permissions: only `FHIR Sync Administrator` (read/write/create/delete; Settings read/write).
- [ ] **Step 4:** migrate, PASS.
- [ ] **Step 5:** Commit `feat(sync): outbox doctypes, destination config and FHIR Sync Administrator role`.

### Task 3: Auth registry

**Files:** Create `fhir_gateway/sync/__init__.py`, `fhir_gateway/sync/auth.py`; Test `fhir_gateway/tests/test_sync_auth.py`

**Interfaces:** Produces `apply_auth(destination, request_kwargs: dict) -> dict`; `UnsupportedAuthType(frappe.ValidationError)`.

- [ ] **Step 1: Failing tests**: Basic → `kwargs["auth"] == ("cid", "s3cret")` (secret stored via Password field on a real inserted destination); `auth_type="Bogus"` (set via `db_set`, bypassing Select) → `UnsupportedAuthType`.
- [ ] **Step 2–4:** RUN → FAIL; implement dict registry `{"Basic": _basic}`; PASS.
- [ ] **Step 5:** Commit `feat(sync): pluggable auth registry (Basic)`.

### Task 4: Outbox enqueue + submit triggers

**Files:** Create `fhir_gateway/sync/outbox.py`, `fhir_gateway/sync/triggers.py`; Modify `hooks.py` (`doc_events`); Test `fhir_gateway/tests/test_sync_triggers.py`

**Interfaces:**
- Consumes: mappers (`encounter`, `observation`, `medication_request`, `service_request`, `condition`).
- Produces: `outbox.enqueue_sync_event(resource_type: str, payload: dict, source_doctype: str, source_name: str) -> str` (event name); creates Deliveries with `status="Pending"`, `next_attempt_at=now`; schedules `frappe.enqueue("fhir_gateway.sync.dispatcher.attempt_delivery", delivery_name=..., queue="short", enqueue_after_commit=True)` per delivery. Payload stored via `frappe.as_json`. `triggers.on_submit(doc, method=None)`; `triggers.build_events(doc) -> list[tuple[resource_type, payload]]`.
- hooks: `doc_events = {dt: {"on_submit": "fhir_gateway.sync.triggers.on_submit"} for dt in ("Patient Encounter", "Observation", "Vital Signs", "Medication Request", "Service Request")}`.

- [ ] **Step 1: Failing tests** (real destinations: A takes `Encounter`+`Condition`, B takes `Encounter` only, C disabled with all types):
  - `test_encounter_with_diagnoses_fans_out`: submitted Patient Encounter with 2 diagnosis rows → 1 Encounter event + 2 Condition events; Encounter has deliveries to A,B; each Condition only to A; none to C; payloads parse as JSON with matching `resourceType`; `event_id`, `correlation_id` are UUIDs.
  - `test_encounter_without_diagnoses_emits_only_encounter`.
  - `test_build_events_per_doctype`: `build_events` on `_dict` stand-ins for Vital Signs/Observation → `[("Observation", …)]`.
  - `test_mapper_failure_does_not_block_submit`: patch `condition.to_fhir` to raise → encounter `docstatus == 1`, an `Error Log` row was added, and **zero** Sync Events for that encounter (Encounter event rolled back too).
  - `test_no_matching_destination_creates_no_deliveries`: event still stored? — No: if no destination matches, skip the event entirely (nothing to deliver); assert zero events.
- [ ] **Step 2:** RUN → FAIL.
- [ ] **Step 3:** Implement. `on_submit` wraps `build_events` + all enqueues in `frappe.db.savepoint("fhir_sync")`; on `Exception` → `frappe.db.rollback(save_point="fhir_sync")` + `frappe.log_error(title="FHIR sync enqueue failed", reference_doctype=…, reference_name=…)`. (Broad catch is the spec's load-bearing fault-isolation rule; comment it.)
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(sync): enqueue FHIR sync events on clinical submit`.

### Task 5: Dispatcher, retry, dead-letter alert, sweep

**Files:** Create `fhir_gateway/sync/dispatcher.py`, `fhir_gateway/sync/alerts.py`; Modify `hooks.py` (`scheduler_events["cron"]["*/5 * * * *"]`); Test `fhir_gateway/tests/test_sync_dispatcher.py` (+ loopback server helper in `tests/_support.py`)

**Interfaces:**
- Produces: `dispatcher.BACKOFF_SECONDS`; `dispatcher.attempt_delivery(delivery_name: str) -> None`; `dispatcher.sweep_due_deliveries() -> None` (enqueues one job per due row, cap 500 per tick); `alerts.alert_webhook_url() -> str | None` (`frappe.conf.alert_webhook_url` → env `ALERT_WEBHOOK_URL`); `alerts.notify_dead_letter(delivery, event, destination) -> None` (never raises).
- Request: `POST {base_url.rstrip("/")}/{resource_type}`, body = stored payload, `Content-Type: application/fhir+json`, extra headers + id headers, `timeout=destination.timeout_seconds`.

- [ ] **Step 1: Failing tests** — `_support.LoopbackServer(status_codes: list[int])` (threaded `http.server`, records path/headers/body):
  - `test_2xx_marks_delivered` (status, `delivered_at`, `attempt_count==1`, path `/fhir/Encounter` with `base_url` ending in `/fhir/`, Basic header, `X-Source-Event-Id`, `X-Correlation-Id`, extra header `X-Facility-Id`).
  - `test_blank_header_name_omits_header`.
  - `test_401_dead_letters_without_retry` + alert server receives JSON containing event_id; `last_error` contains `"401"` and not the secret.
  - `test_500_schedules_retry` (`Failed`, `attempt_count==1`, `next_attempt_at ≈ now+60s`).
  - `test_429_is_retried`.
  - `test_connection_error_is_retried` (closed port).
  - `test_exhausted_retries_dead_letter` (`max_retry_attempts=2`, two 500s).
  - `test_noop_on_final_or_missing_row`, `test_disabled_destination_untouched` (Review Focus 1–2).
  - `test_alert_failure_never_raises` (alert URL unset, and pointing at closed port) (Review Focus 3).
  - `test_sweep_picks_due_skips_future` (patch `frappe.enqueue`, assert called for due row only).
- [ ] **Step 2–4:** RUN → FAIL; implement; PASS.
- [ ] **Step 5:** Commit `feat(sync): delivery dispatcher with backoff, dead-letter alert and sweep`.

### Task 6: Retention purge

**Files:** Create `fhir_gateway/sync/retention.py`; Modify `hooks.py` (`scheduler_events["daily"]`); Test `fhir_gateway/tests/test_sync_retention.py`

**Interfaces:** Produces `retention.purge_delivered_events() -> int` (count purged).

- [ ] **Step 1: Failing tests**: old event with all deliveries `Delivered` → purged (event + deliveries); old event with one `Dead-lettered` → kept; recent all-delivered → kept; old event with a `Pending` → kept.
- [ ] **Step 2–4:** RUN → FAIL; implement (age by event `creation`; bounded batch of 1000); PASS.
- [ ] **Step 5:** Commit `feat(sync): daily purge of fully-delivered sync events`.

### Task 7: Full suite, docs, review

- [ ] `RUN` whole app (`--app fhir_gateway`) → all PASS; `ruff check` + `ruff format --check` clean.
- [ ] README: "Outbound sync" section — configuring a destination, statuses, manual replay (set Dead-lettered → Pending), `ALERT_WEBHOOK_URL`.
- [ ] Independent review via `frappe-code-reviewer`; fix findings.
- [ ] Commit `docs: outbound sync configuration`. Pin bump in `spice_facility/docker/allinone/fhir_gateway.env` only after the user approves push/merge.

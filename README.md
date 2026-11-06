### FHIR Gateway

A FHIR R4 REST API over Frappe Healthcare clinical data, for consumption by external systems such as OpenHIM.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app fhir_gateway
```

### Outbound sync

Besides the read API, the gateway pushes newly-**submitted** clinical records to external systems
(e.g. an OpenHIM channel) as FHIR R4 resources. Design: `docs/superpowers/specs/2026-10-22-outbound-sync-design.md`.

| Submitted doctype | Pushed as |
|---|---|
| Patient Encounter | `Encounter`, plus one `Condition` per diagnosis row |
| Observation, Vital Signs | `Observation` |
| Medication Request | `MedicationRequest` |
| Service Request | `ServiceRequest` |

**Configure a destination** (role **FHIR Sync Administrator**): create a *FHIR Sync Destination* with
its base URL (`https://` required except for localhost), Basic-auth client ID/secret, the resource
types it should receive, and any static headers such as `X-Facility-Id` (header values are stored in
plain text — never put a credential there). Each resource is `POST`ed to
`{base_url}/{resourceType}` with `X-Source-Event-Id` / `X-Correlation-Id` headers (names configurable,
blank omits). Only records submitted after a destination exists are sent — there is no backfill, and
cancellations/amendments are not propagated.

**Delivery** is attempted right after the submit commits, and a scheduler sweep every 5 minutes
retries anything still due. Each *FHIR Sync Delivery* ends as:

- `Delivered` — 2xx response.
- `Failed` — 429/5xx/timeout; retried after 1m, 5m, 30m, 2h, 6h, 24h (up to the destination's
  *Max Retry Attempts*), then `Dead-lettered`.
- `Dead-lettered` — any other 4xx, or retries exhausted. A short summary (no patient data) is POSTed
  to the alert webhook: site config `alert_webhook_url`, else the `ALERT_WEBHOOK_URL` env var.
  To retry one by hand, set its status back to `Pending` (this restores its full retry budget).

A sync failure never blocks the clinical submit; it is recorded in the Error Log instead. Fully
delivered events are purged daily after *FHIR Sync Settings → Retention Days* (default 30);
dead-lettered ones are kept.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/fhir_gateway
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

gpl-3.0

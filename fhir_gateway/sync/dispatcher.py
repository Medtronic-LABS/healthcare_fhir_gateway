"""Delivers one `FHIR Sync Delivery` to its destination. `attempt_delivery` is the single code path
for both the near-real-time job enqueued after submit and the 5-minute scheduler sweep.

Outcome per attempt:
- 2xx -> Delivered.
- 429 / 5xx / timeout / connection error -> Failed, retried after BACKOFF_SECONDS[attempt-1];
  once retries exceed the destination's max_retry_attempts -> Dead-lettered.
- anything else (4xx, unexpected 3xx) -> Dead-lettered at once: resending an identical request
  that was rejected for its auth or content won't change the answer.

Races between the immediate job and a sweep are narrowed two ways: both enqueue under one
deterministic RQ job id with deduplicate, and the row is locked (SELECT ... FOR UPDATE) for the
attempt, so a second worker waits and then sees a final status. The destination is still expected
to be idempotent on the event-id header for whatever slips through."""

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from fhir_gateway.sync import DELIVERY, DESTINATION, EVENT, alerts, auth

# 1m, 5m, 30m, 2h, 6h, 24h -- one step per retry, matching the default max_retry_attempts = 6.
BACKOFF_SECONDS = [60, 300, 1800, 7200, 21600, 86400]
SWEEP_BATCH_SIZE = 500
_RETRYABLE_STATUSES = ("Pending", "Failed")
_FHIR_JSON = "application/fhir+json"

# _post outcomes
_DELIVERED, _RETRY, _REJECTED = "delivered", "retry", "rejected"


def enqueue_attempt(delivery_name, enqueue_after_commit=False):
	frappe.enqueue(
		"fhir_gateway.sync.dispatcher.attempt_delivery",
		queue="short",
		job_id=f"fhir_sync_delivery::{delivery_name}",
		deduplicate=True,
		enqueue_after_commit=enqueue_after_commit,
		delivery_name=delivery_name,
	)


def sweep_due_deliveries():
	"""Scheduler safety net: re-enqueue every retryable delivery whose time has come, oldest first,
	for enabled destinations only (a disabled one's rows wait, untouched, until it's re-enabled)."""
	enabled = frappe.get_all(DESTINATION, filters={"enabled": 1}, pluck="name")
	if not enabled:
		return
	due = frappe.get_all(
		DELIVERY,
		filters={
			"status": ["in", _RETRYABLE_STATUSES],
			"next_attempt_at": ["<=", now_datetime()],
			"destination": ["in", enabled],
		},
		pluck="name",
		order_by="next_attempt_at asc",
		limit=SWEEP_BATCH_SIZE,
	)
	for name in due:
		enqueue_attempt(name)


def attempt_delivery(delivery_name):
	if not frappe.db.exists(DELIVERY, delivery_name):
		return
	delivery = frappe.get_doc(DELIVERY, delivery_name, for_update=True)
	if delivery.status not in _RETRYABLE_STATUSES:
		return
	destination = frappe.get_doc(DESTINATION, delivery.destination)
	if not destination.enabled:
		return
	event = frappe.get_doc(EVENT, delivery.sync_event)

	outcome, error = _post(destination, event)
	delivery.attempt_count = (delivery.attempt_count or 0) + 1
	delivery.last_error = error
	if outcome == _DELIVERED:
		delivery.status = "Delivered"
		delivery.delivered_at = now_datetime()
	elif outcome == _RETRY and delivery.attempt_count <= (destination.max_retry_attempts or 0):
		delivery.status = "Failed"
		delay = BACKOFF_SECONDS[min(delivery.attempt_count, len(BACKOFF_SECONDS)) - 1]
		delivery.next_attempt_at = add_to_date(now_datetime(), seconds=delay)
	else:
		delivery.status = "Dead-lettered"
	delivery.save(ignore_permissions=True)

	if delivery.status == "Dead-lettered":
		alerts.notify_dead_letter(delivery, event, destination)


def _post(destination, event):
	"""Returns (outcome, short error | None). The error never carries a credential, the
	Authorization header or a traceback."""
	headers = {"Content-Type": _FHIR_JSON, "Accept": _FHIR_JSON}
	if destination.event_id_header_name:
		headers[destination.event_id_header_name] = event.event_id
	if destination.correlation_id_header_name and event.correlation_id:
		headers[destination.correlation_id_header_name] = event.correlation_id
	for row in destination.extra_headers:
		headers[row.header_name] = row.header_value

	try:
		request_kwargs = auth.apply_auth(
			destination,
			{
				"data": event.fhir_payload.encode(),
				"headers": headers,
				"timeout": destination.timeout_seconds or 10,
				"allow_redirects": False,
			},
		)
	except auth.UnsupportedAuthType:
		return _REJECTED, f"Unsupported auth type {destination.auth_type}"

	url = f"{destination.base_url.rstrip('/')}/{event.resource_type}"
	try:
		response = requests.post(url, **request_kwargs)
	except requests.RequestException as e:
		# timeout / connection refused / DNS: no answer at all, so worth retrying
		return _RETRY, type(e).__name__
	code = response.status_code
	if 200 <= code < 300:
		return _DELIVERED, None
	error = f"HTTP {code} {response.reason or ''}".strip()
	return (_RETRY if code == 429 or code >= 500 else _REJECTED), error

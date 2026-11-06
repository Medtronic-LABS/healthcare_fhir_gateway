"""Delivers one `FHIR Sync Delivery` to its destination. `attempt_delivery` is the single code path
for both the near-real-time job enqueued after submit and the 5-minute scheduler sweep.

Outcome per attempt:
- 2xx -> Delivered.
- 429 / 5xx / timeout / connection error -> Failed, retried after BACKOFF_SECONDS[attempt-1];
  once retries exceed the destination's max_retry_attempts -> Dead-lettered.
- anything else (4xx, unexpected 3xx, a request that can't even be built) -> Dead-lettered at
  once: resending an identical request that was rejected won't change the answer.

An attempt first *claims* the row in its own short transaction: it counts the attempt and pushes
`next_attempt_at` out by LEASE_SECONDS, then commits. No lock is held during the HTTP call. A second
job for the same row (immediate job vs. sweep) sees a future `next_attempt_at` and backs off; a job
killed mid-call (RQ timeout, worker crash) leaves the row to be re-picked once the lease expires,
and because the attempt was already counted, a row that keeps dying is eventually dead-lettered
rather than retried forever. The destination is still expected to be idempotent on the event-id
header for whatever slips through."""

import frappe
import requests
from frappe.utils import add_to_date, get_datetime, now_datetime
from frappe.utils.background_jobs import MAX_QUEUED_JOBS, get_queue

from fhir_gateway.sync import DELIVERY, DESTINATION, EVENT, alerts, auth

# 1m, 5m, 30m, 2h, 6h, 24h -- one step per retry, matching the default max_retry_attempts = 6.
BACKOFF_SECONDS = [60, 300, 1800, 7200, 21600, 86400]
# Longer than the `short` queue's job timeout (300s), so a lease outlives any live attempt.
LEASE_SECONDS = 600
# Retries share the site-wide `short` queue: take a small slice per tick and leave room for every
# other app's jobs (Frappe starts refusing enqueues at MAX_QUEUED_JOBS).
SWEEP_BATCH_SIZE = 50
_QUEUE = "short"
_QUEUE_HEADROOM = MAX_QUEUED_JOBS // 2
_RETRYABLE_STATUSES = ("Pending", "Failed")
_FHIR_JSON = "application/fhir+json"

# _post outcomes
_DELIVERED, _RETRY, _REJECTED = "delivered", "retry", "rejected"

# Raised by requests before anything is sent: a configuration error no retry can fix.
_UNSENDABLE = (
	requests.exceptions.InvalidURL,
	requests.exceptions.InvalidHeader,
	requests.exceptions.InvalidSchema,
	requests.exceptions.MissingSchema,
)


def enqueue_attempt(delivery_name):
	"""Best-effort: returns False (row left for the sweep) when the queue is near full or
	unreachable, instead of letting frappe.enqueue throw at the caller."""
	try:
		if get_queue(_QUEUE).count >= _QUEUE_HEADROOM:
			return False
		frappe.enqueue(
			"fhir_gateway.sync.dispatcher.attempt_delivery",
			queue=_QUEUE,
			job_id=f"fhir_sync_delivery::{delivery_name}",
			deduplicate=True,
			delivery_name=delivery_name,
		)
		return True
	except Exception:
		# Deliberately broad (Redis down, queue overloaded): every row this skips is already due, so
		# the next sweep retries it. Logged to file, not Error Log -- this may run after the
		# request's final commit.
		frappe.logger("fhir_gateway").warning(f"could not enqueue FHIR sync delivery {delivery_name}")
		return False


def sweep_due_deliveries():
	"""Scheduler safety net: re-enqueue retryable deliveries whose time has come, oldest first, for
	enabled destinations only (a disabled one's rows wait, untouched, until it's re-enabled)."""
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
		if not enqueue_attempt(name):
			break


def attempt_delivery(delivery_name):
	claim = _claim(delivery_name)
	if not claim:
		return
	delivery, destination, event = claim
	if delivery.attempt_count > (destination.max_retry_attempts or 0) + 1:
		# Every allowed attempt was already started; the last one(s) never finished.
		status = _record(delivery, destination, _REJECTED, "Attempt did not complete")
	else:
		outcome, error = _post(destination, event)
		status = _record(delivery, destination, outcome, error)
	frappe.db.commit()
	if status == "Dead-lettered":
		alerts.notify_dead_letter(frappe.get_doc(DELIVERY, delivery_name), event, destination)


def _claim(delivery_name):
	"""Count the attempt and lease the row, committed before any network I/O. None when there is
	nothing to do: row gone, already final, leased by another job, or its destination disabled."""
	if not frappe.db.exists(DELIVERY, delivery_name):
		return None
	delivery = frappe.get_doc(DELIVERY, delivery_name, for_update=True)
	now = now_datetime()
	leased = delivery.next_attempt_at and get_datetime(delivery.next_attempt_at) > now
	if delivery.status not in _RETRYABLE_STATUSES or leased:
		return None
	destination = frappe.get_doc(DESTINATION, delivery.destination)
	if not destination.enabled:
		return None
	delivery.attempt_count = (delivery.attempt_count or 0) + 1
	frappe.db.set_value(
		DELIVERY,
		delivery_name,
		{"attempt_count": delivery.attempt_count, "next_attempt_at": add_to_date(now, seconds=LEASE_SECONDS)},
		update_modified=False,
	)
	frappe.db.commit()
	return delivery, destination, frappe.get_doc(EVENT, delivery.sync_event)


def _record(delivery, destination, outcome, error):
	values = {"last_error": error}
	if outcome == _DELIVERED:
		values.update(status="Delivered", delivered_at=now_datetime(), next_attempt_at=None)
	elif outcome == _RETRY and delivery.attempt_count <= (destination.max_retry_attempts or 0):
		delay = BACKOFF_SECONDS[min(delivery.attempt_count, len(BACKOFF_SECONDS)) - 1]
		values.update(status="Failed", next_attempt_at=add_to_date(now_datetime(), seconds=delay))
	else:
		values.update(status="Dead-lettered", next_attempt_at=None)
	frappe.db.set_value(DELIVERY, delivery.name, values)
	return values["status"]


def _post(destination, event):
	"""Returns (outcome, short error | None). The error never carries a credential, the
	Authorization header, a URL or a traceback."""
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
	except _UNSENDABLE as e:
		return _REJECTED, type(e).__name__
	except requests.RequestException as e:
		# timeout / connection refused / DNS: no answer at all, so worth retrying
		return _RETRY, type(e).__name__
	except Exception as e:
		# Anything else (e.g. UnicodeEncodeError from a non-latin-1 header value) is a request that
		# can never be sent as configured. Classify it rather than let the job die and the row be
		# re-picked forever.
		return _REJECTED, type(e).__name__
	code = response.status_code
	if 200 <= code < 300:
		return _DELIVERED, None
	error = f"HTTP {code} {response.reason or ''}".strip()
	return (_RETRY if code == 429 or code >= 500 else _REJECTED), error

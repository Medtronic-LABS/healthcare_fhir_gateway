"""Writes the outbox: one `FHIR Sync Event` per mapped resource plus one `FHIR Sync Delivery` per
enabled destination that takes that resource type -- in the caller's transaction, so a submit that
commits has its event queued. Destinations are matched as of now: one added later never sees a
backfill.

An event no destination wants is not stored at all: it would be a PHI-bearing payload with nothing
to deliver it to."""

import uuid
from functools import partial

import frappe
from frappe.utils import now_datetime

from fhir_gateway.sync import DELIVERY, DESTINATION, EVENT, dispatcher


def destinations_by_type(resource_types):
	"""{resource_type: [destination names]} for enabled destinations -- resolved once per submit,
	not once per event."""
	enabled = frappe.get_all(DESTINATION, filters={"enabled": 1}, pluck="name")
	if not enabled:
		return {}
	rows = frappe.get_all(
		"FHIR Sync Destination Resource Type",
		filters={
			"parenttype": DESTINATION,
			"parent": ["in", enabled],
			"resource_type": ["in", list(set(resource_types))],
		},
		fields=["parent", "resource_type"],
	)
	by_type = {}
	for row in rows:
		by_type.setdefault(row.resource_type, set()).add(row.parent)
	return {resource_type: sorted(names) for resource_type, names in by_type.items()}


def enqueue_sync_event(resource_type, payload, source_doctype, source_name, correlation_id, destinations):
	"""Writes the Event and one Delivery per name in `destinations`; returns the Delivery names
	([] -- and nothing written -- when `destinations` is empty). The caller schedules them with
	`schedule_deliveries` once everything it is writing has succeeded."""
	if not destinations:
		return []

	event_id = str(uuid.uuid4())
	frappe.get_doc(
		{
			"doctype": EVENT,
			"event_id": event_id,
			"correlation_id": correlation_id,
			"resource_type": resource_type,
			"source_doctype": source_doctype,
			"source_name": source_name,
			"fhir_payload": frappe.as_json(payload, indent=None),
		}
	).insert(ignore_permissions=True)
	return [_insert_delivery(event_id, destination) for destination in destinations]


def _insert_delivery(event_name, destination):
	doc = frappe.get_doc(
		{
			"doctype": DELIVERY,
			"sync_event": event_name,
			"destination": destination,
			"status": "Pending",
			"next_attempt_at": now_datetime(),
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def schedule_deliveries(delivery_names):
	"""Near-real-time first attempt, once the submit's transaction commits. Registered as our own
	after-commit callback rather than `enqueue(enqueue_after_commit=True)`: Frappe runs those
	callbacks unguarded inside commit(), and a queue error there would reach the clinician after
	their record was already saved. `enqueue_attempt` never raises; anything it skips is already
	due, so the scheduler sweep picks it up."""
	for name in delivery_names:
		frappe.db.after_commit.add(partial(dispatcher.enqueue_attempt, name))

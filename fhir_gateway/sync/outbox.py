"""Writes the outbox: one `FHIR Sync Event` per mapped resource plus one `FHIR Sync Delivery` per
enabled destination that takes that resource type -- in the caller's transaction, so a submit that
commits has its event queued. Destinations are matched as of now: one added later never sees a
backfill.

An event no destination wants is not stored at all: it would be a PHI-bearing payload with nothing
to deliver it to."""

import uuid

import frappe
from frappe.utils import now_datetime

from fhir_gateway.sync import DELIVERY, DESTINATION, EVENT, dispatcher


def new_correlation_id():
	return str(uuid.uuid4())


def destinations_for(resource_type):
	return frappe.get_all(
		DESTINATION,
		filters=[
			[DESTINATION, "enabled", "=", 1],
			["FHIR Sync Destination Resource Type", "resource_type", "=", resource_type],
		],
		pluck="name",
		distinct=True,
	)


def enqueue_sync_event(resource_type, payload, source_doctype, source_name, correlation_id):
	"""Returns the names of the Deliveries created ([] when no destination takes this type). The
	caller schedules them with `schedule_deliveries` once everything it is writing has succeeded."""
	destinations = destinations_for(resource_type)
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
	"""Near-real-time first attempt, once the submit's transaction commits. If a job is lost (worker
	down, Redis flushed) the scheduler sweep picks the row up anyway -- `next_attempt_at` is already
	due."""
	for name in delivery_names:
		dispatcher.enqueue_attempt(name, enqueue_after_commit=True)

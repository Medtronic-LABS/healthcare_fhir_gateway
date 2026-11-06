"""Daily purge of the outbox: an event is deleted (with its deliveries) once every one of its
deliveries is Delivered and it is older than `FHIR Sync Settings.retention_days`. Anything with a
Dead-lettered, Failed or Pending delivery is kept -- dead letters are the audit trail of what never
arrived, so they are never auto-purged."""

import frappe
from frappe.utils import add_days, now_datetime

from fhir_gateway.sync import DELIVERY, EVENT

DEFAULT_RETENTION_DAYS = 30
BATCH_SIZE = 1000


def purge_delivered_events():
	"""Returns the number of events purged."""
	cutoff = add_days(now_datetime(), -_retention_days())
	purged = 0
	while names := _purgeable_batch(cutoff):
		frappe.db.delete(DELIVERY, {"sync_event": ["in", names]})
		frappe.db.delete(EVENT, {"name": ["in", names]})
		frappe.db.commit()
		purged += len(names)
		if len(names) < BATCH_SIZE:
			break
	return purged


def _retention_days():
	# An emptied Int field reads as 0; treat that as "unset", not "purge immediately".
	days = frappe.db.get_single_value("FHIR Sync Settings", "retention_days")
	return days if days and days > 0 else DEFAULT_RETENTION_DAYS


def _purgeable_batch(cutoff):
	# Kept events are excluded in the query itself, so a backlog of dead letters can never fill
	# every batch and starve the purge.
	event = frappe.qb.DocType(EVENT)
	delivery = frappe.qb.DocType(DELIVERY)
	not_delivered = (
		frappe.qb.from_(delivery).select(delivery.sync_event).where(delivery.status != "Delivered")
	)
	return (
		frappe.qb.from_(event)
		.select(event.name)
		.where(event.creation < cutoff)
		.where(event.name.notin(not_delivered))
		.orderby(event.creation)
		.limit(BATCH_SIZE)
		.run(pluck=True)
	)

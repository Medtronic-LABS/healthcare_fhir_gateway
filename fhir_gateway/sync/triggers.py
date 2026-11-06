"""`doc_events.on_submit` entry point: map the submitted record to FHIR and write it to the outbox.

Load-bearing rule: a failure in here must never block the clinical submit it is attached to. The
savepoint is taken first, so mapping (which reads the DB) and outbox writes share one rollback
point -- on Postgres any failed statement aborts the transaction until rolled back to it. Any
exception rolls back to the savepoint -- a submit leaves either every event it should, or none --
and is logged, never re-raised. Delivery jobs are scheduled only after every write succeeded,
because a savepoint rollback does not cancel after-commit callbacks.

The one exception that is re-raised: a deadlock or lock-wait timeout. The database has then already
rolled back the *whole* transaction, the clinical submit's own writes and our savepoint included, so
swallowing it would let the request carry on as if the record were submitted when it is not."""

import uuid
from typing import NamedTuple

import frappe

from fhir_gateway.mappers import condition, encounter, medication_request, observation, service_request
from fhir_gateway.sync import outbox

_SAVEPOINT = "fhir_gateway_sync"
_ENCOUNTER = "Patient Encounter"


class SyncEvent(NamedTuple):
	resource_type: str
	payload: dict
	source_doctype: str
	source_name: str


def _patient_encounter(doc):
	events = [SyncEvent(encounter.RESOURCE_TYPE, encounter.to_fhir(doc), doc.doctype, doc.name)]
	# Diagnoses are child rows with no doc_events of their own, so their Conditions ride on the
	# encounter's submit; the row is the Condition's source (its name is the resource id).
	events += [
		SyncEvent(condition.RESOURCE_TYPE, condition.to_fhir(doc, row), row.doctype, row.name)
		for row in doc.diagnosis
	]
	return events


def _single(mapper):
	return lambda doc: [SyncEvent(mapper.RESOURCE_TYPE, mapper.to_fhir(doc), doc.doctype, doc.name)]


_BUILDERS = {
	_ENCOUNTER: _patient_encounter,
	"Observation": _single(observation),
	"Vital Signs": _single(observation),
	"Medication Request": _single(medication_request),
	"Service Request": _single(service_request),
}


def build_events(doc):
	return _BUILDERS[doc.doctype](doc)


def _encounter_of(doc):
	"""The Patient Encounter a record belongs to, if any."""
	if doc.doctype == _ENCOUNTER:
		return doc.name
	if doc.doctype == "Medication Request":
		return doc.get("order_group")  # Link to Patient Encounter
	if doc.doctype == "Service Request" and doc.get("order_group_doctype") == _ENCOUNTER:
		return doc.get("order_group")
	if doc.doctype == "Vital Signs":
		return doc.get("encounter")
	if doc.doctype == "Observation" and doc.get("reference_doctype") == _ENCOUNTER:
		return doc.get("reference_docname")
	return None


def correlation_id_for(doc):
	"""Records from one encounter share a correlation id, so a destination can join them. Healthcare
	submits an encounter's Service/Medication Requests from inside the encounter's own on_submit --
	each fires this hook separately, before the encounter's -- so the id is derived from the
	encounter's name rather than carried in request state."""
	encounter_name = _encounter_of(doc)
	if not encounter_name:
		return str(uuid.uuid4())
	return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{frappe.local.site}/{_ENCOUNTER}/{encounter_name}"))


def on_submit(doc, method=None):
	try:
		frappe.db.savepoint(_SAVEPOINT)
		try:
			deliveries = _write_outbox(doc)
		except Exception as e:
			if _transaction_lost(e):
				raise
			frappe.db.rollback(save_point=_SAVEPOINT)
			raise
		frappe.db.release_savepoint(_SAVEPOINT)
		outbox.schedule_deliveries(deliveries)
	except Exception as e:
		if _transaction_lost(e):
			raise
		# Deliberately broad otherwise: any bug in the sync path is logged (with traceback) and
		# swallowed so the clinical record still submits. See module docstring.
		frappe.log_error(
			title=f"FHIR sync enqueue failed for {doc.doctype}",
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)


def _write_outbox(doc):
	events = build_events(doc)
	if not events:
		return []
	correlation_id = correlation_id_for(doc)
	destinations = outbox.destinations_by_type([e.resource_type for e in events])
	deliveries = []
	for event in events:
		deliveries += outbox.enqueue_sync_event(
			*event, correlation_id=correlation_id, destinations=destinations.get(event.resource_type)
		)
	return deliveries


def _transaction_lost(e):
	return frappe.db.is_deadlocked(e) or frappe.db.is_timedout(e)

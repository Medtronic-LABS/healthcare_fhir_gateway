"""`doc_events.on_submit` entry point: map the submitted record to FHIR and write it to the outbox.

Load-bearing rule: a failure anywhere in here must never block the clinical submit it is attached
to. All mapping happens first (no DB writes), then all outbox rows are written under one savepoint;
any exception rolls back to it -- so a submit leaves either every event it should, or none -- and is
logged, never re-raised. Delivery jobs are scheduled only after every write succeeded, because a
savepoint rollback does not cancel after-commit callbacks."""

from typing import NamedTuple

import frappe

from fhir_gateway.mappers import condition, encounter, medication_request, observation, service_request
from fhir_gateway.sync import outbox

_SAVEPOINT = "fhir_gateway_sync"


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
	"Patient Encounter": _patient_encounter,
	"Observation": _single(observation),
	"Vital Signs": _single(observation),
	"Medication Request": _single(medication_request),
	"Service Request": _single(service_request),
}


def build_events(doc):
	return _BUILDERS[doc.doctype](doc)


def on_submit(doc, method=None):
	try:
		events = build_events(doc)
		correlation_id = outbox.new_correlation_id()
		frappe.db.savepoint(_SAVEPOINT)
		try:
			deliveries = []
			for event in events:
				deliveries += outbox.enqueue_sync_event(*event, correlation_id=correlation_id)
		except Exception:
			frappe.db.rollback(save_point=_SAVEPOINT)
			raise
		frappe.db.release_savepoint(_SAVEPOINT)
		outbox.schedule_deliveries(deliveries)
	except Exception:
		# Deliberately broad: any bug in the sync path is logged (with traceback) and swallowed so
		# the clinical record still submits. See module docstring.
		frappe.log_error(
			title=f"FHIR sync enqueue failed for {doc.doctype}",
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)

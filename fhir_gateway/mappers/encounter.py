"""`healthcare` `Patient Encounter` -> FHIR `Encounter`. Read-only (egress) for this MVP."""

from functools import partial

from fhir_gateway.mappers._shared import (
	as_str_datetime,
	codification_to_coding,
	combine_date_time,
	get_doc,
	search_docs,
	strip_fhir_date_prefix,
	strip_reference_prefix,
)

DOCTYPE = "Patient Encounter"
RESOURCE_TYPE = "Encounter"

SEARCH_PARAMS = {
	"patient": "reference",
	"_lastUpdated": "date",
}

# Patient Encounter.status (plain Select: Open/Ordered/Completed/Cancelled) isn't drawn from a
# Code Value/FHIR ValueSet the way Medication Request/Service Request's status fields are -- this
# app's own translation into FHIR's Encounter.status vocabulary.
_STATUS_MAP = {
	"Open": "in-progress",
	"Ordered": "in-progress",
	"Completed": "finished",
	"Cancelled": "cancelled",
}


def to_fhir(doc):
	resource = {
		"resourceType": RESOURCE_TYPE,
		"id": doc.name,
		"status": _STATUS_MAP.get(doc.status, "unknown"),
		"class": {
			"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
			"code": "AMB",
			"display": "ambulatory",
		},
		"subject": {"reference": f"Patient/{doc.patient}"},
	}
	if doc.appointment_type:
		resource["type"] = [{"text": doc.appointment_type}]
	if doc.practitioner:
		participant = {"individual": {"reference": f"Practitioner/{doc.practitioner}"}}
		if doc.practitioner_name:
			participant["type"] = [{"text": doc.practitioner_name}]
		resource["participant"] = [participant]
	if doc.appointment:
		resource["appointment"] = [{"reference": f"Appointment/{doc.appointment}"}]
	start = combine_date_time(doc.encounter_date, doc.encounter_time)
	if start:
		resource["period"] = {"start": start}
	coding = codification_to_coding(doc.codification_table)
	if coding:
		resource["reasonCode"] = [{"coding": coding}]
	if doc.modified:
		resource["meta"] = {"lastUpdated": as_str_datetime(doc.modified)}
	return resource


def build_filters(args):
	filters = {}
	if args.get("patient"):
		filters["patient"] = strip_reference_prefix(args["patient"], "Patient")
	if args.get("_lastUpdated"):
		filters["modified"] = (">=", strip_fhir_date_prefix(args["_lastUpdated"]))
	return filters


get = partial(get_doc, DOCTYPE)
search = partial(search_docs, DOCTYPE, to_fhir)

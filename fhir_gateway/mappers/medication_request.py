"""`healthcare` `Medication Request` -> FHIR `MedicationRequest`. Read-only (egress).

`status`/`intent`/`priority` are already `Code Value` links seeded from FHIR's own ValueSets (see
the FHIR deep dive earlier in this project) -- `code_value_string` resolves the Link to the real
FHIR vocabulary string rather than the Code Value doc's composite name."""

from functools import partial

from fhir_gateway.mappers._shared import (
	as_str_datetime,
	code_value_string,
	codification_to_coding,
	combine_date_time,
	fhir_enum_code,
	get_doc,
	search_docs,
	strip_fhir_date_prefix,
	strip_reference_prefix,
)

DOCTYPE = "Medication Request"
RESOURCE_TYPE = "MedicationRequest"

SEARCH_PARAMS = {
	"patient": "reference",
	"_lastUpdated": "date",
}


def to_fhir(doc):
	resource = {
		"resourceType": RESOURCE_TYPE,
		"id": doc.name,
		"status": fhir_enum_code(code_value_string(doc.status)) or "unknown",
		"intent": fhir_enum_code(code_value_string(doc.intent)) or "order",
		"subject": {"reference": f"Patient/{doc.patient}"},
	}
	if doc.priority:
		resource["priority"] = fhir_enum_code(code_value_string(doc.priority)) or "routine"

	coding = codification_to_coding(doc.codification_table)
	med_text = doc.medication or doc.item_code or doc.name
	resource["medicationCodeableConcept"] = (
		{"coding": coding, "text": med_text} if coding else {"text": med_text}
	)

	if doc.practitioner:
		resource["requester"] = {"reference": f"Practitioner/{doc.practitioner}"}
	if doc.order_date:
		resource["authoredOn"] = combine_date_time(doc.order_date, doc.order_time)
	dosage_instruction = {}
	if doc.dosage_form:
		dosage_instruction.setdefault("method", {})["text"] = doc.dosage_form
	if doc.dosage:
		dosage_instruction["text"] = doc.dosage
	if dosage_instruction:
		resource["dosageInstruction"] = [dosage_instruction]
	# `quantity` on this doctype is the total amount ordered (dispensed), not a per-dose amount --
	# FHIR's dispenseRequest.quantity, not dosageInstruction.doseAndRate.
	if doc.quantity:
		resource["dispenseRequest"] = {"quantity": {"value": doc.quantity}}
	if doc.order_group:
		resource["encounter"] = {"reference": f"Encounter/{doc.order_group}"}
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

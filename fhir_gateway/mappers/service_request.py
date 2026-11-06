"""`healthcare` `Service Request` -> FHIR `ServiceRequest`. Push-only for now (outbound sync); not
registered in router.SUPPORTED_RESOURCES, so the read API is unchanged.

`status`/`intent`/`priority` are `Code Value` links, like Medication Request's -- "Request Status"
values are already FHIR codes, Intent/Priority need `fhir_enum_code`'s casing fix. What was ordered
is a template (`template_dt`/`template_dn`: a Lab Test, Observation or Clinical Procedure Template),
so `code.text` is the template's name and `code.coding` comes from the request's own codification
table when present."""

from fhir_gateway.mappers._shared import (
	as_str_datetime,
	code_value_string,
	codification_to_coding,
	combine_date_time,
	fhir_enum_code,
)

DOCTYPE = "Service Request"
RESOURCE_TYPE = "ServiceRequest"


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
	text = doc.template_dn or doc.name
	resource["code"] = {"coding": coding, "text": text} if coding else {"text": text}

	if doc.practitioner:
		resource["requester"] = {"reference": f"Practitioner/{doc.practitioner}"}
	authored_on = combine_date_time(doc.order_date, doc.order_time)
	if authored_on:
		resource["authoredOn"] = authored_on
	# `order_group` is a Dynamic Link -- only a Patient Encounter maps to FHIR's `encounter`.
	if doc.order_group and doc.order_group_doctype == "Patient Encounter":
		resource["encounter"] = {"reference": f"Encounter/{doc.order_group}"}
	if doc.order_description:
		resource["note"] = [{"text": doc.order_description}]
	if doc.modified:
		resource["meta"] = {"lastUpdated": as_str_datetime(doc.modified)}
	return resource

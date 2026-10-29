"""`healthcare` `Patient Encounter Diagnosis` row -> FHIR `Condition` (category
`encounter-diagnosis`). Push-only for now (outbound sync); not registered in
router.SUPPORTED_RESOURCES.

A diagnosis isn't its own document in Healthcare -- it's a child row on the encounter, pointing at a
`Diagnosis` master record -- so this mapper takes both: the encounter supplies subject, recorder and
date; the row supplies a stable `id` (its own row name) and the diagnosis; the master record supplies
the coding. A diagnosis recorded on a submitted encounter is taken as clinically active and
confirmed: Healthcare has no per-diagnosis status to read either from."""

import frappe

from fhir_gateway.mappers._shared import codification_to_coding, combine_date_time

DIAGNOSIS_DOCTYPE = "Diagnosis"
RESOURCE_TYPE = "Condition"


def to_fhir(encounter, diagnosis_row):
	resource = {
		"resourceType": RESOURCE_TYPE,
		"id": diagnosis_row.name,
		"clinicalStatus": {
			"coding": [
				{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}
			]
		},
		"verificationStatus": {
			"coding": [
				{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}
			]
		},
		"category": [
			{
				"coding": [
					{
						"system": "http://terminology.hl7.org/CodeSystem/condition-category",
						"code": "encounter-diagnosis",
					}
				]
			}
		],
		"code": _code(diagnosis_row.diagnosis),
		"subject": {"reference": f"Patient/{encounter.patient}"},
		"encounter": {"reference": f"Encounter/{encounter.name}"},
	}
	if encounter.practitioner:
		resource["recorder"] = {"reference": f"Practitioner/{encounter.practitioner}"}
	recorded = combine_date_time(encounter.encounter_date, encounter.encounter_time)
	if recorded:
		resource["recordedDate"] = recorded
	return resource


def _code(diagnosis_name):
	coding = []
	if diagnosis_name and frappe.db.exists(DIAGNOSIS_DOCTYPE, diagnosis_name):
		coding = codification_to_coding(
			frappe.get_cached_doc(DIAGNOSIS_DOCTYPE, diagnosis_name).codification_table
		)
	return {"coding": coding, "text": diagnosis_name} if coding else {"text": diagnosis_name}

"""`healthcare` `Vital Signs` -> FHIR `Observation` (category `vital-signs`).

One Vital Signs row holds several distinct measurements (temperature, pulse, respiratory rate,
blood pressure, height, weight, BMI) in one record -- FHIR models exactly this as a single
"vital signs panel" Observation with one `component` per measurement (LOINC 85353-1), rather than
one Observation per reading. `observation.py` is the public resourceType surface; this module only
builds the resource for a Vital Signs source row.
"""

from fhir_gateway.mappers._shared import as_float, as_str_datetime, combine_date_time

DOCTYPE = "Vital Signs"

# (fieldname, LOINC code, display, unit) -- matches the LOINC codes used in
# spice_next_core/api/fhir.py's own blood-pressure composite for exactly the same reason: this is
# the standard LOINC vital-signs panel, not invented here.
#
# `temperature`'s unit is a real gap, not an assumption glossed over: Vital Signs.temperature is a
# plain Data field with no unit metadata anywhere in healthcare's own schema or settings (checked
# both) -- whatever a user types is unitless as far as the doctype is concerned. This site's own
# demo data is unambiguously Fahrenheit (e.g. "98.4"), so that's what's declared below, but this is
# a per-deployment assumption, not a fact this gateway can verify from the data model itself. A
# Celsius-convention Healthcare install would need this overridden.
_TEMPERATURE_UNIT = "[degF]"
_COMPONENTS = [
	("temperature", "8310-5", "Body temperature", _TEMPERATURE_UNIT),
	("pulse", "8867-4", "Heart rate", "/min"),
	("respiratory_rate", "9279-1", "Respiratory rate", "/min"),
	("bp_systolic", "8480-6", "Systolic blood pressure", "mm[Hg]"),
	("bp_diastolic", "8462-4", "Diastolic blood pressure", "mm[Hg]"),
	("height", "8302-2", "Body height", "m"),
	("weight", "29463-7", "Body weight", "kg"),
	("bmi", "39156-5", "Body mass index", "kg/m2"),
]

# `height`/`weight`/`bmi` are Frappe `Float` fields, which default to 0.0 when never entered
# (unlike the other components here, which are `Data` fields defaulting to "" -- already filtered
# by as_float()'s None/"" check). A genuine 0.0 reading is never clinically meaningful for any of
# these three, so it's treated the same as "not measured" rather than emitted as a real component.
_NEVER_ZERO = {"height", "weight", "bmi"}


def to_fhir(doc):
	components = []
	for fieldname, code, display, unit in _COMPONENTS:
		value = as_float(doc.get(fieldname))
		if value is None or (fieldname in _NEVER_ZERO and value == 0):
			continue
		components.append(
			{
				"code": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}]},
				"valueQuantity": {"value": value, "unit": unit},
			}
		)

	resource = {
		"resourceType": "Observation",
		"id": doc.name,
		"status": "final",
		"category": [
			{
				"coding": [
					{
						"system": "http://terminology.hl7.org/CodeSystem/observation-category",
						"code": "vital-signs",
					}
				]
			}
		],
		"code": {
			"coding": [{"system": "http://loinc.org", "code": "85353-1", "display": "Vital signs panel"}]
		},
		"subject": {"reference": f"Patient/{doc.patient}"},
	}
	effective = combine_date_time(doc.signs_date, doc.signs_time)
	if effective:
		resource["effectiveDateTime"] = effective
	if doc.encounter:
		resource["encounter"] = {"reference": f"Encounter/{doc.encounter}"}
	if components:
		resource["component"] = components
	if doc.modified:
		resource["meta"] = {"lastUpdated": as_str_datetime(doc.modified)}
	return resource

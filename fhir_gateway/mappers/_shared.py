"""Helpers shared by the resource mappers."""

import frappe


def strip_reference_prefix(value, resource_type):
	prefix = f"{resource_type}/"
	return value[len(prefix) :] if value.startswith(prefix) else value


def strip_fhir_date_prefix(value):
	for prefix in ("gt", "ge", "lt", "le", "eq"):
		if value.startswith(prefix):
			return value[len(prefix) :]
	return value


def code_value_string(code_value_name):
	"""Resolve a `Code Value` Link field's value to the actual FHIR vocabulary string.

	The Link's stored value is the Code Value doc's *name*, a composite key like
	"active-Medication Request Status" -- not the FHIR string itself. The real value (e.g.
	"active") lives in that doc's own `code_value` field. Confirmed empirically: `Medication
	Request.status` already holds real Code Value names seeded from FHIR's own ValueSets (see the
	FHIR deep dive earlier in this project), so this is a straight lookup, not a translation."""
	if not code_value_name:
		return None
	return frappe.db.get_value("Code Value", code_value_name, "code_value")


def fhir_enum_code(value):
	"""Normalize a resolved Code Value string into FHIR's exact code casing.

	Verified against the live data: `healthcare`'s "Medication Request Status" and "Request Status"
	Code Value entries are already correctly lowercase-hyphenated ("active", "entered-in-error"),
	but its "Intent" and
	"Priority" Code Systems -- despite being correctly flagged `is_fhir_defined` with the right
	canonical `uri` -- store Title-Case display labels as `code_value` ("Original Order", "Routine"),
	not the actual FHIR enum code ("original-order", "routine"). Lowercasing + dashing spaces is a
	safe, lossless fix here because this label *is* the FHIR code's display form, and is a no-op on
	already-correct values like "active"."""
	if not value:
		return None
	return value.lower().replace(" ", "-")


def get_doc(doctype, name):
	"""Shared `read` implementation every mapper's `get()` delegates to."""
	if not frappe.db.exists(doctype, name):
		return None
	return frappe.get_doc(doctype, name)


def search_docs(doctype, to_fhir, filters, limit, offset):
	"""Shared `search-type` implementation every mapper's `search()` delegates to: count + one
	page of names + map each to FHIR. Pushes LIMIT/OFFSET to SQL rather than fetching everything
	and slicing in Python."""
	total = frappe.db.count(doctype, filters=filters)
	names = frappe.get_all(
		doctype,
		filters=filters,
		pluck="name",
		order_by="modified desc",
		limit_start=offset,
		limit_page_length=limit,
	)
	return [to_fhir(frappe.get_doc(doctype, n)) for n in names], total


def codification_to_coding(codification_table):
	"""`healthcare`'s `Codification Table` child rows already carry system/code/display --
	structurally identical to FHIR's `CodeableConcept.coding[]`. See the FHIR deep-dive earlier in
	this project: this is the data-model alignment that makes these mappers mostly field copies."""
	coding = []
	for row in codification_table or []:
		system = row.system or (f"urn:oid:{row.oid}" if row.oid else None)
		if not row.code and not row.display:
			continue
		entry = {}
		if system:
			entry["system"] = system
		if row.code:
			entry["code"] = row.code
		if row.display:
			entry["display"] = row.display
		coding.append(entry)
	return coding


def as_float(value):
	if value in (None, ""):
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def as_str_datetime(value):
	"""Frappe datetime/date fields come back as datetime.datetime/date objects; FHIR wants an
	ISO-8601 string. `str()` on either already gives the right shape (`YYYY-MM-DD` or
	`YYYY-MM-DD HH:MM:SS`) -- just needs the space swapped for a `T` to be a valid FHIR instant."""
	if not value:
		return None
	return str(value).replace(" ", "T")


def combine_date_time(date_val, time_val):
	"""Frappe keeps a record's date and time in two fields (e.g. `encounter_date`/`encounter_time`);
	FHIR wants one dateTime. Falls back to the bare date when no time was recorded."""
	if not date_val:
		return None
	if time_val:
		return f"{date_val}T{time_val}"
	return str(date_val)

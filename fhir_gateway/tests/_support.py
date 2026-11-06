"""Shared test helpers used across fhir_gateway's test modules. Not itself a test module -- Frappe's
test runner only discovers test_*.py files, so this is never collected or run directly."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import frappe


def ensure_gender(value):
	"""Idempotently ensure a Gender record exists. No app ships a single Gender record as a fixture
	(confirmed: not frappe, erpnext, or healthcare) -- a long-lived dev site accumulates them
	organically (e.g. the first Patient or Practitioner ever created through Desk), but a fresh CI
	site has none at all. Any test that sets a sex/gender Link value must seed it first, or insert
	fails with LinkValidationError."""
	if value and not frappe.db.exists("Gender", value):
		frappe.get_doc({"doctype": "Gender", "gender": value}).insert(ignore_permissions=True)


def make_patient(**fields):
	# invite_user=0: healthcare's own Patient controller auto-creates a linked User when `email` is
	# set unless this is explicitly off -- a side effect most callers have no interest in and that
	# would otherwise leak a User record their tearDown doesn't know to clean up.
	values = {"doctype": "Patient", "sex": "Male", "first_name": "Test", "invite_user": 0}
	values.update(fields)
	ensure_gender(values.get("sex"))
	doc = frappe.get_doc(values)
	doc.insert(ignore_permissions=True)
	return doc


def make_practitioner(**fields):
	values = {"doctype": "Healthcare Practitioner", "first_name": "Test", "status": "Active"}
	values.update(fields)
	ensure_gender(values.get("gender"))
	doc = frappe.get_doc(values)
	doc.insert(ignore_permissions=True)
	return doc


def ensure_code_system(name, uri):
	"""Idempotently ensure a Code System exists; returns True if this call created it. Healthcare
	seeds its Code Systems (ICD-10, LOINC, ...) only through its setup wizard, never run on a
	headless CI site, so a codification test must seed the one it links to."""
	if frappe.db.exists("Code System", name):
		return False
	frappe.get_doc({"doctype": "Code System", "code_system": name, "uri": uri}).insert(
		ignore_permissions=True
	)
	return True


def ensure_any(doctype, creator):
	"""Return the name of any existing record of `doctype`, or create one via `creator()` if none
	exist. For reference/lookup data a test incidentally depends on (Company, Appointment Type,
	Healthcare Practitioner) to satisfy a mandatory Link field -- the test doesn't care which one,
	the same way a long-lived dev site always happens to have at least one lying around already.
	A fresh CI site doesn't, so `frappe.get_all(doctype, limit=1)` alone silently returns nothing."""
	existing = frappe.get_all(doctype, limit=1, pluck="name")
	return existing[0] if existing else creator()


def ensure_company():
	def create():
		# Country/Currency are seeded by Frappe core itself (country_info.json/currency_info.json --
		# confirmed present in the hundreds on a fresh site), unlike Company, so picking any existing
		# one is always safe here.
		country = frappe.get_all("Country", limit=1, pluck="name")[0]
		currency = frappe.get_all("Currency", limit=1, pluck="name")[0]
		doc = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "FHIR Gateway Test Co",
				"abbr": "FGTC",
				"default_currency": currency,
				"country": country,
			}
		)
		# Company.on_update() otherwise creates a default chart of accounts and warehouses -- one of
		# which (Goods In Transit) needs a "Warehouse Type: Transit" record that ERPNext only ever
		# seeds via its interactive setup wizard (erpnext/setup/setup_wizard/operations/
		# install_fixtures.py), never run on this headless site. This test Company exists only to
		# satisfy a mandatory Link field; it has no interest in either.
		previous = frappe.local.flags.ignore_chart_of_accounts
		frappe.local.flags.ignore_chart_of_accounts = True
		try:
			doc.insert(ignore_permissions=True)
		finally:
			frappe.local.flags.ignore_chart_of_accounts = previous
		return doc.name

	return ensure_any("Company", create)


def ensure_practitioner():
	return ensure_any("Healthcare Practitioner", lambda: make_practitioner().name)


def ensure_appointment_type():
	def create():
		doc = frappe.get_doc({"doctype": "Appointment Type", "appointment_type": "FHIR Gateway Test Type"})
		doc.insert(ignore_permissions=True)
		return doc.name

	return ensure_any("Appointment Type", create)


def make_sync_destination(resource_types=None, extra_headers=None, client_secret="s3cret", **fields):
	"""Insert a `FHIR Sync Destination`. `resource_types` is a plain list of FHIR type names and
	`extra_headers` a {name: value} dict -- both become their child-table rows."""
	values = {
		"doctype": "FHIR Sync Destination",
		"destination_name": "Test Destination",
		"base_url": "http://localhost:1/fhir",
		"client_id": "cid",
		"client_secret": client_secret,
	}
	values.update(fields)
	values["resource_types"] = [{"resource_type": t} for t in (resource_types or ["Encounter"])]
	values["extra_headers"] = [
		{"header_name": k, "header_value": v} for k, v in (extra_headers or {}).items()
	]
	doc = frappe.get_doc(values)
	doc.insert(ignore_permissions=True)
	return doc


class LoopbackServer:
	"""A real HTTP server on 127.0.0.1 standing in for a destination (or the alert webhook): real
	requests over the wire, just not to a third party. Answers with `status_codes` in order (the
	last one repeats) and records every request as {path, headers, body}.

	with LoopbackServer([500, 200]) as server:
	        ... server.url ... server.requests
	"""

	def __init__(self, status_codes=(200,)):
		self.status_codes = list(status_codes)
		self.requests = []

	def __enter__(self):
		outer = self

		class Handler(BaseHTTPRequestHandler):
			def do_POST(self):
				length = int(self.headers.get("Content-Length") or 0)
				body = self.rfile.read(length).decode()
				outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
				index = min(len(outer.requests), len(outer.status_codes)) - 1
				self.send_response(outer.status_codes[index])
				self.send_header("Content-Type", "application/json")
				self.end_headers()
				self.wfile.write(json.dumps({"ok": True}).encode())

			def log_message(self, *args):
				pass

		self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
		self._thread.start()
		return self

	def __exit__(self, *exc):
		self._server.shutdown()
		self._server.server_close()

	@property
	def url(self):
		return f"http://127.0.0.1:{self._server.server_address[1]}"


def closed_port_url():
	"""A localhost URL nothing listens on -- for connection-refused paths."""
	with LoopbackServer() as server:
		url = server.url
	return url

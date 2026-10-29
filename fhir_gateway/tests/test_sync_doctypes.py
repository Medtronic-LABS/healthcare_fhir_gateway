"""Outbound-sync doctypes: destination validation, defaults and the FHIR Sync Administrator scoping.
Destinations hold credentials and Events/Deliveries hold PHI-bearing payloads, so the permission
test is the important one here."""

import unittest

import frappe

from fhir_gateway.tests._support import make_sync_destination

SYNC_DOCTYPES = ["FHIR Sync Destination", "FHIR Sync Event", "FHIR Sync Delivery", "FHIR Sync Settings"]


class TestSyncDoctypes(unittest.TestCase):
	def setUp(self):
		self._docs = []
		self._users = []

	def tearDown(self):
		frappe.set_user("Administrator")
		for doctype, name in reversed(self._docs):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
		for user in self._users:
			if frappe.db.exists("User", user):
				frappe.delete_doc("User", user, ignore_permissions=True, force=True)
		frappe.db.commit()

	def _destination(self, **fields):
		doc = make_sync_destination(**fields)
		self._docs.append(("FHIR Sync Destination", doc.name))
		return doc

	def _user(self, email, roles):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Sync Test",
				"send_welcome_email": 0,
				"user_type": "System User",
				"roles": [{"role": r} for r in roles],
			}
		).insert(ignore_permissions=True)
		self._users.append(email)
		return email

	def test_http_rejected_for_remote_host(self):
		with self.assertRaises(frappe.ValidationError):
			self._destination(base_url="http://openhim.example.org/spice-facility/fhir")

	def test_http_allowed_for_localhost(self):
		for i, url in enumerate(
			["http://localhost:8080/fhir", "http://127.0.0.1:1", "http://openhim.localhost"]
		):
			doc = self._destination(destination_name=f"Local {i}", base_url=url)
			self.assertEqual(doc.base_url, url)

	def test_https_allowed_for_remote_host(self):
		doc = self._destination(base_url="https://openhim.example.org/spice-facility/fhir")
		self.assertTrue(frappe.db.exists("FHIR Sync Destination", doc.name))

	def test_unparseable_url_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._destination(base_url="openhim.example.org/fhir")

	def test_defaults(self):
		doc = self._destination()
		self.assertEqual(doc.enabled, 1)
		self.assertEqual(doc.auth_type, "Basic")
		self.assertEqual(doc.event_id_header_name, "X-Source-Event-Id")
		self.assertEqual(doc.correlation_id_header_name, "X-Correlation-Id")
		self.assertEqual(doc.timeout_seconds, 10)
		self.assertEqual(doc.max_retry_attempts, 6)
		self.assertEqual(frappe.get_single("FHIR Sync Settings").retention_days, 30)

	def test_get_resource_types(self):
		doc = self._destination(resource_types=["Encounter", "Condition"])
		self.assertEqual(doc.get_resource_types(), {"Encounter", "Condition"})

	def test_user_without_role_cannot_access(self):
		user = self._user("fhir-sync-norole@example.com", ["FHIR Integration"])
		for doctype in SYNC_DOCTYPES:
			self.assertFalse(frappe.has_permission(doctype, "read", user=user), doctype)
			self.assertFalse(frappe.has_permission(doctype, "write", user=user), doctype)

	def test_sync_administrator_can_access(self):
		user = self._user("fhir-sync-admin@example.com", ["FHIR Sync Administrator"])
		for doctype in SYNC_DOCTYPES:
			self.assertTrue(frappe.has_permission(doctype, "read", user=user), doctype)
		# Writable: destination config, settings, and a Delivery's status (manual replay). An Event is
		# the payload snapshot as submitted -- never editable, even by the administrator.
		for doctype in ("FHIR Sync Destination", "FHIR Sync Delivery", "FHIR Sync Settings"):
			self.assertTrue(frappe.has_permission(doctype, "write", user=user), doctype)
		self.assertFalse(frappe.has_permission("FHIR Sync Event", "write", user=user))

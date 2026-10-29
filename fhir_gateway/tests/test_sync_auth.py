"""sync/auth.py: the auth_type registry. Uses a real inserted destination so the secret goes through
the Password field's encryption and comes back via get_password(), exactly as at delivery time."""

import unittest

import frappe

from fhir_gateway.sync import auth
from fhir_gateway.tests._support import make_sync_destination


class TestSyncAuth(unittest.TestCase):
	def setUp(self):
		self.destination = make_sync_destination(client_id="cid", client_secret="s3cret")

	def tearDown(self):
		frappe.delete_doc("FHIR Sync Destination", self.destination.name, ignore_permissions=True, force=True)
		frappe.db.commit()

	def test_basic_sets_requests_auth_tuple(self):
		kwargs = auth.apply_auth(self.destination, {"timeout": 10})

		self.assertEqual(kwargs["auth"], ("cid", "s3cret"))
		self.assertEqual(kwargs["timeout"], 10)

	def test_secret_is_not_stored_in_plaintext(self):
		stored = frappe.db.get_value("FHIR Sync Destination", self.destination.name, "client_secret")
		self.assertNotEqual(stored, "s3cret")

	def test_unknown_auth_type_raises(self):
		# The Select only offers "Basic"; a stale value can still arrive via API/fixture, bypassing it.
		self.destination.db_set("auth_type", "Bogus")
		self.destination.reload()

		with self.assertRaises(auth.UnsupportedAuthType):
			auth.apply_auth(self.destination, {})

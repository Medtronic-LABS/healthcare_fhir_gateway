"""sync/retention.py: only events whose every delivery is Delivered, and older than retention_days,
are purged. A Dead-lettered (or still-pending) delivery keeps its event forever."""

import json
import unittest
import uuid

import frappe
from frappe.utils import add_days, now_datetime

from fhir_gateway.sync import retention
from fhir_gateway.tests._support import make_sync_destination


class TestSyncRetention(unittest.TestCase):
	def setUp(self):
		self.destination = make_sync_destination(destination_name="Retention Test")
		self._events = []

	def tearDown(self):
		for event in self._events:
			frappe.db.delete("FHIR Sync Delivery", {"sync_event": event})
			frappe.db.delete("FHIR Sync Event", {"name": event})
		frappe.delete_doc("FHIR Sync Destination", self.destination.name, ignore_permissions=True, force=True)
		frappe.db.commit()

	def _event(self, age_days, statuses):
		event_id = str(uuid.uuid4())
		frappe.get_doc(
			{
				"doctype": "FHIR Sync Event",
				"event_id": event_id,
				"resource_type": "Encounter",
				"source_doctype": "Patient Encounter",
				"source_name": "HLC-ENC-RET",
				"fhir_payload": json.dumps({"resourceType": "Encounter"}),
			}
		).insert(ignore_permissions=True)
		created = add_days(now_datetime(), -age_days)
		frappe.db.set_value("FHIR Sync Event", event_id, "creation", created, update_modified=False)
		for status in statuses:
			frappe.get_doc(
				{
					"doctype": "FHIR Sync Delivery",
					"sync_event": event_id,
					"destination": self.destination.name,
					"status": status,
				}
			).insert(ignore_permissions=True)
		self._events.append(event_id)
		return event_id

	def _exists(self, event):
		return bool(frappe.db.exists("FHIR Sync Event", event))

	def test_old_fully_delivered_event_is_purged_with_its_deliveries(self):
		event = self._event(31, ["Delivered", "Delivered"])

		retention.purge_delivered_events()

		self.assertFalse(self._exists(event))
		self.assertFalse(frappe.db.exists("FHIR Sync Delivery", {"sync_event": event}))

	def test_dead_lettered_event_is_never_purged(self):
		event = self._event(400, ["Delivered", "Dead-lettered"])

		retention.purge_delivered_events()

		self.assertTrue(self._exists(event))
		self.assertEqual(frappe.db.count("FHIR Sync Delivery", {"sync_event": event}), 2)

	def test_pending_or_failed_event_is_kept(self):
		pending = self._event(31, ["Pending"])
		failed = self._event(31, ["Delivered", "Failed"])

		retention.purge_delivered_events()

		self.assertTrue(self._exists(pending))
		self.assertTrue(self._exists(failed))

	def test_recent_delivered_event_is_kept(self):
		event = self._event(5, ["Delivered"])

		retention.purge_delivered_events()

		self.assertTrue(self._exists(event))

	def test_retention_days_setting_is_honoured(self):
		event = self._event(5, ["Delivered"])
		settings = frappe.get_single("FHIR Sync Settings")
		previous = settings.retention_days
		frappe.db.set_single_value("FHIR Sync Settings", "retention_days", 3)
		try:
			retention.purge_delivered_events()
		finally:
			frappe.db.set_single_value("FHIR Sync Settings", "retention_days", previous)

		self.assertFalse(self._exists(event))

	def test_purge_is_not_starved_by_kept_events(self):
		kept = [self._event(60, ["Dead-lettered"]) for _ in range(3)]
		purged = self._event(31, ["Delivered"])

		original = retention.BATCH_SIZE
		retention.BATCH_SIZE = 2
		try:
			retention.purge_delivered_events()
		finally:
			retention.BATCH_SIZE = original

		self.assertFalse(self._exists(purged))
		self.assertTrue(all(self._exists(e) for e in kept))

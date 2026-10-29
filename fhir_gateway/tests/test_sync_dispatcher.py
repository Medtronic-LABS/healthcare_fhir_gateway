"""sync/dispatcher.py + sync/alerts.py against real loopback HTTP servers: outcome per response
class, backoff, dead-lettering and its alert, header contract, and the no-op guards."""

import base64
import json
import unittest
import uuid
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from fhir_gateway.sync import alerts, dispatcher
from fhir_gateway.tests._support import LoopbackServer, closed_port_url, make_sync_destination

PAYLOAD = {"resourceType": "Encounter", "id": "HLC-ENC-TEST", "status": "finished"}


class TestSyncDispatcher(unittest.TestCase):
	def setUp(self):
		self._destinations = []
		self._events = []

	def tearDown(self):
		for event in self._events:
			frappe.db.delete("FHIR Sync Delivery", {"sync_event": event})
			frappe.db.delete("FHIR Sync Event", {"name": event})
		for name in self._destinations:
			frappe.delete_doc("FHIR Sync Destination", name, ignore_permissions=True, force=True)
		frappe.db.delete("Error Log", {"method": ["like", "FHIR sync%"]})
		frappe.db.commit()

	def _delivery(self, base_url, status="Pending", next_attempt_at=None, **destination_fields):
		destination = make_sync_destination(
			destination_name=f"Dispatch Test {uuid.uuid4().hex[:8]}", base_url=base_url, **destination_fields
		)
		self._destinations.append(destination.name)
		event_id = str(uuid.uuid4())
		frappe.get_doc(
			{
				"doctype": "FHIR Sync Event",
				"event_id": event_id,
				"correlation_id": "corr-1",
				"resource_type": "Encounter",
				"source_doctype": "Patient Encounter",
				"source_name": "HLC-ENC-TEST",
				"fhir_payload": json.dumps(PAYLOAD),
			}
		).insert(ignore_permissions=True)
		self._events.append(event_id)
		delivery = frappe.get_doc(
			{
				"doctype": "FHIR Sync Delivery",
				"sync_event": event_id,
				"destination": destination.name,
				"status": status,
				"next_attempt_at": next_attempt_at or now_datetime(),
			}
		).insert(ignore_permissions=True)
		return delivery.name

	def _reload(self, name):
		return frappe.get_doc("FHIR Sync Delivery", name)

	def _make_due(self, name):
		frappe.db.set_value("FHIR Sync Delivery", name, "next_attempt_at", add_to_date(None, seconds=-1))

	def test_2xx_marks_delivered_with_header_contract(self):
		with LoopbackServer([201]) as server:
			name = self._delivery(
				server.url + "/spice-facility/fhir/", extra_headers={"X-Facility-Id": "FAC-1"}
			)
			dispatcher.attempt_delivery(name)

		delivery = self._reload(name)
		self.assertEqual(delivery.status, "Delivered")
		self.assertEqual(delivery.attempt_count, 1)
		self.assertTrue(delivery.delivered_at)
		self.assertFalse(delivery.last_error)

		[request] = server.requests
		self.assertEqual(request["path"], "/spice-facility/fhir/Encounter")
		self.assertEqual(json.loads(request["body"]), PAYLOAD)
		headers = request["headers"]
		self.assertEqual(headers["Content-Type"], "application/fhir+json")
		self.assertEqual(headers["Authorization"], "Basic " + base64.b64encode(b"cid:s3cret").decode())
		self.assertEqual(headers["X-Source-Event-Id"], delivery.sync_event)
		self.assertEqual(headers["X-Correlation-Id"], "corr-1")
		self.assertEqual(headers["X-Facility-Id"], "FAC-1")

	def test_blank_header_name_omits_header(self):
		with LoopbackServer([200]) as server:
			name = self._delivery(
				server.url, event_id_header_name="", correlation_id_header_name="Custom-Corr"
			)
			dispatcher.attempt_delivery(name)

		headers = server.requests[0]["headers"]
		self.assertNotIn("X-Source-Event-Id", headers)
		self.assertNotIn("X-Correlation-Id", headers)
		self.assertEqual(headers["Custom-Corr"], "corr-1")

	def test_401_dead_letters_without_retry_and_alerts(self):
		with LoopbackServer([401]) as server, LoopbackServer([200]) as alert:
			with patch.object(alerts, "alert_webhook_url", return_value=alert.url):
				name = self._delivery(server.url)
				dispatcher.attempt_delivery(name)

		delivery = self._reload(name)
		self.assertEqual(delivery.status, "Dead-lettered")
		self.assertEqual(delivery.attempt_count, 1)
		self.assertIn("401", delivery.last_error)
		self.assertNotIn("s3cret", delivery.last_error)
		[alert_request] = alert.requests
		text = json.loads(alert_request["body"])["text"]
		self.assertIn(delivery.sync_event, text)
		self.assertIn("Encounter", text)
		self.assertNotIn("s3cret", text)

	def test_500_schedules_retry_with_first_backoff_step(self):
		with LoopbackServer([500]) as server:
			name = self._delivery(server.url)
			before = now_datetime()
			dispatcher.attempt_delivery(name)

		delivery = self._reload(name)
		self.assertEqual(delivery.status, "Failed")
		self.assertEqual(delivery.attempt_count, 1)
		delay = get_datetime(delivery.next_attempt_at) - before
		self.assertTrue(timedelta(seconds=59) <= delay <= timedelta(seconds=65), delay)
		self.assertIn("500", delivery.last_error)

	def test_429_is_retried(self):
		with LoopbackServer([429]) as server:
			name = self._delivery(server.url)
			dispatcher.attempt_delivery(name)

		self.assertEqual(self._reload(name).status, "Failed")

	def test_connection_error_is_retried(self):
		name = self._delivery(closed_port_url())
		dispatcher.attempt_delivery(name)

		delivery = self._reload(name)
		self.assertEqual(delivery.status, "Failed")
		self.assertTrue(delivery.last_error)

	def test_exhausted_retries_dead_letter(self):
		with LoopbackServer([500]) as server, patch.object(alerts, "alert_webhook_url", return_value=None):
			name = self._delivery(server.url, max_retry_attempts=1)
			dispatcher.attempt_delivery(name)  # first attempt
			self.assertEqual(self._reload(name).status, "Failed")
			self._make_due(name)
			dispatcher.attempt_delivery(name)  # the one allowed retry

		delivery = self._reload(name)
		self.assertEqual(delivery.status, "Dead-lettered")
		self.assertEqual(delivery.attempt_count, 2)
		self.assertEqual(len(server.requests), 2)

	def test_unsupported_auth_type_dead_letters(self):
		with LoopbackServer([200]) as server, patch.object(alerts, "alert_webhook_url", return_value=None):
			name = self._delivery(server.url)
			frappe.db.set_value("FHIR Sync Destination", self._destinations[-1], "auth_type", "Bogus")
			dispatcher.attempt_delivery(name)

		self.assertEqual(server.requests, [])
		delivery = self._reload(name)
		self.assertEqual(delivery.status, "Dead-lettered")
		self.assertIn("Unsupported auth type", delivery.last_error)

	def test_leased_row_is_skipped(self):
		# another job claimed it moments ago
		with LoopbackServer([200]) as server:
			name = self._delivery(server.url, next_attempt_at=add_to_date(None, minutes=5))
			dispatcher.attempt_delivery(name)

		self.assertEqual(server.requests, [])
		self.assertEqual(self._reload(name).attempt_count, 0)

	def test_claim_counts_attempt_and_leases_before_sending(self):
		seen = {}

		def fake_post(destination, event):
			seen.update(
				frappe.db.get_value(
					"FHIR Sync Delivery",
					{"sync_event": event.name},
					["attempt_count", "next_attempt_at"],
					as_dict=True,
				)
			)
			return dispatcher._DELIVERED, None

		name = self._delivery(closed_port_url())
		with patch.object(dispatcher, "_post", side_effect=fake_post):
			dispatcher.attempt_delivery(name)

		self.assertEqual(seen["attempt_count"], 1)
		lease_floor = add_to_date(None, seconds=dispatcher.LEASE_SECONDS - 60)
		self.assertGreater(get_datetime(seen["next_attempt_at"]), lease_floor)
		self.assertEqual(self._reload(name).status, "Delivered")

	def test_attempt_that_never_finished_dead_letters(self):
		# a job killed mid-call counted its attempt but never recorded an outcome
		with LoopbackServer([200]) as server, patch.object(alerts, "alert_webhook_url", return_value=None):
			name = self._delivery(server.url, max_retry_attempts=2)
			frappe.db.set_value("FHIR Sync Delivery", name, "attempt_count", 3)
			dispatcher.attempt_delivery(name)

		self.assertEqual(server.requests, [])
		delivery = self._reload(name)
		self.assertEqual(delivery.status, "Dead-lettered")
		self.assertEqual(delivery.last_error, "Attempt did not complete")

	def test_unsendable_requests_dead_letter_without_retry(self):
		cases = [
			({"Bad:Header": "x"}, "InvalidHeader"),
			({"X-Facility-Name": "H\u00f4pital \u0939"}, "UnicodeEncodeError"),
		]
		for extra_headers, error in cases:
			with patch.object(alerts, "alert_webhook_url", return_value=None):
				name = self._delivery(closed_port_url(), extra_headers=extra_headers)
				dispatcher.attempt_delivery(name)
			delivery = self._reload(name)
			self.assertEqual((delivery.status, delivery.last_error), ("Dead-lettered", error))

	def test_failed_alert_does_not_log_webhook_url(self):
		with LoopbackServer([400]) as server, LoopbackServer([404]) as alert:
			webhook = alert.url + "/services/T000/B000/secret-token"
			with patch.object(alerts, "alert_webhook_url", return_value=webhook):
				name = self._delivery(server.url)
				dispatcher.attempt_delivery(name)

		log = frappe.get_last_doc("Error Log", filters={"reference_name": name})
		self.assertEqual(log.method, "FHIR sync dead-letter alert failed")
		self.assertIn("HTTP 404", log.error)
		self.assertNotIn("secret-token", log.error)

	def test_enqueue_attempt_never_raises(self):
		with patch("frappe.enqueue", side_effect=ConnectionError("redis down")):
			self.assertFalse(dispatcher.enqueue_attempt("any"))

	def test_enqueue_attempt_leaves_headroom_in_shared_queue(self):
		with (
			patch.object(dispatcher, "get_queue", return_value=frappe._dict(count=10**6)),
			patch("frappe.enqueue") as enqueue,
		):
			self.assertFalse(dispatcher.enqueue_attempt("any"))
		enqueue.assert_not_called()

	def test_noop_on_final_or_missing_row(self):
		with LoopbackServer([200]) as server:
			delivered = self._delivery(server.url, status="Delivered")
			dead = self._delivery(server.url, status="Dead-lettered")
			dispatcher.attempt_delivery(delivered)
			dispatcher.attempt_delivery(dead)
			dispatcher.attempt_delivery("does-not-exist")

		self.assertEqual(server.requests, [])
		self.assertEqual(self._reload(delivered).attempt_count, 0)
		self.assertEqual(self._reload(dead).status, "Dead-lettered")

	def test_disabled_destination_untouched(self):
		with LoopbackServer([200]) as server:
			name = self._delivery(server.url, enabled=0)
			dispatcher.attempt_delivery(name)

		self.assertEqual(server.requests, [])
		delivery = self._reload(name)
		self.assertEqual((delivery.status, delivery.attempt_count), ("Pending", 0))

	def test_alert_failure_never_raises(self):
		for url in (None, closed_port_url()):
			with LoopbackServer([400]) as server, patch.object(alerts, "alert_webhook_url", return_value=url):
				name = self._delivery(server.url)
				dispatcher.attempt_delivery(name)
			self.assertEqual(self._reload(name).status, "Dead-lettered")

	def test_alert_webhook_url_prefers_site_config_then_env(self):
		with patch.dict(frappe.conf, {"alert_webhook_url": "https://site.example/hook"}):
			self.assertEqual(alerts.alert_webhook_url(), "https://site.example/hook")
		with (
			patch.dict(frappe.conf, {"alert_webhook_url": None}),
			patch.dict("os.environ", {"ALERT_WEBHOOK_URL": "https://env.example/hook"}),
		):
			self.assertEqual(alerts.alert_webhook_url(), "https://env.example/hook")

	def test_sweep_picks_due_skips_future_and_disabled(self):
		due = self._delivery(
			closed_port_url(), status="Failed", next_attempt_at=add_to_date(None, minutes=-1)
		)
		future = self._delivery(
			closed_port_url(), status="Failed", next_attempt_at=add_to_date(None, hours=1)
		)
		disabled = self._delivery(closed_port_url(), enabled=0, next_attempt_at=add_to_date(None, minutes=-1))
		delivered = self._delivery(
			closed_port_url(), status="Delivered", next_attempt_at=add_to_date(None, minutes=-1)
		)

		with patch("frappe.enqueue") as enqueue:
			dispatcher.sweep_due_deliveries()

		swept = {c.kwargs["delivery_name"] for c in enqueue.call_args_list}
		self.assertIn(due, swept)
		self.assertNotIn(future, swept)
		self.assertNotIn(disabled, swept)
		self.assertNotIn(delivered, swept)
		call = next(c for c in enqueue.call_args_list if c.kwargs["delivery_name"] == due)
		self.assertEqual(call.args[0], "fhir_gateway.sync.dispatcher.attempt_delivery")
		self.assertTrue(call.kwargs["deduplicate"])
		self.assertEqual(call.kwargs["job_id"], f"fhir_sync_delivery::{due}")

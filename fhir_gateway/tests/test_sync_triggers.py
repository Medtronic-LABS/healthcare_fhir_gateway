"""sync/triggers.py + sync/outbox.py: a real Patient Encounter submit must leave exactly the expected
Events and Deliveries behind, fanned out per destination resource_types -- and a failure anywhere in
the sync path must leave the submit intact and no partial outbox rows.

`frappe.enqueue` is patched so the after-commit delivery jobs are asserted rather than pushed to a
real worker queue."""

import json
import unittest
import uuid
from unittest.mock import patch

import frappe
from frappe.utils import now_datetime

from fhir_gateway.sync import triggers
from fhir_gateway.tests._support import (
	ensure_appointment_type,
	ensure_practitioner,
	make_patient,
	make_sync_destination,
)

DISPATCH_METHOD = "fhir_gateway.sync.dispatcher.attempt_delivery"


class TestSyncTriggers(unittest.TestCase):
	def setUp(self):
		self._docs = []
		self._started = now_datetime()
		self.dest_a = self._track(
			make_sync_destination(destination_name="Sync Test A", resource_types=["Encounter", "Condition"])
		)
		self.dest_b = self._track(
			make_sync_destination(destination_name="Sync Test B", resource_types=["Encounter"])
		)
		self.dest_off = self._track(
			make_sync_destination(
				destination_name="Sync Test Disabled",
				enabled=0,
				resource_types=["Encounter", "Condition", "Observation"],
			)
		)
		enqueue_patcher = patch("frappe.enqueue")
		self.enqueue = enqueue_patcher.start()
		self.addCleanup(enqueue_patcher.stop)

	def tearDown(self):
		for event in frappe.get_all(
			"FHIR Sync Event", filters={"source_name": ["in", self._source_names()]}, pluck="name"
		):
			frappe.db.delete("FHIR Sync Delivery", {"sync_event": event})
			frappe.db.delete("FHIR Sync Event", {"name": event})
		for doctype, name in reversed(self._docs):
			if not frappe.db.exists(doctype, name):
				continue
			doc = frappe.get_doc(doctype, name)
			if doc.meta.is_submittable and doc.docstatus == 1:
				# Bypass cancel(): healthcare's cancel hooks have their own side effects (fee validity,
				# appointment status) this test never set up.
				frappe.db.set_value(doctype, name, "docstatus", 2)
			frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
		frappe.db.delete("Error Log", {"method": ["like", "FHIR sync%"], "creation": [">=", self._started]})
		frappe.db.commit()

	def _track(self, doc):
		self._docs.append((doc.doctype, doc.name))
		return doc

	def _source_names(self):
		names = [name for _, name in self._docs]
		for doctype, name in self._docs:
			if doctype == "Patient Encounter" and frappe.db.exists(doctype, name):
				names += [r.name for r in frappe.get_doc(doctype, name).diagnosis]
		return names or ["-"]

	def _diagnosis(self, name):
		if not frappe.db.exists("Diagnosis", name):
			self._track(
				frappe.get_doc({"doctype": "Diagnosis", "diagnosis": name}).insert(ignore_permissions=True)
			)
		return name

	def _submit_encounter(self, diagnoses=()):
		patient = self._track(make_patient(first_name="Sync Test"))
		doc = frappe.get_doc(
			{
				"doctype": "Patient Encounter",
				"patient": patient.name,
				"practitioner": ensure_practitioner(),
				"encounter_date": "2026-01-01",
				"encounter_time": "10:00:00",
				"appointment_type": ensure_appointment_type(),
				"diagnosis": [{"diagnosis": self._diagnosis(d)} for d in diagnoses],
			}
		)
		doc.insert(ignore_permissions=True)
		self._track(doc)
		doc.submit()
		return doc

	def _events(self, **filters):
		return frappe.get_all(
			"FHIR Sync Event",
			filters=filters,
			fields=["name", "event_id", "correlation_id", "resource_type", "source_name", "fhir_payload"],
		)

	def _destinations_of(self, event_name):
		return set(
			frappe.get_all("FHIR Sync Delivery", filters={"sync_event": event_name}, pluck="destination")
		)

	def _sync_errors_logged(self, source):
		"""Error Logs from this test only: encounter names are reused once a test deletes them, so an
		older run's log for the same name must not count."""
		return frappe.db.count(
			"Error Log",
			{
				"reference_doctype": source.doctype,
				"reference_name": source.name,
				"creation": [">=", self._started],
			},
		)

	def _scheduled_deliveries(self):
		return [
			c.kwargs["delivery_name"] for c in self.enqueue.call_args_list if c.args[0] == DISPATCH_METHOD
		]

	def test_encounter_with_diagnoses_fans_out(self):
		encounter = self._submit_encounter(diagnoses=["Sync Test Fever", "Sync Test Cough"])
		self.assertEqual(self._sync_errors_logged(encounter), 0)

		[enc_event] = self._events(source_doctype="Patient Encounter", resource_type="Encounter")
		self.assertEqual(enc_event.source_name, encounter.name)
		self.assertEqual(self._destinations_of(enc_event.name), {self.dest_a.name, self.dest_b.name})
		payload = json.loads(enc_event.fhir_payload)
		self.assertEqual(payload["resourceType"], "Encounter")
		self.assertEqual(payload["id"], encounter.name)
		uuid.UUID(enc_event.event_id)
		uuid.UUID(enc_event.correlation_id)

		row_names = {r.name for r in encounter.diagnosis}
		conditions = self._events(resource_type="Condition", source_name=["in", list(row_names)])
		self.assertEqual({e.source_name for e in conditions}, row_names)
		for event in conditions:
			self.assertEqual(self._destinations_of(event.name), {self.dest_a.name})
			self.assertEqual(json.loads(event.fhir_payload)["resourceType"], "Condition")
			# one submit = one correlation id across everything it emitted
			self.assertEqual(event.correlation_id, enc_event.correlation_id)

		deliveries = frappe.get_all(
			"FHIR Sync Delivery",
			filters={"sync_event": ["in", [enc_event.name] + [e.name for e in conditions]]},
			fields=["name", "status", "next_attempt_at"],
		)
		self.assertEqual(len(deliveries), 4)
		self.assertTrue(all(d.status == "Pending" and d.next_attempt_at for d in deliveries))
		self.assertEqual(sorted(self._scheduled_deliveries()), sorted(d.name for d in deliveries))

	def test_encounter_without_diagnoses_emits_only_encounter(self):
		encounter = self._submit_encounter()

		events = self._events(source_doctype="Patient Encounter", source_name=encounter.name)
		self.assertEqual([e.resource_type for e in events], ["Encounter"])
		self.assertFalse(self._events(resource_type="Condition", correlation_id=events[0].correlation_id))

	def test_no_matching_destination_creates_no_event(self):
		frappe.db.set_value("FHIR Sync Destination", self.dest_a.name, "enabled", 0)
		frappe.db.set_value("FHIR Sync Destination", self.dest_b.name, "enabled", 0)

		encounter = self._submit_encounter(diagnoses=["Sync Test Fever"])

		self.assertFalse(self._events(source_name=encounter.name))
		self.assertFalse(self._scheduled_deliveries())

	def test_mapper_failure_does_not_block_submit(self):
		with patch("fhir_gateway.mappers.condition.to_fhir", side_effect=RuntimeError("boom")):
			encounter = self._submit_encounter(diagnoses=["Sync Test Fever"])

		self.assertEqual(frappe.db.get_value("Patient Encounter", encounter.name, "docstatus"), 1)
		self.assertEqual(self._sync_errors_logged(encounter), 1)
		# all-or-nothing: the Encounter event built before the failing Condition is not kept either
		self.assertFalse(self._events(source_name=encounter.name))
		self.assertFalse(self._scheduled_deliveries())

	def test_outbox_write_failure_rolls_back_partial_rows(self):
		with patch("fhir_gateway.sync.outbox._insert_delivery", side_effect=RuntimeError("db boom")):
			encounter = self._submit_encounter()

		self.assertEqual(frappe.db.get_value("Patient Encounter", encounter.name, "docstatus"), 1)
		self.assertEqual(self._sync_errors_logged(encounter), 1)
		self.assertFalse(self._events(source_name=encounter.name))
		self.assertFalse(self._scheduled_deliveries())

	def test_build_events_per_doctype(self):
		vitals = frappe._dict(
			doctype="Vital Signs", name="VS-1", patient="P1", signs_date="2026-01-01", signs_time="10:00:00"
		)
		observation = frappe._dict(
			doctype="Observation",
			name="OBS-1",
			patient="P1",
			status="Final",
			codification_table=[],
			observation_template="Hb",
		)

		[vitals_event] = triggers.build_events(vitals)
		self.assertEqual((vitals_event.resource_type, vitals_event.source_name), ("Observation", "VS-1"))
		self.assertEqual(vitals_event.payload["code"]["coding"][0]["code"], "85353-1")
		[obs_event] = triggers.build_events(observation)
		self.assertEqual((obs_event.resource_type, obs_event.source_doctype), ("Observation", "Observation"))
		self.assertEqual(obs_event.payload["id"], "OBS-1")

"""Unit test for mappers/medication_request.py -- a frappe._dict stand-in rather than a full DB
row, since Medication Request has a heavy mandatory-field chain (company, practitioner,
dosage_form, dosage, medication_item...) unrelated to what this mapper actually needs to prove.
Real `Code Value` records ARE used for status/intent/priority though, since `code_value_string`'s
whole job is resolving a real Link value -- faking that would test nothing. This is also the test
that caught a real data-quality bug: healthcare's "Intent"/"Priority" Code Value rows store
Title-Case display labels ("Original Order", "Routine"), not the actual FHIR enum code
("original-order", "routine") -- see `fhir_enum_code`'s docstring in mappers/_shared.py."""

import unittest

import frappe

from fhir_gateway.mappers import medication_request as mr_mapper


class TestMedicationRequestMapper(unittest.TestCase):
	def test_to_fhir_basic_shape(self):
		doc = frappe._dict(
			doctype="Medication Request",
			name="HMR-TEST-0001",
			status="active-Medication Request Status",
			intent="Original Order-Intent",
			priority="Routine-Priority",
			patient="Ramesh Kumar",
			practitioner="HLC-PRAC-2026-00001",
			medication=None,
			item_code="Tablet Metformin 500mg",
			codification_table=[],
			order_date="2026-01-01",
			order_time="10:00:00",
			dosage_form="Tablet",
			dosage="BID",
			quantity=30,
			order_group=None,
			modified=None,
		)

		resource = mr_mapper.to_fhir(doc)

		self.assertEqual(resource["resourceType"], "MedicationRequest")
		self.assertEqual(resource["id"], "HMR-TEST-0001")
		self.assertEqual(resource["status"], "active")
		self.assertEqual(resource["intent"], "original-order")
		self.assertEqual(resource["priority"], "routine")
		self.assertEqual(resource["subject"], {"reference": "Patient/Ramesh Kumar"})
		self.assertEqual(resource["requester"], {"reference": "Practitioner/HLC-PRAC-2026-00001"})
		self.assertEqual(resource["medicationCodeableConcept"]["text"], "Tablet Metformin 500mg")
		self.assertEqual(resource["dosageInstruction"][0]["text"], "BID")
		self.assertEqual(resource["dispenseRequest"]["quantity"]["value"], 30)

	def test_missing_status_falls_back_to_unknown(self):
		doc = frappe._dict(
			doctype="Medication Request",
			name="HMR-TEST-0002",
			status=None,
			intent=None,
			priority=None,
			patient="Ramesh Kumar",
			practitioner=None,
			medication=None,
			item_code=None,
			codification_table=[],
			order_date=None,
			order_time=None,
			dosage_form=None,
			dosage=None,
			quantity=None,
			order_group=None,
			modified=None,
		)

		resource = mr_mapper.to_fhir(doc)

		self.assertEqual(resource["status"], "unknown")
		self.assertEqual(resource["intent"], "order")
		self.assertNotIn("priority", resource)
		self.assertNotIn("dosageInstruction", resource)
		self.assertNotIn("dispenseRequest", resource)

"""Integration test for mappers/condition.py. The encounter and diagnosis row are frappe._dict
stand-ins, but the `Diagnosis` master record is real: the mapper's coding comes from that record's
own codification table, so faking it would test nothing."""

import unittest

import frappe

from fhir_gateway.mappers import condition as condition_mapper
from fhir_gateway.tests._support import ensure_code_system

ENCOUNTER = frappe._dict(
	doctype="Patient Encounter",
	name="HLC-ENC-TEST-1",
	patient="Ramesh Kumar",
	practitioner="HLC-PRAC-2026-00001",
	encounter_date="2026-01-01",
	encounter_time="10:00:00",
)


class TestConditionMapper(unittest.TestCase):
	def setUp(self):
		self._docs = []

	def tearDown(self):
		for doctype, name in reversed(self._docs):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
		frappe.db.commit()

	def _ensure_icd10_code(self, code, display):
		"""A Codification Table row must link a real Code Value (its `code`/`display`/`system` are
		fetched from it), and that Code Value a real Code System -- neither exists on a fresh site."""
		if ensure_code_system("ICD-10", "http://hl7.org/fhir/sid/icd-10"):
			self._docs.append(("Code System", "ICD-10"))
		name = f"{code}-ICD-10"
		if not frappe.db.exists("Code Value", name):
			frappe.get_doc(
				{"doctype": "Code Value", "code_system": "ICD-10", "code_value": code, "display": display}
			).insert(ignore_permissions=True)
			self._docs.append(("Code Value", name))
		return name

	def _make_diagnosis(self, name, codification=None):
		doc = frappe.get_doc(
			{"doctype": "Diagnosis", "diagnosis": name, "codification_table": codification or []}
		)
		doc.insert(ignore_permissions=True)
		self._docs.append(("Diagnosis", doc.name))
		return doc

	def test_to_fhir_basic_shape(self):
		diagnosis = self._make_diagnosis("FHIR Gateway Test Fever")
		row = frappe._dict(name="row1", diagnosis=diagnosis.name)

		resource = condition_mapper.to_fhir(ENCOUNTER, row)

		self.assertEqual(resource["resourceType"], "Condition")
		self.assertEqual(resource["id"], "row1")
		self.assertEqual(resource["clinicalStatus"]["coding"][0]["code"], "active")
		self.assertEqual(resource["verificationStatus"]["coding"][0]["code"], "confirmed")
		self.assertEqual(resource["category"][0]["coding"][0]["code"], "encounter-diagnosis")
		self.assertEqual(resource["code"], {"text": "FHIR Gateway Test Fever"})
		self.assertEqual(resource["subject"], {"reference": "Patient/Ramesh Kumar"})
		self.assertEqual(resource["encounter"], {"reference": "Encounter/HLC-ENC-TEST-1"})
		self.assertEqual(resource["recorder"], {"reference": "Practitioner/HLC-PRAC-2026-00001"})
		self.assertEqual(resource["recordedDate"], "2026-01-01T10:00:00")

	def test_codes_come_from_diagnosis_codification(self):
		code_value = self._ensure_icd10_code("A90", "Dengue fever")
		diagnosis = self._make_diagnosis(
			"FHIR Gateway Test Dengue",
			[{"code_system": "ICD-10", "code_value": code_value, "code": "A90", "display": "Dengue fever"}],
		)
		row = frappe._dict(name="row2", diagnosis=diagnosis.name)

		resource = condition_mapper.to_fhir(ENCOUNTER, row)

		# display comes from the Code Value, which a long-lived site may already hold with its own label
		[coding] = resource["code"]["coding"]
		self.assertEqual(coding["system"], "http://hl7.org/fhir/sid/icd-10")
		self.assertEqual(coding["code"], "A90")
		self.assertEqual(resource["code"]["text"], "FHIR Gateway Test Dengue")

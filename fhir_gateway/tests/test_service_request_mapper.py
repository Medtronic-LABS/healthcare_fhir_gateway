"""Unit test for mappers/service_request.py -- same frappe._dict stand-in approach as
test_medication_request_mapper.py (Service Request's mandatory-field chain -- company, template_dt/
template_dn, practitioner -- is unrelated to what the mapper needs to prove), with real `Code Value`
names for status/intent/priority since resolving those Links is the mapper's real job."""

import unittest

import frappe

from fhir_gateway.mappers import service_request as sr_mapper
from fhir_gateway.mappers._shared import code_value_string, fhir_enum_code

# http://hl7.org/fhir/ValueSet/request-status
FHIR_REQUEST_STATUSES = {"draft", "active", "on-hold", "revoked", "completed", "entered-in-error", "unknown"}


def _service_request(**overrides):
	values = dict(
		doctype="Service Request",
		name="HSR-TEST-0001",
		status="active-Request Status",
		intent="Original Order-Intent",
		priority="Routine-Priority",
		patient="Ramesh Kumar",
		practitioner="HLC-PRAC-2026-00001",
		template_dt="Lab Test Template",
		template_dn="CBC",
		codification_table=[],
		order_date="2026-01-01",
		order_time="10:00:00",
		order_group="HLC-ENC-1",
		order_group_doctype="Patient Encounter",
		order_description=None,
		modified=None,
	)
	values.update(overrides)
	return frappe._dict(values)


class TestServiceRequestMapper(unittest.TestCase):
	def test_to_fhir_basic_shape(self):
		resource = sr_mapper.to_fhir(_service_request())

		self.assertEqual(resource["resourceType"], "ServiceRequest")
		self.assertEqual(resource["id"], "HSR-TEST-0001")
		self.assertEqual(resource["status"], "active")
		self.assertEqual(resource["intent"], "original-order")
		self.assertEqual(resource["priority"], "routine")
		self.assertEqual(resource["code"], {"text": "CBC"})
		self.assertEqual(resource["subject"], {"reference": "Patient/Ramesh Kumar"})
		self.assertEqual(resource["requester"], {"reference": "Practitioner/HLC-PRAC-2026-00001"})
		self.assertEqual(resource["authoredOn"], "2026-01-01T10:00:00")
		self.assertEqual(resource["encounter"], {"reference": "Encounter/HLC-ENC-1"})

	def test_missing_status_falls_back_to_unknown(self):
		resource = sr_mapper.to_fhir(
			_service_request(
				status=None, intent=None, priority=None, order_group=None, order_group_doctype=None
			)
		)

		self.assertEqual(resource["status"], "unknown")
		self.assertEqual(resource["intent"], "order")
		self.assertNotIn("priority", resource)
		self.assertNotIn("encounter", resource)

	def test_order_group_from_another_doctype_is_not_an_encounter(self):
		resource = sr_mapper.to_fhir(
			_service_request(order_group="IP-1", order_group_doctype="Inpatient Record")
		)

		self.assertNotIn("encounter", resource)

	def test_every_real_status_option_is_mapped(self):
		names = frappe.get_all("Code Value", filters={"code_system": "Request Status"}, pluck="name")
		self.assertTrue(names, "no Request Status Code Values seeded on this site")
		for name in names:
			self.assertIn(fhir_enum_code(code_value_string(name)), FHIR_REQUEST_STATUSES, name)

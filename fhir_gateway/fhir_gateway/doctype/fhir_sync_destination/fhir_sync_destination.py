# Copyright (c) 2026, Medtronic Labs and contributors
# For license information, please see license.txt

"""One external system that receives pushed FHIR resources (e.g. OpenHIM's `spice-facility`
channel). Configuration only -- delivery logic lives in fhir_gateway/sync/."""

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

# Plain http is accepted only for a destination on this same machine (local dev / CI loopback);
# anything that leaves the host carries PHI and credentials, so it must be TLS.
_LOCAL_HOSTS = {"localhost", "127.0.0.1"}


class FHIRSyncDestination(Document):
	def validate(self):
		self._validate_base_url()

	def _validate_base_url(self):
		parsed = urlparse(self.base_url or "")
		if parsed.scheme not in ("http", "https") or not parsed.hostname:
			frappe.throw(_("Base URL must be an absolute http(s) URL, e.g. https://openhim.example.org/fhir"))
		if parsed.scheme == "http" and not _is_local(parsed.hostname):
			frappe.throw(_("Base URL must use https:// unless the host is localhost"))

	def get_resource_types(self):
		return {row.resource_type for row in self.resource_types}


def _is_local(hostname):
	return hostname in _LOCAL_HOSTS or hostname.endswith(".localhost")

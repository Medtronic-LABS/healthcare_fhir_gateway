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
# Upper bound keeps one attempt well inside the `short` queue's 300s job timeout.
_MAX_TIMEOUT_SECONDS = 60


class FHIRSyncDestination(Document):
	def validate(self):
		self._validate_base_url()
		self._validate_limits()

	def _validate_base_url(self):
		parsed = urlparse(self.base_url or "")
		if parsed.scheme not in ("http", "https") or not parsed.hostname:
			frappe.throw(_("Base URL must be an absolute http(s) URL, e.g. https://openhim.example.org/fhir"))
		if parsed.scheme == "http" and not _is_local(parsed.hostname):
			frappe.throw(_("Base URL must use https:// unless the host is localhost"))

	def _validate_limits(self):
		if not 1 <= (self.timeout_seconds or 0) <= _MAX_TIMEOUT_SECONDS:
			frappe.throw(_("Timeout Seconds must be between 1 and {0}").format(_MAX_TIMEOUT_SECONDS))
		if (self.max_retry_attempts or 0) < 0:
			frappe.throw(_("Max Retry Attempts cannot be negative"))

	def get_resource_types(self):
		return {row.resource_type for row in self.resource_types}


def _is_local(hostname):
	return hostname in _LOCAL_HOSTS or hostname.endswith(".localhost")

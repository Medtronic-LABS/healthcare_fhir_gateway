"""Applies a destination's credentials to an outgoing request. A registry keyed on
`FHIR Sync Destination.auth_type`: adding API Key or OAuth2 later is one function here plus one
Select option -- the dispatcher never changes.

The secret is read with get_password() at request time only and never cached, logged or returned
in an error message."""

import frappe
from frappe import _


class UnsupportedAuthType(frappe.ValidationError):
	pass


def _basic(destination, request_kwargs):
	request_kwargs["auth"] = (
		destination.client_id or "",
		destination.get_password("client_secret", raise_exception=False) or "",
	)
	return request_kwargs


_AUTH_HANDLERS = {
	"Basic": _basic,
}


def apply_auth(destination, request_kwargs):
	handler = _AUTH_HANDLERS.get(destination.auth_type)
	if not handler:
		raise UnsupportedAuthType(
			_("Unsupported auth type {0} on FHIR Sync Destination {1}").format(
				destination.auth_type, destination.name
			)
		)
	return handler(destination, request_kwargs)

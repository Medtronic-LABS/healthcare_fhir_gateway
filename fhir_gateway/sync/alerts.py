"""Dead-letter alert: a short summary POSTed to the deployment's existing alert webhook (the same
`ALERT_WEBHOOK_URL` backup.sh uses, same `{"text": ...}` body), so no new notification channel.

The summary names the resource type, event, destination and error -- never patient data or a
credential. Alerting is best-effort: a missing or failing webhook is logged, never raised, so it
can't undo the dead-letter it reports."""

import os

import frappe
import requests

_TIMEOUT_SECONDS = 10


def alert_webhook_url():
	"""Site config `alert_webhook_url` first, then the container's `ALERT_WEBHOOK_URL` env var."""
	return frappe.conf.get("alert_webhook_url") or os.environ.get("ALERT_WEBHOOK_URL")


def notify_dead_letter(delivery, event, destination):
	url = alert_webhook_url()
	if not url:
		return
	text = (
		f"FHIR sync dead-lettered ({frappe.local.site}): {event.resource_type} event {event.name} "
		f"-> {destination.name}: {delivery.last_error}"
	)
	try:
		requests.post(url, json={"text": text}, timeout=_TIMEOUT_SECONDS).raise_for_status()
	except requests.RequestException:
		frappe.log_error(
			title="FHIR sync dead-letter alert failed",
			reference_doctype=delivery.doctype,
			reference_name=delivery.name,
		)

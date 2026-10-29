"""Outbound sync: push FHIR resources for newly-submitted clinical records to configured
destinations. See docs/superpowers/specs/2026-10-22-outbound-sync-design.md.

triggers.py (on_submit) -> outbox.py (Event + Deliveries) -> dispatcher.py (HTTP, retry) ->
alerts.py (dead-letter); retention.py purges; auth.py applies destination credentials."""

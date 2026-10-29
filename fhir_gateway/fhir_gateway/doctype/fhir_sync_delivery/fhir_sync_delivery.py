# Copyright (c) 2026, Medtronic Labs and contributors
# For license information, please see license.txt

"""One (event, destination) delivery. The dispatcher writes outcomes with db.set_value, so this
controller only runs for an administrator's own edits -- the manual replay."""

from frappe.model.document import Document
from frappe.utils import now_datetime


class FHIRSyncDelivery(Document):
	def validate(self):
		before = self.get_doc_before_save()
		if before and before.status != "Pending" and self.status == "Pending":
			# Manual replay: due now, with the destination's full retry budget again -- otherwise a
			# row that already exhausted its retries would get exactly one more try.
			self.attempt_count = 0
			self.next_attempt_at = now_datetime()

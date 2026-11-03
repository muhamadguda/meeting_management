"""ElevateMeet (user, 3 Nov 2026): Meeting Type values are "Internal Meeting" and "External Meeting"."""

import frappe


def execute():
	for old, new in (("Internal", "Internal Meeting"), ("External", "External Meeting"), ("Client", "External Meeting")):
		frappe.db.set_value("Meeting Schedule", {"meeting_type": old}, "meeting_type", new, update_modified=False)

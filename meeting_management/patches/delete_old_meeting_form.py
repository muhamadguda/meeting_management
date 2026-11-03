"""ElevateMeet (user, 3 Nov 2026): one meeting form. The old Meeting form and its two representative tables are
deleted with their records (the only record, Meet-00001, was an empty test draft; backup kept outside the site)."""

import frappe


def execute():
	for name in frappe.get_all("Meeting", pluck="name") if frappe.db.table_exists("Meeting") else []:
		frappe.db.delete("Meeting", {"name": name})
	for dt in ("Meeting", "Meeting Company Representative", "Meeting Party Representative"):
		if frappe.db.exists("DocType", dt):
			frappe.delete_doc("DocType", dt, force=1, ignore_permissions=True, ignore_missing=True)
	frappe.db.delete("Property Setter", {"doc_type": ["in", ["Meeting", "Meeting Company Representative", "Meeting Party Representative"]]})
	frappe.db.delete("Custom Field", {"dt": ["in", ["Meeting", "Meeting Company Representative", "Meeting Party Representative"]]})

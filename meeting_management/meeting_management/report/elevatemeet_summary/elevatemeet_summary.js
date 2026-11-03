// Copyright (c) 2026, Elevate Holding
// License: MIT

frappe.query_reports["ElevateMeet Summary"] = {
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -56) },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), 7) },
		{ fieldname: "meeting_type", label: __("Meeting With"), fieldtype: "Select", options: "\nClient\nInternal" },
		{ fieldname: "organiser", label: __("Organiser"), fieldtype: "Link", options: "User" },
	],
};

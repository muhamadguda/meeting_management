# Copyright (c) 2026, Elevate Holding
# License: MIT
"""ElevateMeet Summary (Elevate, 3 Nov 2026): meetings per organiser per week, held vs cancelled, notes written, and
how many of their deals reached a proposal."""

import frappe
from frappe import _
from frappe.utils import add_days, getdate


def execute(filters=None):
	filters = filters or {}
	start = getdate(filters.get("from_date") or add_days(getdate(), -56))
	end = getdate(filters.get("to_date") or add_days(getdate(), 7))
	conds = {"scheduled_from": ["between", [str(start) + " 00:00:00", str(end) + " 23:59:59"]]}
	if filters.get("meeting_type"):
		conds["meeting_type"] = filters["meeting_type"]
	if filters.get("organiser"):
		conds["meeting_arranged_by"] = filters["organiser"]
	rows = frappe.get_all("Meeting Schedule", filters=conds,
		fields=["name", "meeting_arranged_by", "scheduled_from", "status", "opportunity"], limit_page_length=0)
	notes = set(frappe.get_all("Meeting", filters={"docstatus": 1, "schedule_ref": ["in", [r.name for r in rows] or [""]]}, pluck="schedule_ref"))
	deals = [r.opportunity for r in rows if r.opportunity]
	proposed = set(frappe.get_all("Opportunity", filters={"name": ["in", deals or [""]], "custom_proposal_sent_on": ["is", "set"]}, pluck="name")) if deals else set()

	groups = {}
	for r in rows:
		day = getdate(r.scheduled_from)
		week = add_days(day, -day.weekday())
		key = (r.meeting_arranged_by or "", week)
		g = groups.setdefault(key, {"organiser": key[0], "week": week, "scheduled": 0, "held": 0, "cancelled": 0, "notes": 0, "proposals": 0})
		g["scheduled"] += 1
		g["held"] += r.status == "Held"
		g["cancelled"] += r.status == "Cancelled"
		g["notes"] += r.name in notes
		g["proposals"] += bool(r.opportunity and r.opportunity in proposed)
	data = sorted(groups.values(), key=lambda g: (g["week"], g["organiser"]), reverse=True)
	names = {u.name: u.full_name for u in frappe.get_all("User", filters={"name": ["in", [g["organiser"] for g in data] or [""]]}, fields=["name", "full_name"])}
	for g in data:
		g["organiser_name"] = names.get(g["organiser"]) or g["organiser"]
		g["held_rate"] = round(100.0 * g["held"] / g["scheduled"], 1) if g["scheduled"] else 0

	columns = [
		{"fieldname": "week", "label": _("Week Of"), "fieldtype": "Date", "width": 110},
		{"fieldname": "organiser_name", "label": _("Organiser"), "fieldtype": "Data", "width": 180},
		{"fieldname": "scheduled", "label": _("Meetings"), "fieldtype": "Int", "width": 100},
		{"fieldname": "held", "label": _("Held"), "fieldtype": "Int", "width": 80},
		{"fieldname": "cancelled", "label": _("Cancelled"), "fieldtype": "Int", "width": 100},
		{"fieldname": "held_rate", "label": _("Held %"), "fieldtype": "Percent", "width": 90},
		{"fieldname": "notes", "label": _("Notes Written"), "fieldtype": "Int", "width": 120},
		{"fieldname": "proposals", "label": _("Led to Proposal"), "fieldtype": "Int", "width": 130},
	]

	weeks = sorted({g["week"] for g in data})
	chart = {
		"data": {
			"labels": [frappe.utils.formatdate(w, "d MMM") for w in weeks],
			"datasets": [
				{"name": _("Held"), "values": [sum(g["held"] for g in data if g["week"] == w) for w in weeks]},
				{"name": _("Cancelled"), "values": [sum(g["cancelled"] for g in data if g["week"] == w) for w in weeks]},
				{"name": _("Not yet held"), "values": [sum(g["scheduled"] - g["held"] - g["cancelled"] for g in data if g["week"] == w) for w in weeks]},
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"colors": ["#14b88a", "#e5306b", "#c0c0c8"],
	}
	return columns, data, None, chart

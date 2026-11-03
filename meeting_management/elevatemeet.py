"""ElevateMeet (Elevate, 3 Nov 2026): what Elevate added to the Meeting Management app.

- BD link: a deal made from a confirmed BD meeting (Log Call > Meeting Confirmed) gets a Meeting Schedule; the
  schedule follows its meeting (Event) when it moves, is cancelled or held. Submitting the meeting notes marks the
  deal Meeting Held, which is what the BD KPI counts.
- Reminders: a Raven message about an hour before, to the organiser and the Elevate attendees.
- Invites: a calendar invite (.ics) from the default outgoing account to the organiser and Elevate attendees, again
  when the time or the attendees change, and a cancellation. The client is emailed only with "Send to Client".
- Internal meetings: Meeting With = Internal, no lead or customer.
- My Meetings: the person's meetings today and this week, and notes not yet written.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, get_url_to_form, now_datetime, getdate, format_datetime

BOT = "ElevateMeet"
OPEN_DEAL_STAGE = "Meeting Confirmed"


# ---------------------------------------------------------------- BD link

def schedule_from_deal(doc, method=None):
	"""Opportunity after_insert: a deal with a BD meeting gets its Meeting Schedule."""
	if not doc.get("custom_meeting") or not frappe.db.exists("Event", doc.custom_meeting):
		return
	if frappe.db.exists("Meeting Schedule", {"bd_event": doc.custom_meeting}):
		frappe.db.set_value("Meeting Schedule", {"bd_event": doc.custom_meeting}, "opportunity", doc.name)
		return
	event = frappe.get_doc("Event", doc.custom_meeting)
	lead = doc.party_name if doc.opportunity_from == "Lead" else None
	organiser = doc.get("opportunity_owner") or event.owner
	schedule = frappe.get_doc({
		"doctype": "Meeting Schedule",
		"meeting_type": "External",
		"party_type": "Lead" if lead else None,
		"party": lead,
		"lead": lead,
		"opportunity": doc.name,
		"bd_event": event.name,
		"scheduled_from": event.starts_on,
		"scheduled_to": event.ends_on or add_to_date(event.starts_on, minutes=60),
		"organisation": doc.get("customer_name") or doc.get("title") or lead or doc.name,
		"meeting_arranged_by": organiser,
		"is_online": 1 if (event.get("location") or "").lower().startswith("online") else 0,
		"meeting_agenda": event.get("description"),
		"attendees": [{"user": u} for u in event_attendees(event.name, organiser)],
	})
	if lead:
		fill_lead_contact(schedule, lead)
	schedule.flags.ignore_mandatory = True
	schedule.insert(ignore_permissions=True)


def event_attendees(event, organiser):
	users = frappe.get_all("DocShare", filters={"share_doctype": "Event", "share_name": event}, pluck="user")
	return [u for u in dict.fromkeys(users) if u and u != organiser and is_staff(u)]


def fill_lead_contact(schedule, lead):
	l = frappe.db.get_value("Lead", lead, ["lead_name", "email_id", "mobile_no", "phone", "company_name"], as_dict=True) or {}
	schedule.contact_person_name = l.get("lead_name")
	schedule.email_id = l.get("email_id")
	schedule.mobile_no = l.get("phone") or l.get("mobile_no")
	if l.get("company_name"):
		schedule.organisation = l.company_name


def follow_event(doc, method=None):
	"""Event on_update: the BD meeting moved, was cancelled or held; its schedule follows."""
	for name in frappe.get_all("Meeting Schedule", filters={"bd_event": doc.name}, pluck="name"):
		s = frappe.get_doc("Meeting Schedule", name)
		if s.status == "Held":
			continue
		changed = False
		if doc.status == "Cancelled" and s.status != "Cancelled":
			s.status, changed = "Cancelled", True
		elif doc.status == "Completed" and s.status != "Held":
			s.status, changed = "Held", True
		if doc.status == "Open" and str(get_datetime(s.scheduled_from)) != str(get_datetime(doc.starts_on)):
			s.scheduled_from = doc.starts_on
			s.scheduled_to = doc.ends_on or add_to_date(doc.starts_on, minutes=60)
			s.status = "Scheduled"
			changed = True
		if changed:
			s.flags.ignore_mandatory = True
			s.save(ignore_permissions=True)


def notes_submitted(doc, method=None):
	"""Meeting on_submit: the schedule is Held and its deal moves to Meeting Held (the BD KPI's meetings held)."""
	if not doc.get("schedule_ref") or not frappe.db.exists("Meeting Schedule", doc.schedule_ref):
		return
	s = frappe.get_doc("Meeting Schedule", doc.schedule_ref)
	if s.status != "Held":
		s.db_set("status", "Held")
	if not s.opportunity or not frappe.db.exists("Opportunity", s.opportunity):
		return
	o = frappe.get_doc("Opportunity", s.opportunity)
	if o.status != "Open" or o.sales_stage != OPEN_DEAL_STAGE:
		return
	if s.bd_event and frappe.db.exists("Event", s.bd_event):
		e = frappe.get_doc("Event", s.bd_event)
		if e.status != "Completed":
			e.status = "Completed"
			e.save(ignore_permissions=True)
	o.sales_stage = "Meeting Held"
	o.custom_meeting_held_on = str(get_datetime(doc.meeting_from or s.scheduled_from))[:10]
	o.save(ignore_permissions=True)
	o.add_comment("Comment", _("Discovery meeting held (meeting notes {0}).").format(doc.name))


# ---------------------------------------------------------------- schedule rules

def schedule_validate(doc, method=None):
	doc.meeting_type = doc.meeting_type or "External"
	doc.status = doc.status or "Scheduled"
	if not doc.meeting_arranged_by:
		doc.meeting_arranged_by = frappe.session.user if frappe.session.user != "Administrator" else None
	if doc.meeting_type == "Internal":
		doc.party_type = doc.party = None
		if not doc.organisation:
			doc.organisation = _("Internal")
	elif not doc.organisation and not doc.flags.ignore_mandatory:
		frappe.throw(_("Pick the lead or job applicant for an external meeting."))
	if doc.scheduled_from and doc.scheduled_to and get_datetime(doc.scheduled_to) < get_datetime(doc.scheduled_from):
		frappe.throw(_("The meeting must end after it starts."))
	seen = set()
	for row in list(doc.get("attendees") or []):
		if not row.user or row.user in seen or row.user == doc.meeting_arranged_by:
			doc.remove(row)
			continue
		seen.add(row.user)
	check_departments(doc, users=[r.user for r in doc.get("attendees") or []])
	before = doc.get_doc_before_save()
	if before and str(before.scheduled_from) != str(doc.scheduled_from):
		doc.reminded = 0


# ---------------------------------------------------------------- internal meetings: departments (option A, 3 Nov 2026)

def meeting_validate(doc, method=None):
	"""Meeting (notes) validate: an internal meeting's Elevate people come from its department(s)."""
	doc.meeting_type = doc.meeting_type or "External"
	if doc.meeting_type == "Internal":
		doc.party_type = doc.party = None
	check_departments(doc, employees=[r.employee for r in doc.get("meeting_company_representative") or [] if r.employee])


def chosen_departments(doc):
	if doc.get("department_scope") == "Same Department":
		return [doc.department] if doc.get("department") else []
	return [r.department for r in doc.get("departments") or [] if r.department]


def check_departments(doc, users=None, employees=None):
	if doc.get("meeting_type") != "Internal":
		doc.department_scope = doc.department = None
		doc.set("departments", [])
		return
	organiser = doc.get("meeting_arranged_by")
	if doc.department_scope == "Same Department" and not doc.department and organiser:
		doc.department = frappe.db.get_value("Employee", {"user_id": organiser, "status": "Active"}, "department")
	if doc.department_scope == "Same Department":
		doc.set("departments", [])
	elif doc.department_scope == "Different Departments":
		doc.department = None
	depts = chosen_departments(doc)
	if not depts:
		return
	outside = []
	for u in users or []:
		if u != organiser and frappe.db.get_value("Employee", {"user_id": u, "status": "Active"}, "department") not in depts:
			outside.append(frappe.db.get_value("User", u, "full_name") or u)
	for e in employees or []:
		row = frappe.db.get_value("Employee", e, ["department", "user_id", "employee_name"], as_dict=True) or {}
		if row.get("user_id") != organiser and row.get("department") not in depts:
			outside.append(row.get("employee_name") or e)
	if outside:
		frappe.throw(_("These people aren't in {0}: {1}").format(", ".join(depts), ", ".join(outside)), title=_("Wrong department"))


@frappe.whitelist()
def organiser_department(user=None):
	return frappe.db.get_value("Employee", {"user_id": user or frappe.session.user, "status": "Active"}, "department")


@frappe.whitelist()
def department_people(departments):
	"""Active employees with a login in the given department(s), for "Add Department Members"."""
	depts = frappe.parse_json(departments) if isinstance(departments, str) else departments
	if not depts:
		return []
	return frappe.get_all("Employee", filters={"status": "Active", "department": ["in", depts], "user_id": ["is", "set"]},
		fields=["name as employee", "employee_name", "user_id as user", "department"], order_by="employee_name asc")


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def staff_query(doctype, txt, searchfield, start, page_len, filters):
	"""The attendee picker: active staff of the chosen department(s), or everyone when none is chosen."""
	depts = (filters or {}).get("departments") or []
	cond = "and e.department in %(depts)s" if depts else ""
	return frappe.db.sql("""select u.name, u.full_name, e.department from `tabUser` u
		left join `tabEmployee` e on e.user_id = u.name and e.status = 'Active'
		where u.enabled = 1 and u.user_type = 'System User' and u.name not in ('Administrator', 'Guest')
		and (u.name like %(t)s or u.full_name like %(t)s) {cond}
		order by u.full_name limit %(start)s, %(len)s""".format(cond=cond),
		{"t": "%" + (txt or "") + "%", "depts": tuple(depts) or ("",), "start": int(start), "len": int(page_len)})


def schedule_on_update(doc, method=None):
	share_with_attendees(doc)
	send_staff_invites(doc)


def share_with_attendees(doc):
	for u in staff_recipients(doc):
		if not frappe.has_permission("Meeting Schedule", "read", doc=doc, user=u):
			frappe.share.add_docshare("Meeting Schedule", doc.name, u, read=1, notify=0, flags={"ignore_share_permission": True})


def staff_recipients(doc):
	users = [doc.meeting_arranged_by] + [r.user for r in doc.get("attendees") or []]
	return [u for u in dict.fromkeys(users) if u and is_staff(u)]


def is_staff(user):
	if user in ("Administrator", "Guest"):
		return False
	return bool(frappe.db.get_value("User", {"name": user, "enabled": 1, "user_type": "System User"}))


# ---------------------------------------------------------------- invites

def settings():
	"""ElevateMeet Settings, with the defaults when the page was never saved."""
	s = frappe.get_cached_doc("ElevateMeet Settings") if frappe.db.exists("DocType", "ElevateMeet Settings") else frappe._dict()
	return frappe._dict(
		send_reminders=1 if s.get("send_reminders") is None else s.get("send_reminders"),
		reminder_minutes=s.get("reminder_minutes") or 60,
		default_duration=s.get("default_duration") or 60,
		send_staff_invites=1 if s.get("send_staff_invites") is None else s.get("send_staff_invites"),
		staff_invite_note=s.get("staff_invite_note") or "",
	)


@frappe.whitelist()
def default_duration():
	return settings().default_duration


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def party_query(doctype, txt, searchfield, start, page_len, filters):
	"""External parties show their ID and name: a lead's company and contact, an applicant's name and job opening."""
	like = "%" + (txt or "") + "%"
	if doctype == "Job Applicant":
		return frappe.db.sql("""select name, applicant_name, job_title from `tabJob Applicant`
			where name like %(t)s or applicant_name like %(t)s or phone_number like %(t)s or email_id like %(t)s
			order by modified desc limit %(start)s, %(len)s""", {"t": like, "start": int(start), "len": int(page_len)})
	return frappe.db.sql("""select name, company_name, lead_name from `tabLead`
		where name like %(t)s or company_name like %(t)s or lead_name like %(t)s or phone like %(t)s
		order by modified desc limit %(start)s, %(len)s""", {"t": like, "start": int(start), "len": int(page_len)})


def send_staff_invites(doc):
	"""A calendar invite to the organiser and Elevate attendees; an update when the time or attendees change; a
	cancellation when the meeting is cancelled."""
	if not settings().send_staff_invites:
		return
	users = staff_recipients(doc)
	emails = sorted(e for e in (frappe.db.get_value("User", u, "email") for u in users) if e)
	key = "|".join([str(doc.scheduled_from), str(doc.scheduled_to), ",".join(emails), doc.status or ""])
	if key == (doc.invite_key or ""):
		return
	previous = [e for e in (doc.invite_sent_to or "").split(",") if e]
	if doc.status == "Cancelled":
		if previous:
			send_ics(doc, previous, (doc.invite_sequence or 0) + 1, cancel=True)
		doc.db_set({"invite_key": key, "invite_sequence": (doc.invite_sequence or 0) + 1}, update_modified=False)
		return
	if doc.status != "Scheduled" or not emails or not doc.scheduled_from:
		doc.db_set("invite_key", key, update_modified=False)
		return
	removed = [e for e in previous if e not in emails]
	sequence = (doc.invite_sequence or 0) + (1 if previous else 0)
	if removed:
		send_ics(doc, removed, sequence, cancel=True)
	send_ics(doc, emails, sequence)
	doc.db_set({"invite_key": key, "invite_sequence": sequence, "invite_sent_to": ",".join(emails)}, update_modified=False)


def ics(doc, attendees, sequence, cancel=False, organiser_email=None):
	stamp = now_datetime().strftime("%Y%m%dT%H%M%S")
	start = get_datetime(doc.scheduled_from).strftime("%Y%m%dT%H%M%S")
	end = get_datetime(doc.scheduled_to or add_to_date(doc.scheduled_from, minutes=60)).strftime("%Y%m%dT%H%M%S")
	tz = frappe.db.get_single_value("System Settings", "time_zone") or "Africa/Cairo"
	sender = organiser_email or default_sender()
	lines = [
		"BEGIN:VCALENDAR", "PRODID:-//Elevate Holding//ElevateMeet//EN", "VERSION:2.0", "CALSCALE:GREGORIAN",
		"METHOD:" + ("CANCEL" if cancel else "REQUEST"), "BEGIN:VEVENT",
		"UID:" + doc.name + "@elevatemeet." + (frappe.local.site or "elevate"),
		"SEQUENCE:" + str(sequence), "DTSTAMP:" + stamp,
		"DTSTART;TZID=" + tz + ":" + start, "DTEND;TZID=" + tz + ":" + end,
		"SUMMARY:" + clean(title(doc)), "ORGANIZER;CN=ElevateMeet:mailto:" + sender,
	]
	lines += ["ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:" + a for a in attendees]
	if doc.get("meeting_agenda"):
		lines.append("DESCRIPTION:" + clean(doc.meeting_agenda))
	lines += ["STATUS:" + ("CANCELLED" if cancel else "CONFIRMED"), "END:VEVENT", "END:VCALENDAR"]
	return "\r\n".join(lines) + "\r\n"


def clean(text):
	import re

	text = re.sub(r"<[^>]+>", " ", str(text or ""))
	return " ".join(text.split()).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")[:900]


def title(doc):
	if doc.get("meeting_type") == "Internal":
		return _("Internal meeting: {0}").format(doc.get("meeting_agenda") and clean(doc.meeting_agenda)[:60] or doc.name)
	return _("Meeting: {0}").format(doc.organisation or doc.party or doc.name)


def default_sender():
	return frappe.db.get_value("Email Account", {"default_outgoing": 1}, "email_id") or "noreply@elevateholding.net"


def send_ics(doc, recipients, sequence, cancel=False, message=None):
	when = format_datetime(doc.scheduled_from, "EEEE d MMM yyyy, HH:mm")
	subject = (_("Cancelled: {0}") if cancel else _("Invitation: {0}")).format(title(doc) + " - " + when)
	body = message or (
		"<p>" + (_("This meeting was cancelled.") if cancel else _("You're invited to this meeting.")) + "</p>"
		+ "<p><b>" + frappe.utils.escape_html(title(doc)) + "</b><br>" + when + "</p>"
		+ ("<p>" + frappe.utils.escape_html(clean(doc.meeting_agenda).replace("\\", "")) + "</p>" if doc.get("meeting_agenda") else "")
		+ ("<p>" + frappe.utils.escape_html(settings().staff_invite_note) + "</p>" if settings().staff_invite_note else "")
		+ '<p><a href="' + get_url_to_form("Meeting Schedule", doc.name) + '">' + _("Open in ElevateMeet") + "</a></p>"
	)
	frappe.sendmail(
		recipients=recipients,
		subject=subject,
		message=body,
		attachments=[{"fname": "invite.ics", "fcontent": ics(doc, recipients, sequence, cancel)}],
		reference_doctype="Meeting Schedule",
		reference_name=doc.name,
	)


@frappe.whitelist()
def send_to_client(name):
	"""The "Send to Client" button: the invitation message and the calendar invite to the client's email (and CC)."""
	doc = frappe.get_doc("Meeting Schedule", name)
	doc.check_permission("write")
	if doc.meeting_type == "Internal":
		frappe.throw(_("An internal meeting has no client."))
	if not doc.email_id:
		frappe.throw(_("Add the client's email first."))
	recipients = [doc.email_id] + [x.strip() for x in (doc.cc_to or "").split(",") if x.strip()]
	send_ics(doc, recipients, doc.invite_sequence or 0, message=doc.invitation_message)
	doc.add_comment("Info", _("Invitation sent to {0}").format(", ".join(recipients)))
	return recipients


# ---------------------------------------------------------------- reminders

def send_reminders():
	"""Every 15 minutes: a Raven message about an hour before each scheduled meeting, once."""
	if "raven" not in frappe.get_installed_apps() or not frappe.db.exists("Raven Bot", BOT) or not settings().send_reminders:
		return
	now = now_datetime()
	due = frappe.get_all("Meeting Schedule", filters={
		"status": "Scheduled", "reminded": 0,
		"scheduled_from": ["between", [now, add_to_date(now, minutes=int(settings().reminder_minutes) + 15)]],
	}, pluck="name")
	if not due:
		return
	bot = frappe.get_doc("Raven Bot", BOT)
	for name in due:
		s = frappe.get_doc("Meeting Schedule", name)
		html = (
			"<p><strong>" + frappe.utils.escape_html(title(s)) + "</strong></p>"
			+ "<p>" + _("Starts at {0}.").format(format_datetime(s.scheduled_from, "HH:mm")) + "</p>"
			+ '<p><a href="' + get_url_to_form("Meeting Schedule", s.name) + '">' + _("Open the meeting") + "</a></p>"
		)
		for u in staff_recipients(s):
			if frappe.db.exists("Raven User", {"user": u}):
				try:
					bot.send_direct_message(u, html)
				except Exception:
					frappe.log_error(title="ElevateMeet: reminder failed", message=frappe.get_traceback())
		s.db_set("reminded", 1, update_modified=False)
	frappe.db.commit()


def make_bot():
	if "raven" in frappe.get_installed_apps() and not frappe.db.exists("Raven Bot", BOT):
		frappe.get_doc({"doctype": "Raven Bot", "bot_name": BOT,
						"description": "Reminds people of their meetings an hour before."}).insert(ignore_permissions=True)


# ---------------------------------------------------------------- My Meetings

@frappe.whitelist()
def my_meetings():
	user = frappe.session.user
	mine = set(frappe.get_all("Meeting Schedule", filters={"meeting_arranged_by": user}, pluck="name"))
	mine |= set(frappe.get_all("Meeting Schedule Attendee", filters={"user": user, "parenttype": "Meeting Schedule"}, pluck="parent"))
	today = getdate()
	week_end = add_to_date(today, days=7)
	fields = ["name", "organisation", "meeting_type", "scheduled_from", "scheduled_to", "status", "meeting_arranged_by", "is_online"]
	rows = frappe.get_all("Meeting Schedule", filters={"name": ["in", list(mine) or [""]]}, fields=fields, order_by="scheduled_from asc")
	written = set(frappe.get_all("Meeting", filters={"schedule_ref": ["in", [r.name for r in rows] or [""]], "docstatus": ["<", 2]}, pluck="schedule_ref"))
	out = {"today": [], "week": [], "notes_due": []}
	for r in rows:
		day = getdate(r.scheduled_from)
		r.notes = r.name in written
		if r.status == "Scheduled" and day == today:
			out["today"].append(r)
		elif r.status == "Scheduled" and today < day <= getdate(week_end):
			out["week"].append(r)
		if r.meeting_arranged_by == user and r.status != "Cancelled" and get_datetime(r.scheduled_to or r.scheduled_from) < now_datetime() and not r.notes:
			out["notes_due"].append(r)
	return out

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

BOT = "Meetings Bot"   # user, 3 Nov 2026
COPY_TO = "mohamed.emad@elevateholding.net"   # user, 4 Nov 2026: gets a Raven copy of every reminder
OPEN_DEAL_STAGE = "Meeting Confirmed"


# ---------------------------------------------------------------- BD link

def schedule_from_deal(doc, method=None):
	"""Opportunity after_insert: the deal joins the meeting booked for it (made at booking), or gets one."""
	if not doc.get("custom_meeting") or not frappe.db.exists("Event", doc.custom_meeting):
		return
	if frappe.db.exists("Meeting Schedule", {"bd_event": doc.custom_meeting}):
		frappe.db.set_value("Meeting Schedule", {"bd_event": doc.custom_meeting}, "opportunity", doc.name)
		return
	lead = doc.party_name if doc.opportunity_from == "Lead" else None
	bd_schedule(frappe.get_doc("Event", doc.custom_meeting), lead, doc.get("opportunity_owner"), doc)


def schedule_from_booking(doc, method=None):
	"""Event after_insert (user, 3 Nov 2026): a BD meeting booked on a lead (Log Call > Meeting Booked) is a meeting in
	ElevateMeet straight away, with the BDE as organiser."""
	if doc.get("event_category") != "Meeting" or frappe.db.exists("Meeting Schedule", {"bd_event": doc.name}):
		return
	lead = next((p.reference_docname for p in doc.get("event_participants") or [] if p.reference_doctype == "Lead"), None)
	if lead:
		bd_schedule(doc, lead, doc.owner)


def bd_schedule(event, lead, organiser=None, deal=None):
	organiser = organiser or event.owner
	schedule = frappe.get_doc({
		"doctype": "Meeting Schedule",
		"meeting_type": "External Meeting",
		"party_type": "Lead" if lead else None,
		"party": lead,
		"lead": lead,
		"opportunity": deal.name if deal else None,
		"bd_event": event.name,
		"scheduled_from": event.starts_on,
		"scheduled_to": event.ends_on or add_to_date(event.starts_on, minutes=60),
		"organisation": (deal and (deal.get("customer_name") or deal.get("title"))) or lead,
		"meeting_arranged_by": organiser,
		"is_online": 1 if (event.get("location") or "").lower().startswith("online") else 0,
		"meeting_agenda": event.get("description"),
		"attendees": [{"user": u} for u in event_attendees(event.name, organiser)],
	})
	if lead:
		fill_lead_contact(schedule, lead)
	schedule.flags.ignore_mandatory = True
	schedule.flags.from_deal = True
	schedule.insert(ignore_permissions=True)


def event_share_attendee(doc, method=None):
	"""DocShare after_insert: a person shared on a BD meeting joins its ElevateMeet meeting (Log Call shares the meeting
	with the attendees right after booking it)."""
	if doc.share_doctype != "Event" or not doc.user or not is_staff(doc.user):
		return
	name = frappe.db.get_value("Meeting Schedule", {"bd_event": doc.share_name})
	if not name:
		return
	s = frappe.get_doc("Meeting Schedule", name)
	if s.status != "Scheduled" or doc.user == s.meeting_arranged_by or doc.user in [r.user for r in s.get("attendees") or []]:
		return
	s.append("attendees", {"user": doc.user})
	s.flags.ignore_mandatory = True
	s.save(ignore_permissions=True)


INTERVIEW_HELD = ("Cleared", "Rejected", "Passed", "Failed")
INTERVIEW_CANCELLED = ("Cancelled", "No Show")
# user, 4 Nov 2026: Operations / Client interviews go to the operations head (To) and the operations team leaders (CC),
# who all become attendees; the booking mail + Raven DMs come from Meetings Bot
OPS_INTERVIEW_TYPES = ("Operations Interview", "Client Interview")
OPS_HEAD = "osama.youssef@elevateholding.net"
OPS_TEAM_PROFILE = "Operations Team"


def ops_team_leaders():
	"""The operations team leaders: enabled users with the "Operations Team" role profile, except the operations head."""
	users = frappe.get_all("User Role Profile", filters={"role_profile": OPS_TEAM_PROFILE, "parenttype": "User"}, pluck="parent")
	return [u for u in dict.fromkeys(users) if u != OPS_HEAD and is_staff(u)]


def is_ops_interview(doc):
	return bool(doc.get("interview")) and frappe.db.get_value("Interview", doc.interview, "interview_type") in OPS_INTERVIEW_TYPES


def ops_interview_notes(interview, applicant, start):
	"""The Notes of an Operations / Client interview meeting: who the operations head interviews, and when."""
	esc = frappe.utils.escape_html
	head = (frappe.db.get_value("User", OPS_HEAD, "first_name") or "").strip()
	lob = interview.get("custom_pitch_account") or frappe.db.get_value("Job Applicant", interview.job_applicant, "custom_recruitment_account")
	greet = (esc(head) + ", you") if head else "You"
	return ("<p>" + greet + " have someone to interview: <b>" + esc(applicant or interview.job_applicant)
		+ "</b> (" + esc(interview.job_applicant) + "), " + esc(interview.get("interview_type") or "Interview") + " on <b>"
		+ esc(format_datetime(start, "EEEE d MMM, HH:mm")) + "</b>." + (" LOB: " + esc(lob) + "." if lob else "") + "</p>")


def interview_meeting(doc, method=None):
	"""Interview after_insert / on_update (user, 3 Nov 2026): an interview booked ahead with a job applicant is a
	meeting in ElevateMeet (the recruiter organises, the interviewers attend) and follows the interview's time and result.
	An interview logged after the fact (its start already passed when first saved) doesn't become a meeting."""
	if not doc.get("job_applicant") or not doc.get("scheduled_on") or not doc.get("from_time"):
		return
	start = get_datetime(str(doc.scheduled_on) + " " + str(doc.from_time))
	end = get_datetime(str(doc.scheduled_on) + " " + str(doc.to_time)) if doc.get("to_time") else None
	if not end or end <= start:
		end = add_to_date(start, minutes=30)
	name = frappe.db.get_value("Meeting Schedule", {"interview": doc.name})
	if not name and start <= now_datetime():
		return
	status = "Cancelled" if doc.status in INTERVIEW_CANCELLED else "Held" if doc.status in INTERVIEW_HELD else "Scheduled"
	organiser = doc.get("custom_recruiter") or doc.owner
	people = [r.interviewer for r in doc.get("interview_details") or [] if r.get("interviewer")]
	if name:
		s = frappe.get_doc("Meeting Schedule", name)
		s.scheduled_from, s.scheduled_to = start, end
		# user, 4 Nov 2026: Done / Passed Due are kept; a result still marks Held / Cancelled, a move to the future reopens
		if status in ("Held", "Cancelled"):
			if s.status not in ("Held", "Done"):
				s.status = status
		elif s.status in ("Passed Due", "Cancelled") and get_datetime(end) > now_datetime():
			s.status = "Scheduled"
		have = {r.user for r in s.get("attendees") or []}
		for u in people:
			if u not in have and u != s.meeting_arranged_by:
				s.append("attendees", {"user": u})
		s.flags.ignore_mandatory = True
		s.save(ignore_permissions=True)
		return
	a = frappe.db.get_value("Job Applicant", doc.job_applicant, ["applicant_name", "email_id", "phone_number"], as_dict=True) or {}
	kind = doc.get("interview_type") or doc.get("interview_round") or _("Interview")
	ops = doc.get("interview_type") in OPS_INTERVIEW_TYPES
	if ops:   # user, 4 Nov 2026: the operations head and the team leaders attend
		people = people + [OPS_HEAD] + ops_team_leaders()
	s = frappe.get_doc({
		"doctype": "Meeting Schedule",
		"meeting_type": "External Meeting",
		"party_type": "Job Applicant",
		"party": doc.job_applicant,
		"organisation": a.get("applicant_name"),
		"contact_person_name": a.get("applicant_name"),
		"email_id": a.get("email_id"),
		"mobile_no": a.get("phone_number"),
		"interview": doc.name,
		"status": status,
		"scheduled_from": start,
		"scheduled_to": end,
		"meeting_arranged_by": organiser,
		"meeting_agenda": _("{0} with {1}").format(kind, a.get("applicant_name") or doc.job_applicant),
		"attendees": [{"user": u} for u in dict.fromkeys(people) if u != organiser],
		"discussion": ops_interview_notes(doc, a.get("applicant_name"), start) if ops else None,
	})
	s.flags.ignore_mandatory = True
	s.flags.from_deal = True
	s.insert(ignore_permissions=True)


def unlink_on_party(doc, method=None):
	"""Meeting Schedule on_trash: the lead's or applicant's "Meeting" link moves to its previous meeting (or clears), so a
	meeting can be deleted (found in the 3 Nov 2026 demo)."""
	for dt in ("Lead", "Job Applicant"):
		if not frappe.get_meta(dt).has_field("custom_meeting_schedule"):
			continue
		for party in frappe.get_all(dt, filters={"custom_meeting_schedule": doc.name}, pluck="name"):
			prev = frappe.get_all("Meeting Schedule", filters={"party_type": dt, "party": party, "name": ["!=", doc.name]},
				pluck="name", order_by="scheduled_from desc, creation desc", limit=1)
			frappe.db.set_value(dt, party, "custom_meeting_schedule", prev[0] if prev else None, update_modified=False)


def link_on_party(doc):
	"""The lead's or applicant's "Meeting" link shows its latest meeting (user, 3 Nov 2026)."""
	if doc.party_type in ("Lead", "Job Applicant") and doc.party and frappe.get_meta(doc.party_type).has_field("custom_meeting_schedule"):
		latest = frappe.get_all("Meeting Schedule", filters={"party_type": doc.party_type, "party": doc.party},
			pluck="name", order_by="scheduled_from desc, creation desc", limit=1)
		if latest:
			frappe.db.set_value(doc.party_type, doc.party, "custom_meeting_schedule", latest[0], update_modified=False)


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


@frappe.whitelist()
def mark_held(name):
	"""Option A (3 Nov 2026): one meeting form. Mark as Held closes the meeting: Held + time, the BD meeting completed,
	the deal at Meeting Held (BD KPI), and the discussion on the lead's or applicant's timeline."""
	s = frappe.get_doc("Meeting Schedule", name)
	s.check_permission("write")
	if s.status == "Cancelled":
		frappe.throw(_("This meeting was cancelled."))
	if s.status == "Held":
		return
	s.status = "Held"
	s.held_on = now_datetime()
	s.flags.ignore_mandatory = True
	s.save()
	if s.bd_event and frappe.db.exists("Event", s.bd_event):
		e = frappe.get_doc("Event", s.bd_event)
		if e.status != "Completed":
			e.status = "Completed"
			e.save(ignore_permissions=True)
	if s.opportunity and frappe.db.exists("Opportunity", s.opportunity):
		o = frappe.get_doc("Opportunity", s.opportunity)
		if o.status == "Open" and o.sales_stage == OPEN_DEAL_STAGE:
			o.sales_stage = "Meeting Held"
			o.custom_meeting_held_on = str(get_datetime(s.scheduled_from or s.held_on))[:10]
			o.save(ignore_permissions=True)
			o.add_comment("Comment", _("Discovery meeting held ({0}).").format(s.name))
	if s.party_type and s.party and frappe.db.exists(s.party_type, s.party):
		who = person_name(frappe.session.user)
		text = "<b>" + _("Meeting held") + "</b> (<a href='" + get_url_to_form("Meeting Schedule", s.name) + "'>" + s.name + "</a>) " + _("by {0}").format(who)
		if s.discussion:
			text += "<br>" + s.discussion
		frappe.get_doc(s.party_type, s.party).add_comment("Comment", text)


# ---------------------------------------------------------------- schedule rules

def schedule_validate(doc, method=None):
	doc.meeting_type = doc.meeting_type or "External Meeting"
	doc.status = doc.status or "Scheduled"
	if doc.is_new() and not doc.flags.from_deal:
		# the organiser is always the person scheduling it (read-only on the form); a BD meeting keeps the deal's BDE
		doc.meeting_arranged_by = frappe.session.user
	if doc.meeting_type == "Internal Meeting":
		doc.party_type = doc.party = None
		if not doc.organisation:
			doc.organisation = _("Internal Meeting")
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


def chosen_departments(doc):
	if doc.get("department_scope") == "Same Department":
		return [doc.department] if doc.get("department") else []
	return [r.department for r in doc.get("departments") or [] if r.department]


def check_departments(doc, users=None, employees=None):
	if doc.get("meeting_type") != "Internal Meeting":
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
	# user, 4 Nov 2026: the organiser's own department may always be invited too
	own = organiser and frappe.db.get_value("Employee", {"user_id": organiser, "status": "Active"}, "department")
	allowed = depts + ([own] if own and own not in depts else [])
	outside = []
	for u in users or []:
		if u != organiser and frappe.db.get_value("Employee", {"user_id": u, "status": "Active"}, "department") not in allowed:
			outside.append(frappe.db.get_value("User", u, "full_name") or u)
	for e in employees or []:
		row = frappe.db.get_value("Employee", e, ["department", "user_id", "employee_name"], as_dict=True) or {}
		if row.get("user_id") != organiser and row.get("department") not in allowed:
			outside.append(row.get("employee_name") or e)
	if outside:
		frappe.throw(_("These people aren't in {0}: {1}").format(", ".join(allowed), ", ".join(outside)), title=_("Wrong department"))


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
	"""The attendee picker: active staff of the chosen department(s) plus the organiser's own department (user,
	4 Nov 2026), or everyone when none is chosen."""
	depts = list((filters or {}).get("departments") or [])
	own = depts and organiser_department((filters or {}).get("organiser"))
	if own and own not in depts:
		depts.append(own)
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
	link_on_party(doc)


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
	head = frappe.db.get_value("User", OPS_HEAD, "email")
	if not previous and head in emails and is_ops_interview(doc):
		ops_booking_notice(doc, emails, sequence, head)
	else:
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
	if doc.get("meeting_type") == "Internal Meeting":
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


def ops_booking_notice(doc, emails, sequence, head):
	"""user, 4 Nov 2026: an Operations / Client interview was booked. ONE mail (To the operations head, CC the organiser,
	the team leaders and the other interviewers) with the meeting, its Notes and the calendar invite; and a Meetings Bot
	Raven DM to each of them (the admin gets a copy)."""
	esc = frappe.utils.escape_html
	rows = reminder_lines(doc)[1]
	link = get_url_to_form("Meeting Schedule", doc.name)
	heading = _("Interview booked: {0}").format(title(doc))
	notes = frappe.utils.sanitize_html(doc.discussion or "")
	table = "".join('<tr><td style="padding:4px 14px 4px 0;color:#5A5A78">' + esc(k) + '</td><td style="padding:4px 0">' + esc(v) + "</td></tr>" for k, v in rows)
	body = ('<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.6;color:#26255E">'
		+ '<p style="font-size:18px;font-weight:bold;margin:0 0 10px">' + esc(heading) + "</p><table>" + table + "</table>"
		+ ('<p style="font-weight:bold;margin:18px 0 4px">' + _("Notes") + "</p><div>" + notes + "</div>" if notes.strip() else "")
		+ '<p style="margin:20px 0"><a href="' + link + '" style="background:#e5306b;color:#ffffff;text-decoration:none;border-radius:6px;padding:11px 22px;font-weight:bold;display:inline-block">' + _("Open the meeting") + "</a></p></div>")
	cc = [e for e in emails if e != head]
	frappe.sendmail(recipients=[head], cc=cc, subject=heading + " - " + format_datetime(doc.scheduled_from, "EEEE d MMM, HH:mm"),
		message=body, attachments=[{"fname": "invite.ics", "fcontent": ics(doc, emails, sequence)}],
		reference_doctype="Meeting Schedule", reference_name=doc.name, expose_recipients="header")
	if "raven" not in frappe.get_installed_apps() or not frappe.db.exists("Raven Bot", BOT):
		return
	raven = ("<p><strong>" + esc(_("New interview booked")) + "</strong></p><p><b>" + esc(title(doc)) + "</b><br>"
		+ "<br>".join("<b>" + esc(k) + ":</b> " + esc(v) for k, v in rows) + "</p>" + (notes if notes.strip() else "")
		+ '<p><a href="' + link + '">' + _("Open the meeting") + "</a></p>")
	sent = []
	for u in staff_recipients(doc):
		if not frappe.db.exists("Raven User", {"user": u}):
			continue
		try:
			frappe.get_doc("Raven Bot", BOT).send_direct_message(u, raven)
			sent.append(u)
		except Exception:
			frappe.log_error(title="Meetings Bot: interview booking DM failed", message=frappe.get_traceback())
	if sent and COPY_TO not in sent and frappe.db.exists("Raven User", {"user": COPY_TO}):
		try:
			frappe.get_doc("Raven Bot", BOT).send_direct_message(COPY_TO, "<p><i>" + esc(_("Copy - sent to {0}").format(
				", ".join(person_name(u) for u in sent))) + "</i></p>" + raven)
		except Exception:
			frappe.log_error(title="Meetings Bot: admin copy failed", message=frappe.get_traceback())


@frappe.whitelist()
def send_to_client(name):
	"""The "Send to Client" button: the invitation message and the calendar invite to the client's email (and CC)."""
	doc = frappe.get_doc("Meeting Schedule", name)
	doc.check_permission("write")
	if doc.meeting_type == "Internal Meeting":
		frappe.throw(_("An internal meeting has no client."))
	if not doc.email_id:
		frappe.throw(_("Add the client's email first."))
	cc = [frappe.db.get_value("User", r.user, "email") for r in doc.get("cc_people") or [] if r.user]
	recipients = [doc.email_id] + [e for e in cc if e] + [x.strip() for x in (doc.cc_to or "").split(",") if x.strip()]
	recipients = list(dict.fromkeys(recipients))
	send_ics(doc, recipients, doc.invite_sequence or 0, message=doc.invitation_message)
	doc.add_comment("Info", _("Invitation sent to {0}").format(", ".join(recipients)))
	return recipients


# ---------------------------------------------------------------- reminders (Meetings Bot)

def mark_passed_due():
	"""Every 15 minutes (user, 4 Nov 2026): a meeting still Scheduled after its end time becomes Passed Due. People can
	still set it by hand (Done, Held...)."""
	now = now_datetime()
	for r in frappe.get_all("Meeting Schedule", filters={"status": "Scheduled", "scheduled_from": ["<", now]},
			fields=["name", "scheduled_from", "scheduled_to"]):
		if get_datetime(r.scheduled_to or add_to_date(r.scheduled_from, minutes=60)) < now:
			frappe.db.set_value("Meeting Schedule", r.name, "status", "Passed Due", update_modified=False)


def send_reminders():
	"""Every 15 minutes: before each scheduled meeting, Meetings Bot reminds the people in it, each about their own
	meeting only (the organiser and the Elevate attendees), on Raven and by email. Once per meeting."""
	if not settings().send_reminders:
		return
	now = now_datetime()
	due = frappe.get_all("Meeting Schedule", filters={
		"status": "Scheduled", "reminded": 0,
		"scheduled_from": ["between", [now, add_to_date(now, minutes=int(settings().reminder_minutes) + 15)]],
	}, pluck="name")
	for name in due:
		s = frappe.get_doc("Meeting Schedule", name)
		for user in staff_recipients(s):
			remind(user, s)
		s.db_set("reminded", 1, update_modified=False)
	frappe.db.commit()


def person_name(user):
	"""The person's full name with the middle name (User full name, e.g. Mohamed Emad Amer); frappe's get_fullname drops
	the middle name. The Employee name is the fallback."""
	return (frappe.db.get_value("User", user, "full_name")
		or frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "employee_name") or user)


def reminder_lines(s):
	"""The meeting's details, the same in Raven and in the email."""
	start = get_datetime(s.scheduled_from)
	end = get_datetime(s.scheduled_to) if s.scheduled_to else None
	minutes = max(0, int((start - now_datetime()).total_seconds() // 60))
	when = format_datetime(start, "EEEE d MMM, HH:mm") + (" - " + format_datetime(end, "HH:mm") if end else "")
	if s.get("meeting_type") == "Internal Meeting":
		depts = chosen_departments(s)
		with_whom = _("Internal Meeting") + (" (" + ", ".join(depts) + ")" if depts else "")
	else:
		with_whom = (s.organisation or s.party or "") + (" - " + _(s.party_type) if s.party_type else "")
	rows = [(_("When"), when), (_("With"), with_whom), (_("Organiser"), person_name(s.meeting_arranged_by) if s.meeting_arranged_by else "")]
	if s.is_online:
		rows.append((_("Where"), _("Online")))
	if s.get("meeting_agenda"):
		rows.append((_("Agenda"), clean(s.meeting_agenda).replace("\\", "")))
	return minutes, [(k, v) for k, v in rows if v]


def remind(user, s, test=False):
	minutes, rows = reminder_lines(s)
	head = (_("TEST - ") if test else "") + (_("Your meeting starts in {0} minutes").format(minutes) if minutes else _("Your meeting is starting now"))
	link = get_url_to_form("Meeting Schedule", s.name)
	esc = frappe.utils.escape_html
	first = esc((frappe.db.get_value("User", user, "first_name") or "").strip())
	raven = ("<p><strong>" + esc(head) + "</strong></p>" + ("<p>" + _("Hi {0},").format(first) + "</p>" if first else "")
		+ "<p><b>" + esc(title(s)) + "</b><br>" + "<br>".join("<b>" + esc(k) + ":</b> " + esc(v) for k, v in rows) + "</p>"
		+ '<p><a href="' + link + '">' + _("Open the meeting") + "</a></p>")
	if "raven" in frappe.get_installed_apps() and frappe.db.exists("Raven Bot", BOT) and frappe.db.exists("Raven User", {"user": user}):
		try:
			frappe.get_doc("Raven Bot", BOT).send_direct_message(user, raven)
		except Exception:
			frappe.log_error(title="Meetings Bot: Raven reminder failed", message=frappe.get_traceback())
	# user, 4 Nov 2026: a Raven copy of every reminder goes to the admin (not for test sends)
	if not test and user != COPY_TO and "raven" in frappe.get_installed_apps() and frappe.db.exists("Raven Bot", BOT) \
			and frappe.db.exists("Raven User", {"user": COPY_TO}):
		try:
			frappe.get_doc("Raven Bot", BOT).send_direct_message(COPY_TO,
				"<p><i>" + esc(_("Copy - sent to {0}").format(person_name(user))) + "</i></p>" + raven)
		except Exception:
			frappe.log_error(title="Meetings Bot: admin copy failed", message=frappe.get_traceback())
	email = frappe.db.get_value("User", user, "email")
	if not email:
		return
	table = "".join('<tr><td style="padding:4px 14px 4px 0;color:#5A5A78">' + esc(k) + '</td><td style="padding:4px 0">' + esc(v) + "</td></tr>" for k, v in rows)
	body = ('<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.6;color:#26255E">'
		+ '<p style="font-size:18px;font-weight:bold;margin:0 0 6px">' + esc(head) + "</p>"
		+ ("<p>" + _("Hi {0},").format(first) + "</p>" if first else "")
		+ '<p style="font-weight:bold;margin:12px 0 4px">' + esc(title(s)) + "</p><table>" + table + "</table>"
		+ '<p style="margin:20px 0"><a href="' + link + '" style="background:#e5306b;color:#ffffff;text-decoration:none;border-radius:6px;padding:11px 22px;font-weight:bold;display:inline-block">' + _("Open the meeting") + "</a></p>"
		+ '<p style="color:#5A5A78;font-size:13px">' + _("You get this because you are in this meeting. Meetings Bot only reminds you about your own meetings.") + "</p></div>")
	try:
		frappe.sendmail(recipients=[email], subject=head + ": " + title(s), message=body,
			reference_doctype="Meeting Schedule", reference_name=s.name, now=test)
	except Exception:
		frappe.log_error(title="Meetings Bot: email reminder failed", message=frappe.get_traceback())


@frappe.whitelist()
def mail_recipients(name):
	"""Send Mail's To and CC (user, 3 Nov 2026): To = the client for an External Meeting, else the organiser;
	CC = the organiser and Elevate attendees (and CC To people), so everyone sees the same email."""
	s = frappe.get_doc("Meeting Schedule", name)
	s.check_permission("read")
	staff = [frappe.db.get_value("User", u, "email") for u in staff_recipients(s)]
	if s.get("meeting_type") == "External Meeting":
		staff += [frappe.db.get_value("User", r.user, "email") for r in s.get("cc_people") or [] if r.user]
	organiser = frappe.db.get_value("User", s.meeting_arranged_by, "email") if s.meeting_arranged_by and is_staff(s.meeting_arranged_by) else None
	# user, 3 Nov 2026: To starts empty (the sender fills it); CC has the organiser and the Elevate attendees
	cc = list(dict.fromkeys(e for e in ([organiser] + staff) if e))
	return {"to": [], "cc": cc}


def parse_emails(value):
	if isinstance(value, str) and value.strip().startswith("["):
		items = frappe.parse_json(value)
	else:
		items = [x.strip() for x in str(value or "").replace(";", ",").replace("\n", ",").split(",") if x.strip()]
	return list(dict.fromkeys(e for e in items if frappe.utils.validate_email_address(e)))


@frappe.whitelist()
def send_meeting_mail(name, to, cc=None):
	"""The "Send Mail" button (user, 3 Nov 2026): ONE email with To and CC, so everyone sees who got it; the meeting in
	the reminder's layout with its Notes. The "Open the meeting" button is left out when someone outside Elevate is on
	it (they can't sign in)."""
	s = frappe.get_doc("Meeting Schedule", name)
	s.check_permission("write")
	to, cc = parse_emails(to), parse_emails(cc)
	cc = [e for e in cc if e not in to]
	if not to:
		frappe.throw(_("Add at least one address in To."))
	everyone = to + cc
	staff = set(frappe.get_all("User", filters={"email": ["in", everyone], "enabled": 1, "user_type": "System User"}, pluck="email"))
	with_button = all(e in staff for e in everyone)
	rows = reminder_lines(s)[1]
	heading = title(s)
	if s.get("meeting_type") == "Internal Meeting":
		# user, 4 Nov 2026: the title = the sender's department - the meeting's chosen departments (the people's
		# departments only when none was chosen); the table shows To and CC by name instead of "With"
		me = frappe.db.get_value("User", frappe.session.user, "email")

		def names_of(emails):
			out = []
			for e in emails:
				u = frappe.db.get_value("User", {"email": e}, "name")
				out.append(person_name(u) if u else e)
			return out

		mine = (frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "department")
			or frappe.db.get_value("Employee", {"user_id": s.meeting_arranged_by, "status": "Active"}, "department"))
		theirs = [d for d in chosen_departments(s) if d != mine]
		if not theirs:
			users = frappe.get_all("User", filters={"email": ["in", [e for e in to + cc if e != me] or [""]]}, pluck="name")
			theirs = list(dict.fromkeys(d for d in (frappe.db.get_value("Employee", {"user_id": u, "status": "Active"}, "department") for u in users) if d and d != mine))
		heading = _("Internal meeting: {0}").format(" - ".join(x for x in [mine or _("Elevate"), ", ".join(theirs)] if x))
		people_rows = [(_("To"), ", ".join(names_of(to))), (_("CC"), ", ".join(names_of([e for e in cc if e != me])))]
		rows = [r for k, v in rows for r in ([(k, v)] if k != _("With") else people_rows) if r[1]]
	esc = frappe.utils.escape_html
	table = "".join('<tr><td style="padding:4px 14px 4px 0;color:#5A5A78">' + esc(k) + '</td><td style="padding:4px 0">' + esc(v) + "</td></tr>" for k, v in rows)
	notes = frappe.utils.sanitize_html(s.discussion or "")
	link = get_url_to_form("Meeting Schedule", s.name)
	body = ('<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.6;color:#26255E">'
		+ '<p style="font-size:18px;font-weight:bold;margin:0 0 10px">' + esc(heading) + "</p><table>" + table + "</table>"
		+ ('<p style="font-weight:bold;margin:18px 0 4px">' + _("Notes") + "</p><div>" + notes + "</div>" if notes.strip() else "")
		+ ('<p style="margin:20px 0"><a href="' + link + '" style="background:#e5306b;color:#ffffff;text-decoration:none;border-radius:6px;padding:11px 22px;font-weight:bold;display:inline-block">' + _("Open the meeting") + "</a></p>" if with_button else "")
		+ "</div>")
	subject = heading + (" - " + format_datetime(s.scheduled_from, "EEEE d MMM, HH:mm") if s.scheduled_from else "")
	# expose_recipients="header": everyone sees the real To and CC (Frappe otherwise puts each person alone in To)
	frappe.sendmail(recipients=to, cc=cc, subject=subject, message=body, reference_doctype="Meeting Schedule", reference_name=s.name,
		expose_recipients="header")
	s.add_comment("Info", _("Meeting mail sent. To: {0}. CC: {1}").format(", ".join(to), ", ".join(cc) or "-"))
	return {"to": to, "cc": cc}


@frappe.whitelist()
def send_test_reminder():
	"""Sends the caller (only) a test reminder for their next meeting, or for a sample meeting if they have none."""
	user = frappe.session.user
	mine = frappe.get_all("Meeting Schedule", filters={"status": "Scheduled", "meeting_arranged_by": user,
		"scheduled_from": [">", now_datetime()]}, pluck="name", order_by="scheduled_from asc", limit=1)
	if mine:
		s = frappe.get_doc("Meeting Schedule", mine[0])
	else:
		s = frappe.get_doc({"doctype": "Meeting Schedule", "name": "SCH-TEST", "meeting_type": "External Meeting", "party_type": "Lead",
			"organisation": "Sample Company", "meeting_arranged_by": user, "is_online": 1, "meeting_agenda": "Sample meeting for the reminder test",
			"scheduled_from": add_to_date(now_datetime(), minutes=60), "scheduled_to": add_to_date(now_datetime(), minutes=120)})
	remind(user, s, test=True)
	return s.name


def make_bot():
	if "raven" in frappe.get_installed_apps() and not frappe.db.exists("Raven Bot", BOT):
		frappe.get_doc({"doctype": "Raven Bot", "bot_name": BOT,
						"description": "Reminds each person about their own meetings, on Raven and by email."}).insert(ignore_permissions=True)


# ---------------------------------------------------------------- My Meetings

@frappe.whitelist()
def my_meetings():
	user = frappe.session.user
	mine = set(frappe.get_all("Meeting Schedule", filters={"meeting_arranged_by": user}, pluck="name"))
	mine |= set(frappe.get_all("Meeting Schedule Attendee", filters={"user": user, "parenttype": "Meeting Schedule"}, pluck="parent"))
	today = getdate()
	week_end = add_to_date(today, days=7)
	fields = ["name", "organisation", "meeting_type", "scheduled_from", "scheduled_to", "status", "meeting_arranged_by", "is_online"]
	rows = frappe.get_all("Meeting Schedule", filters={"name": ["in", list(mine) or [""]], "scheduled_from": ["is", "set"]},
		fields=fields, order_by="scheduled_from asc")
	out = {"today": [], "week": [], "notes_due": []}
	for r in rows:
		day = getdate(r.scheduled_from)
		r.notes = r.status == "Held"   # one form (option A): a held meeting has its notes
		if r.status == "Scheduled" and day == today:
			out["today"].append(r)
		elif r.status == "Scheduled" and today < day <= getdate(week_end):
			out["week"].append(r)
		if r.meeting_arranged_by == user and r.status != "Cancelled" and get_datetime(r.scheduled_to or r.scheduled_from) < now_datetime() and not r.notes:
			out["notes_due"].append(r)
	return out

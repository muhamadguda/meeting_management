// Copyright (c) 2026, Elevate Holding
// License: MIT
// My Meetings (Elevate, 3 Nov 2026): today's and this week's meetings, and meeting notes still to write.

frappe.pages["my-meetings"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("My Meetings"), single_column: true });
	page.set_primary_action(__("New Meeting"), () => frappe.new_doc("Meeting Schedule"), "add");
	page.add_inner_button(__("Calendar"), () => frappe.set_route("meeting-schedule", "view", "calendar"));
	page.add_inner_button(__("Refresh"), () => load());
	const body = $('<div class="my-meetings" style="max-width: 900px; margin: 0 auto; padding: 8px 0 24px;"></div>').appendTo(page.body);

	const when = (r) => {
		const from = frappe.datetime.str_to_user(r.scheduled_from).split(" ");
		return `${from[0]} · ${(r.scheduled_from || "").substr(11, 5)}–${(r.scheduled_to || "").substr(11, 5)}`;
	};
	const card = (r, action) => `
		<a href="/desk/meeting-schedule/${encodeURIComponent(r.name)}" class="mm-row"
			style="display:flex;align-items:center;gap:12px;padding:12px 14px;border:1px solid var(--border-color);border-radius:10px;margin-bottom:8px;color:inherit;text-decoration:none;">
			<span style="width:36px;height:36px;border-radius:9px;background:${r.meeting_type === "Internal" ? "#26255E" : "#e5306b"};color:#fff;display:flex;align-items:center;justify-content:center;flex:none;">
				${frappe.utils.icon(r.meeting_type === "Internal" ? "users" : "calendar", "sm")}
			</span>
			<span style="flex:1;min-width:0;">
				<b style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${frappe.utils.escape_html(r.organisation || r.name)}</b>
				<span class="text-muted small">${when(r)}${r.is_online ? " · " + __("Online") : ""} · ${__(r.meeting_type || "External")}</span>
			</span>
			${action || ""}
		</a>`;
	const section = (heading, rows, empty, action) => `
		<h5 style="margin:22px 0 10px;">${heading} <span class="text-muted">(${rows.length})</span></h5>
		${rows.length ? rows.map((r) => card(r, action && action(r))).join("") : `<p class="text-muted">${empty}</p>`}`;

	function load() {
		frappe.call("meeting_management.elevatemeet.my_meetings").then((r) => {
			const d = r.message || { today: [], week: [], notes_due: [] };
			body.html(
				section(__("Today"), d.today, __("No meetings today.")) +
				section(__("Next 7 days"), d.week, __("Nothing booked for the coming week.")) +
				section(__("Notes to write"), d.notes_due, __("All your past meetings have notes."),
					() => `<span class="btn btn-xs btn-default">${__("Write notes")}</span>`)
			);
			body.find(".mm-row .btn").closest(".mm-row").on("click", function (e) {
				e.preventDefault();
				const name = decodeURIComponent($(this).attr("href").split("/").pop());
				frappe.model.open_mapped_doc({
					method: "meeting_management.meeting_management.doctype.meeting_schedule.meeting_schedule.make_meeting",
					source_name: name,
				});
			});
		});
	}
	load();
};

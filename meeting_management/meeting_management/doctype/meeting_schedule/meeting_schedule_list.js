// ElevateMeet (3 Nov 2026, user): the list is ID | Meeting Type | Status | Scheduled From | Scheduled To, status in colour
const ELEVATEMEET_STATUS_COLOURS = { Scheduled: "blue", Held: "green", Cancelled: "red" };

frappe.listview_settings["Meeting Schedule"] = {
	add_fields: ["status"],
	hide_name_column: true,
	formatters: {
		status(value) {
			if (!value) return "";
			const colour = ELEVATEMEET_STATUS_COLOURS[value] || "gray";
			return `<span class="indicator-pill ${colour} filterable" data-filter="status,=,${value}"><span>${__(value)}</span></span>`;
		},
	},
};

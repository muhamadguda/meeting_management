// Copyright (c) 2023, FinByz Tech Pvt. Ltd. and contributors
// For license information, please see license.txt
// ElevateMeet (3 Nov 2026): the old top-level `this.frm.add_fetch` / `cur_frm.set_query` broke the whole form on
// Frappe v16 (this.frm is undefined there), so the contact fetch and filter now run in setup.
// ElevateMeet (3 Nov 2026, option A): an internal meeting's people come from the same department or chosen departments
var elevatemeet_departments = (frm) =>
	frm.doc.meeting_type !== "Internal" ? [] :
	frm.doc.department_scope === "Same Department" ? (frm.doc.department ? [frm.doc.department] : []) :
	(frm.doc.departments || []).map((r) => r.department).filter(Boolean);

var elevatemeet_department_events = {
	department_scope(frm) {
		if (frm.doc.department_scope === "Same Department" && !frm.doc.department) {
			frappe.xcall("meeting_management.elevatemeet.organiser_department", { user: frm.doc.meeting_arranged_by })
				.then((d) => d && frm.set_value("department", d));
		}
	},
	meeting_type(frm) {
		frm.trigger("refresh");
	},
};

function elevatemeet_add_members(frm, table, to_row) {
	frm.remove_custom_button(__("Add Department Members"));
	if (frm.doc.meeting_type !== "Internal" || frm.doc.docstatus > 0) return;
	frm.add_custom_button(__("Add Department Members"), () => {
		const depts = elevatemeet_departments(frm);
		if (!depts.length) {
			frappe.msgprint(__("Pick the department first."));
			return;
		}
		frappe.xcall("meeting_management.elevatemeet.department_people", { departments: depts }).then((people) => {
			const have = new Set((frm.doc[table] || []).map((r) => r.user || r.employee));
			let added = 0;
			(people || []).forEach((p) => {
				if (p.user === frm.doc.meeting_arranged_by || have.has(p.user) || have.has(p.employee)) return;
				Object.assign(frm.add_child(table), to_row(p));
				added++;
			});
			frm.refresh_field(table);
			frappe.show_alert({ message: __("{0} people added", [added]), indicator: "green" });
		});
	});
}

frappe.ui.form.on('Meeting Schedule', Object.assign({}, elevatemeet_department_events, {
	setup: function(frm) {
		frm.add_fetch('contact', 'email_id', 'email_id');
		frm.set_query("contact", function() {
			return {
				query: "frappe.contacts.doctype.contact.contact.contact_query",
				filters: { link_doctype: frm.doc.party_type, link_name: frm.doc.party }
			};
		});
	},

	refresh: function(frm) {
		frm.set_query("user", "attendees", () => ({ query: "meeting_management.elevatemeet.staff_query", filters: { departments: elevatemeet_departments(frm) } }));
		elevatemeet_add_members(frm, "attendees", (p) => ({ user: p.user, full_name: p.employee_name }));
		frm.set_query('party', () => ({ query: 'meeting_management.elevatemeet.party_query' }));
		if (frm.doc.status && !frm.is_new()) {
			frm.set_intro(frm.doc.status === "Cancelled" ? __("This meeting was cancelled.") :
				frm.doc.status === "Held" ? __("This meeting was held.") :
				__("Elevate attendees get a calendar invite when you save, and a Raven reminder an hour before."),
				frm.doc.status === "Cancelled" ? "red" : frm.doc.status === "Held" ? "green" : "blue");
		}
		if (!frm.is_new() && frm.doc.status === "Scheduled") {
			frm.add_custom_button(__("Cancel Meeting"), () => {
				frappe.confirm(__("Cancel this meeting? Elevate attendees get a cancellation."), () => {
					frm.set_value("status", "Cancelled");
					frm.save();
				});
			});
		}
		frm.add_custom_button(__("Create Meeting"), function() {
			frappe.model.open_mapped_doc({
				method : "meeting_management.meeting_management.doctype.meeting_schedule.meeting_schedule.make_meeting",
				frm : frm
			})
		})
	
	},

	email_template:function(frm){
		frm.call({
			method:"frappe.email.doctype.email_template.email_template.get_email_template",
			args:{
				template_name:frm.doc.email_template,
				doc:frm.doc
			},
			callback: function(r){
				
				frm.set_value("invitation_message", r.message.message)

			}
		})
	},

	
	scheduled_from(frm) {
		if (frm.doc.scheduled_from && !frm.doc.scheduled_to){
		    // frm.set_value('scheduled_to',frm.doc.scheduled_from)
			// ElevateMeet: the default length comes from ElevateMeet Settings
			frappe.xcall('meeting_management.elevatemeet.default_duration').then((mins) => {
				frm.set_value('scheduled_to', moment(frappe.datetime.str_to_obj(frm.doc.scheduled_from)).add(mins || 60, 'minutes').format('YYYY-MM-DD HH:mm:ss'));
			});
		}
	},
	validate: function(frm){
		frm.trigger("set_link_documents");
		// frm.trigger("contact_person_name")
	},

	contact_person_name: function(frm) {
		if(frm.doc.contact || frm.doc.contact_person_name){
			let datetime = frappe.datetime.global_date_format(frm.doc.scheduled_from).split(", ");
			let date = datetime[0] || '';
			let time = datetime[1]+"." || '';
			let sign = frappe.boot.user.email_signature || '';
			let is_online = "";
			if(frm.doc.is_online){
				is_online = " Online ";
			}
			let message = "<p>Dear " + frm.doc.contact_person_name + ",</p><p>Greeting from "+ frm.doc.company + "</p><p> This is regarding the telephonic conversation that we had" + ". The" + is_online + " Meeting has been scheduled on " + date + " at " + time + "</p><p>In case of Any Changes in your Schedule request you to inform us timely.</p><p></p>Thanks & Regards,<p>" + sign + "</p>";
			frm.set_value("invitation_message", message);
		}
	},

	party_type: function(frm){
		frm.set_value('party','');
		frm.set_value('contact', '');
		frm.set_value('organisation', '');
		frm.set_value('contact_person_name', '');
		frm.set_value('mobile_no', '');
		frm.set_value('email_id', '');
		frm.set_value('invitation_message', '');
	},
	party: function(frm) {
		if(frm.doc.party){
			frm.trigger("get_party_details");
			frm.trigger("set_link_documents");
		}
	},
	set_link_documents: function(frm){
		if(frm.doc.party){
			if(frm.doc.party_type=="Lead"){
				frm.set_value("lead",frm.doc.party)
			}
			else if(frm.doc.party_type=="Customer"){
				frm.set_value("customer",frm.doc.party)
			}
			else if(frm.doc.party_type=="Opportunity"){
				frm.set_value("opportunity",frm.doc.party)
			}
		}		
	},
	get_party_details: function(frm){
		frappe.call({
			method:"meeting_management.meeting_management.doctype.meeting_schedule.meeting_schedule.get_party_details",
			args:{
				party: frm.doc.party,
				party_type: frm.doc.party_type
			},
			callback: function(r){
				if(r.message){
					frm.set_value('contact', r.message.contact_person);
					frm.set_value('contact_person_name', r.message.contact_display);
					frm.set_value('mobile_no', r.message.contact_mobile);
					frm.set_value('email_id', r.message.contact_email);
					frm.set_value('organisation', r.message.organisation);
				}
			}
		})
	},
	send_invitation: function(frm){
		if (frm.is_dirty()){
			frappe.throw("Please Save the Current Document and Then Proceed again")
		}
		else{
			// ElevateMeet: the invitation and calendar invite go to the client only from this button
			frappe.confirm(__('Email the invitation and calendar invite to {0}?', [frm.doc.email_id || __('the client')]), () => {
				frappe.call({ method: 'meeting_management.elevatemeet.send_to_client', args: { name: frm.doc.name } })
					.then((r) => { frappe.show_alert({ message: __('Sent to {0}', [(r.message || []).join(', ')]), indicator: 'green' }); frm.reload_doc(); });
			});
		}
	}
}));
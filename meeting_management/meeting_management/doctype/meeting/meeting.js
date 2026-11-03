// Copyright (c) 2023, FinByz Tech Pvt. Ltd. and contributors
// For license information, please see license.txt


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

frappe.ui.form.on('Meeting', Object.assign({}, elevatemeet_department_events, {
	send_mail: function(frm){
		if (frm.is_dirty()){
			frappe.throw("Please Save the Current Document and Then Proceed again")
		}
		else{
			frappe.call({
				method:"meeting_management.meeting_management.doctype.meeting.meeting.send_mail",
				args:{
					self : frm.doc
				},
			})
		}
	},
	refresh: function(frm) {
		// ElevateMeet (3 Nov 2026, user): a new meeting asks only for the type, organiser and time; the rest after saving
		if (frm.is_new()) {
			const keep = ['meeting_type', 'meeting_arranged_by', 'meeting_from', 'meeting_to'];
			const breaks = ['Section Break', 'Column Break', 'Tab Break'];
			frm.meta.fields.forEach((df) => {
				if (!breaks.includes(df.fieldtype) && !keep.includes(df.fieldname)) frm.toggle_display(df.fieldname, false);
			});
			frm.set_intro(__('Pick the meeting type, organiser and time, then save to fill in the rest.'), 'blue');
		}
		// ElevateMeet: an external meeting is with a lead (company) or a job applicant, shown with ID and name
		frm.set_query('party', () => ({ query: 'meeting_management.elevatemeet.party_query' }));
		frm.set_query('employee', 'meeting_company_representative', () => {
			const depts = elevatemeet_departments(frm);
			return { filters: depts.length ? { status: 'Active', department: ['in', depts] } : { status: 'Active' } };
		});
		elevatemeet_add_members(frm, 'meeting_company_representative', (p) => ({ employee: p.employee, employee_name: p.employee_name, emp_user_id: p.user }));
		frm.fields_dict.contact_person.get_query = function(doc) {
			return {
				query: 'frappe.contacts.doctype.contact.contact.contact_query',
				filters: {
					link_doctype: frm.doc.party_type,
					link_name: frm.doc.party
				}
			}
		};
		frm.fields_dict.meeting_party_representative.grid.get_field("contact").get_query = function(doc,cdt,cdn) {
			return {
				query: 'frappe.contacts.doctype.contact.contact.contact_query',
				filters: {
					link_doctype: frm.doc.party_type,
					link_name: frm.doc.party
				}
			}
		}
		frm.fields_dict.address.get_query = function(doc) {
			return {
				query: 'frappe.contacts.doctype.address.address.address_query',
				filters: {
					link_doctype: frm.doc.party_type,
					link_name: frm.doc.party
				}
			}
		};
	},
	meeting_from(frm) {
		if (frm.doc.meeting_from && !frm.doc.meeting_to){
		    // frm.set_value('scheduled_to',frm.doc.scheduled_from)
			frm.set_value('meeting_to',frappe.datetime.get_datetime_as_string(frappe.datetime.str_to_obj(frm.doc.meeting_from).setHours(frappe.datetime.str_to_obj(frm.doc.meeting_from).getHours() + 1)))
		}
	},
	validate: function(frm){
		if (frm.doc.party && frm.doc.party_type){
			frm.trigger('party')
		}
		frm.trigger('set_link_documents');
		// frm.trigger('calculate_km_wise_expense');
	},

	party: function(frm){
		// if(frm.doc.party_type == "Customer" && frm.doc.party)
		frappe.call({
			method:"meeting_management.meeting_management.doctype.meeting_schedule.meeting_schedule.get_party_details",
			args:{
				party: frm.doc.party,
				party_type: frm.doc.party_type
			},
			callback: function(r){
				if(r.message){
					frm.set_value('contact_person', r.message.contact_person)
					frm.set_value('email_id', r.message.contact_email)
					frm.set_value('mobile_no', r.message.contact_mobile)
					frm.set_value('contact', r.message.contact_dispaly)
					frm.set_value('address', r.message.customer_address)
					frm.set_value('address_display', r.message.address_display)
					frm.set_value('organization', r.message.organisation);
				}
			}
		})
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
    meeting_arranged_by: function(frm) {
        if (frm.doc.__islocal) {
            frappe.db.get_value("Employee", {"user_id": frm.doc.meeting_arranged_by}, ["name"], function(value) {
                if (value && value.name) {
                    // Check if a row with the specified condition already exists
                    let row_exists = false;
                    $.each(frm.doc.meeting_company_representative || [], function(i, row) {
                        if (row.employee === value.name) {
                            row_exists = true;
                            return false; // Exit the loop
                        }
                    });

                    // If the row doesn't exist, add a new row
                    if (!row_exists) {
                        let row = frm.add_child("meeting_company_representative");
                        frappe.model.set_value(row.doctype, row.name, 'employee', value.name);
                        frm.refresh_field('meeting_company_representative');
                    }
                }
            });
        }
    },
    contact_person: function(frm) {
        if (frm.doc.__islocal) {
            let row_exists = false;
            $.each(frm.doc.meeting_party_representative || [], function(i, row) {
                if (row.contact === frm.doc.contact_person) {
                    row_exists = true;
                    return false; // Exit the loop
                }
            });
            if (!row_exists) {
                let row = frm.add_child("meeting_party_representative");
                frappe.model.set_value(row.doctype, row.name, 'contact', frm.doc.contact_person);
                frm.refresh_field('meeting_party_representative');
            }
        }
    }
}));

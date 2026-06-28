// Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Negative Stock Batch Exception", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Preview Fix"), () => preview_fix(frm));
		frm.add_custom_button(__("Fix Negative Batch"), () => fix_negative_batch(frm)).addClass(
			"btn-primary"
		);
	},
});

function get_fix_plan(frm) {
	return frappe
		.call({
			method: "batch_correction.batch_correction.doctype.negative_stock_batch_exception.negative_stock_batch_exception.get_fix_plan",
			args: { name: frm.doc.name },
			freeze: true,
			freeze_message: __("Analyzing stock ledger..."),
		})
		.then((r) => r.message);
}

function preview_fix(frm) {
	get_fix_plan(frm).then((plan) => {
		frappe.msgprint({
			title: __("Fix Plan"),
			indicator: plan_indicator(plan.status),
			message: plan_summary_html(plan),
		});
	});
}

function fix_negative_batch(frm) {
	get_fix_plan(frm).then((plan) => {
		if (plan.status !== "fixable") {
			frappe.msgprint({
				title: __("Cannot Fix Automatically"),
				indicator: plan_indicator(plan.status),
				message: plan_summary_html(plan),
			});
			return;
		}

		frappe.confirm(plan_summary_html(plan) + `<p>${__("Continue?")}</p>`, () => {
			frappe
				.call({
					method: "batch_correction.batch_correction.doctype.negative_stock_batch_exception.negative_stock_batch_exception.fix_negative_batch",
					args: { name: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating Repack Stock Entry..."),
				})
				.then((r) => {
					const result = r.message;
					if (result.status === "fixed") {
						frappe.show_alert({
							message: __("Fixed via Stock Entry {0}", [
								frappe.utils.escape_html(result.stock_entry),
							]),
							indicator: "green",
						});
						frappe.set_route("List", "Negative Stock Batch Exception");
					} else {
						frappe.msgprint({
							title: __("Could Not Fix"),
							indicator: plan_indicator(result.status),
							message: plan_summary_html(result),
						});
						frm.reload_doc();
					}
				});
		});
	});
}

function plan_indicator(status) {
	return { fixable: "blue", fixed: "green", healthy: "green" }[status] || "red";
}

function plan_summary_html(plan) {
	if (plan.status === "healthy") {
		return __("No negative balance found in the ledger; this exception may be stale.");
	}
	if (
		plan.status === "insufficient_donors" ||
		plan.status === "error" ||
		plan.status === "fixed_but_unverified"
	) {
		return frappe.utils.escape_html(plan.message || plan.status);
	}

	const donor_rows = (plan.allocations || [])
		.map(
			([batch_no, qty]) =>
				`<li>${frappe.format(qty, { fieldtype: "Float" })} ${__(
					"from"
				)} <a href="/app/batch/${encodeURIComponent(batch_no)}">${frappe.utils.escape_html(
					batch_no
				)}</a></li>`
		)
		.join("");

	return `
		<p>${__("Batch {0} first went negative on {1}.", [
			`<b>${frappe.utils.escape_html(plan.batch_no)}</b>`,
			frappe.datetime.str_to_user(plan.t0),
		])}</p>
		<p>${__("A Repack Stock Entry dated {0} will add {1} units to this batch, drawn from:", [
			frappe.datetime.str_to_user(plan.posting_datetime),
			`<b>${plan.deficit}</b>`,
		])}</p>
		<ul>${donor_rows}</ul>
		<p class="text-muted">${__(
			"This is a backdated entry: ERPNext will repost every later Stock Ledger Entry for this item/warehouse, which can take a while."
		)}</p>
	`;
}

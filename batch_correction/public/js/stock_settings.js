// Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Stock Settings", {
	refresh(frm) {
		frm.add_custom_button(
			__("Fix All Negative Batches"),
			() => fix_all_negative_batches(),
			__("Batch Correction")
		);
	},
});

function fix_all_negative_batches() {
	frappe.confirm(
		__(
			"This will analyze every Negative Stock Batch Exception and submit a Repack Stock Entry for each one that can be fully covered from another batch with spare stock. Repacks are backdated, so ERPNext will repost later stock ledger entries for whatever they touch, which can take a while. Continue?"
		),
		() => {
			frappe
				.call({
					method: "batch_correction.batch_correction.doctype.negative_stock_batch_exception.negative_stock_batch_exception.fix_all_negative_batches",
					freeze: true,
					freeze_message: __("Fixing negative batches..."),
				})
				.then((r) => show_fix_all_results(r.message || []));
		}
	);
}

function show_fix_all_results(results) {
	if (!results.length) {
		frappe.msgprint(__("No Negative Stock Batch Exception records found."));
		return;
	}

	const needs_attention = results.some((r) =>
		["error", "insufficient_donors", "fixed_but_unverified"].includes(r.status)
	);

	frappe.msgprint({
		title: __("Fix All Results"),
		indicator: needs_attention ? "orange" : "green",
		wide: true,
		message: `<p>${summarize(results)}</p>${render_results_table(results)}`,
	});
}

function summarize(results) {
	const counts = {};
	results.forEach((r) => {
		counts[r.status] = (counts[r.status] || 0) + 1;
	});
	return Object.entries(counts)
		.map(([status, count]) => `${count} ${frappe.utils.escape_html(status)}`)
		.join(", ");
}

function status_indicator(status) {
	return { fixed: "green", healthy: "green", fixable: "blue" }[status] || "red";
}

function result_detail(result) {
	if (result.status === "fixed") {
		return `${__("via")} <a href="/app/stock-entry/${encodeURIComponent(
			result.stock_entry
		)}">${frappe.utils.escape_html(result.stock_entry)}</a>`;
	}
	return frappe.utils.escape_html(result.message || "");
}

function render_results_table(results) {
	const rows = results
		.map(
			(r) => `
			<tr>
				<td><a href="/app/batch/${encodeURIComponent(r.batch_no)}">${frappe.utils.escape_html(
				r.batch_no
			)}</a></td>
				<td><a href="/app/warehouse/${encodeURIComponent(r.warehouse)}">${frappe.utils.escape_html(
				r.warehouse
			)}</a></td>
				<td><span class="indicator-pill ${status_indicator(r.status)}">${frappe.utils.escape_html(
				r.status
			)}</span></td>
				<td>${result_detail(r)}</td>
			</tr>`
		)
		.join("");

	return `
		<table class="table table-bordered">
			<thead>
				<tr>
					<th>${__("Batch")}</th>
					<th>${__("Warehouse")}</th>
					<th>${__("Status")}</th>
					<th>${__("Detail")}</th>
				</tr>
			</thead>
			<tbody>${rows}</tbody>
		</table>
	`;
}

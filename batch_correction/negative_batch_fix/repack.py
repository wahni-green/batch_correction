# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import get_datetime


def create_repack_entry(
	company: str,
	warehouse: str,
	item_code: str,
	target_batch: str,
	target_qty: float,
	donor_allocations: list[tuple[str, float]],
	posting_datetime,
) -> "frappe.model.document.Document":
	"""Build (but do not insert/submit) a Repack Stock Entry that moves stock
	from the donor batches into the target batch, within the same warehouse.

	No rates are set manually: ERPNext's own Repack costing fetches each
	source row's incoming rate for its batch, and prices the single finished
	(target) row as the weighted average of consumed value -- so total
	inventory value is preserved, not created or destroyed.
	"""
	posting_datetime = get_datetime(posting_datetime)
	stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")

	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.stock_entry_type = "Repack"
	stock_entry.purpose = "Repack"
	stock_entry.company = company
	stock_entry.set_posting_time = 1
	stock_entry.posting_date = posting_datetime.date()
	stock_entry.posting_time = posting_datetime.time()

	for donor_batch, qty in donor_allocations:
		stock_entry.append(
			"items",
			{
				"item_code": item_code,
				"s_warehouse": warehouse,
				"qty": qty,
				"uom": stock_uom,
				"stock_uom": stock_uom,
				"conversion_factor": 1,
				"batch_no": donor_batch,
				"use_serial_batch_fields": 1,
			},
		)

	stock_entry.append(
		"items",
		{
			"item_code": item_code,
			"t_warehouse": warehouse,
			"qty": target_qty,
			"uom": stock_uom,
			"stock_uom": stock_uom,
			"conversion_factor": 1,
			"batch_no": target_batch,
			"use_serial_batch_fields": 1,
		},
	)

	return stock_entry

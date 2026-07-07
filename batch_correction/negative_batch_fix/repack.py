# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import get_datetime


def _make_repack_header(company: str, posting_datetime) -> "frappe.model.document.Document":
	"""Return a bare Repack Stock Entry with header fields set and no item rows."""
	posting_datetime = get_datetime(posting_datetime)
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Repack"
	se.purpose = "Repack"
	se.company = company
	se.set_posting_time = 1
	se.posting_date = posting_datetime.date()
	se.posting_time = posting_datetime.time()
	return se


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
	stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
	se = _make_repack_header(company, posting_datetime)

	for donor_batch, qty in donor_allocations:
		se.append(
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

	se.append(
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

	return se


def create_reversal_entries(
	company: str,
	warehouse: str,
	item_code: str,
	source_batch: str,
	donor_allocations: list[tuple[str, float]],
	posting_datetime,
) -> list["frappe.model.document.Document"]:
	"""Build (but do not insert/submit) the reversal half of a bridge fix as
	one Repack per donor allocation.

	The deficit batch (source_batch) gives back the borrowed stock to each
	donor batch at posting_datetime (recovery_time + 1s).  One separate Repack
	is created per donor so every entry has a single source row and a single
	target row -- matching ERPNext's expected Repack costing model (multiple
	raw-material sources feeding one finished-goods target).  Merging donors
	into one entry with multiple targets would leave ERPNext unable to
	distribute the source valuation correctly across the targets.

	This leaves every donor's long-term balance unchanged and keeps total item
	inventory at zero net impact.
	"""
	stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
	entries = []

	for donor_batch, qty in donor_allocations:
		se = _make_repack_header(company, posting_datetime)
		se.append(
			"items",
			{
				"item_code": item_code,
				"s_warehouse": warehouse,
				"qty": qty,
				"uom": stock_uom,
				"stock_uom": stock_uom,
				"conversion_factor": 1,
				"batch_no": source_batch,
				"use_serial_batch_fields": 1,
			},
		)
		se.append(
			"items",
			{
				"item_code": item_code,
				"t_warehouse": warehouse,
				"qty": qty,
				"uom": stock_uom,
				"stock_uom": stock_uom,
				"conversion_factor": 1,
				"batch_no": donor_batch,
				"use_serial_batch_fields": 1,
			},
		)
		entries.append(se)

	return entries

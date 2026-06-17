# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint


def execute(filters: dict | None = None):
	"""Return columns and data for the report.

	This is the main entry point for the report. It accepts the filters as a
	dictionary and should return columns and data. It is called by the framework
	every time the report is refreshed or a filter is updated.
	"""
	columns = get_columns()
	data = get_data(filters)

	return columns, data


def get_columns() -> list[dict]:
	return [
		{
			"label": _("Posting Datetime"),
			"fieldname": "posting_date",
			"fieldtype": "Datetime",
			"width": 160,
		},
		{
			"label": _("Batch No"),
			"fieldname": "batch_no",
			"fieldtype": "Link",
			"options": "Batch",
			"width": 120,
		},
		{
			"label": _("Item Code"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 150,
		},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 160,
		},
		{
			"label": _("Previous Qty"),
			"fieldname": "previous_qty",
			"fieldtype": "Float",
			"width": 130,
		},
		{
			"label": _("Transaction Qty"),
			"fieldname": "actual_qty",
			"fieldtype": "Float",
			"width": 130,
		},
		{
			"label": _("Qty After Transaction"),
			"fieldname": "qty_after_transaction",
			"fieldtype": "Float",
			"width": 180,
		},
		{
			"label": _("Document Type"),
			"fieldname": "voucher_type",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("Document No"),
			"fieldname": "voucher_no",
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 130,
		},
	]


def get_data(filters) -> list[dict]:
	warehouses = get_warehouses(filters)
	if not warehouses:
		return []

	flt_precision = cint(frappe.db.get_default("float_precision")) or 2

	sle_item_condition = ""
	values = {
		"warehouses": tuple(w.name for w in warehouses),
		"precision": flt_precision,
	}
	if filters.get("item_code"):
		sle_item_condition = "AND item_code = %(item_code)s"
		values["item_code"] = filters["item_code"]

	sbe_select = get_sbe_movements_select(filters)

	# Single set-based query computing a running per (warehouse, batch_no) balance
	# via a window function, instead of re-running the full Stock Ledger report
	# once per warehouse x batch combination (which made this report time out).
	# nosemgrep: frappe-semgrep-rules.rules.frappe-using-db-sql
	return frappe.db.sql(
		f"""
		WITH movements AS (
			{sbe_select}

			UNION ALL

			SELECT
				batch_no, warehouse, item_code, actual_qty,
				posting_datetime, voucher_type, voucher_no, creation, 0 AS idx
			FROM `tabStock Ledger Entry`
			WHERE docstatus < 2
				AND is_cancelled = 0
				AND batch_no IS NOT NULL AND batch_no != ''
				AND (serial_and_batch_bundle IS NULL OR serial_and_batch_bundle = '')
				AND warehouse IN %(warehouses)s
				{sle_item_condition}
		),
		running AS (
			SELECT
				batch_no, warehouse, item_code, actual_qty, posting_datetime,
				voucher_type, voucher_no, creation, idx,
				SUM(actual_qty) OVER (
					PARTITION BY warehouse, batch_no
					ORDER BY posting_datetime, creation, idx
				) AS qty_after_transaction
			FROM movements
		),
		negative AS (
			SELECT
				*,
				ROW_NUMBER() OVER (
					PARTITION BY warehouse, batch_no
					ORDER BY posting_datetime, creation, idx
				) AS rn
			FROM running
			WHERE ROUND(qty_after_transaction, %(precision)s) < 0
		)
		SELECT
			posting_datetime AS posting_date,
			batch_no, item_code, warehouse,
			qty_after_transaction - actual_qty AS previous_qty,
			actual_qty,
			qty_after_transaction,
			voucher_type, voucher_no
		FROM negative
		WHERE rn = 1
		ORDER BY warehouse, batch_no
		""",
		values,
		as_dict=True,
	)


def get_sbe_movements_select(filters) -> str:
	"""Build the Serial and Batch Entry arm of the movements CTE.

	Sites on an erpnext version older than commit f2ad27eb06 ("perf: DN
	submission with SABB") don't have posting_datetime/voucher_type/voucher_no
	denormalized onto Serial and Batch Entry, so those fields must instead be
	pulled from the parent Serial and Batch Bundle. Sites older still (before
	8d4a179a, "refactor: single table for better performance") also lack
	item_code/is_cancelled on the entry.
	"""
	sbe_columns = set(frappe.db.get_table_columns("Serial and Batch Entry"))

	if {"posting_datetime", "voucher_type", "voucher_no"}.issubset(sbe_columns):
		cancelled_condition = "AND sbe.is_cancelled = 0" if "is_cancelled" in sbe_columns else ""
		item_condition = "AND b.item = %(item_code)s" if filters.get("item_code") else ""
		return f"""
			SELECT
				sbe.batch_no, sbe.warehouse, b.item AS item_code, sbe.qty AS actual_qty,
				sbe.posting_datetime, sbe.voucher_type, sbe.voucher_no, sbe.creation, sbe.idx
			FROM `tabSerial and Batch Entry` sbe
			INNER JOIN `tabBatch` b ON b.name = sbe.batch_no
			WHERE sbe.docstatus = 1
				{cancelled_condition}
				AND sbe.batch_no IS NOT NULL AND sbe.batch_no != ''
				AND sbe.warehouse IN %(warehouses)s
				{item_condition}
		"""

	item_condition = "AND sbb.item_code = %(item_code)s" if filters.get("item_code") else ""
	return f"""
		SELECT
			sbe.batch_no, sbe.warehouse, sbb.item_code, sbe.qty AS actual_qty,
			sbb.posting_datetime, sbb.voucher_type, sbb.voucher_no, sbe.creation, sbe.idx
		FROM `tabSerial and Batch Entry` sbe
		INNER JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
		WHERE sbb.docstatus = 1
			AND sbe.batch_no IS NOT NULL AND sbe.batch_no != ''
			AND sbe.warehouse IN %(warehouses)s
			{item_condition}
	"""


def get_warehouses(filters):
	warehouse_filters = {"disabled": 0, "is_group": 0}
	if filters.get("company"):
		warehouse_filters["company"] = filters["company"]

	if filters.get("warehouse"):
		warehouse_filters["name"] = filters["warehouse"]

	return frappe.get_all("Warehouse", fields=["name", "company"], filters=warehouse_filters)

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

	item_condition = ""
	values = {
		"warehouses": tuple(w.name for w in warehouses),
		"precision": flt_precision,
	}
	if filters.get("item_code"):
		item_condition = "AND item_code = %(item_code)s"
		values["item_code"] = filters["item_code"]

	# Single set-based query computing a running per (warehouse, batch_no) balance
	# via a window function, instead of re-running the full Stock Ledger report
	# once per warehouse x batch combination (which made this report time out).
	# nosemgrep: frappe-semgrep-rules.rules.frappe-using-db-sql
	return frappe.db.sql(
		f"""
		WITH movements AS (
			SELECT
				batch_no, warehouse, item_code, qty AS actual_qty,
				posting_datetime, voucher_type, voucher_no, creation, idx
			FROM `tabSerial and Batch Entry`
			WHERE docstatus = 1
				AND is_cancelled = 0
				AND batch_no IS NOT NULL AND batch_no != ''
				AND warehouse IN %(warehouses)s
				{item_condition}

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
				{item_condition}
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


def get_warehouses(filters):
	warehouse_filters = {"disabled": 0, "is_group": 0}
	if filters.get("company"):
		warehouse_filters["company"] = filters["company"]

	if filters.get("warehouse"):
		warehouse_filters["name"] = filters["warehouse"]

	return frappe.get_all("Warehouse", fields=["name", "company"], filters=warehouse_filters)

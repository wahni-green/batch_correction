# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.report.stock_ledger.stock_ledger import execute as get_stock_ledger_data
from frappe.utils import flt, get_datetime

# Far enough back that it predates any real stock activity, so the report's
# opening-balance row is always zero and can be ignored.
EARLIEST_DATE = "1900-01-01"


def get_qty_precision() -> int:
	return frappe.get_precision("Serial and Batch Entry", "qty")


def get_batch_balance_series(item_code: str, warehouse: str, batch_no: str, company: str) -> list[dict]:
	"""Chronological qty_after_transaction series for a single batch in a warehouse.

	Delegates to the core Stock Ledger report instead of a hand-rolled query: a
	Stock Ledger Entry for a Serial and Batch Bundle can mix several batches'
	quantities into one row, and this report already knows how to segregate
	that back out to the true per-batch qty via the bundle's child entries.
	"""
	filters = frappe._dict(
		company=company,
		item_code=[item_code],
		warehouse=[warehouse],
		batch_no=batch_no,
		segregate_serial_batch_bundle=1,
		valuation_field_type="Currency",
		from_date=EARLIEST_DATE,
		to_date=frappe.utils.today(),
	)
	_columns, data = get_stock_ledger_data(filters)
	# Drop the synthetic opening-balance row: it has no voucher and is zero
	# anyway since from_date predates any real activity.
	return [row for row in data if row.get("voucher_no")]


def find_negative_window(series: list[dict], precision: int) -> tuple[object, float] | None:
	"""Find the point a batch first went negative, and the deficit needed to
	keep it non-negative for the rest of the series.

	Returns (t0, deficit) where `t0` is the posting datetime of the first
	transaction that took the running balance negative, and `deficit` is the
	quantity that must be injected at (or just before) `t0` to keep the running
	balance non-negative from `t0` through the end of the series. Returns None
	if the series never goes negative.

	A single injection at `t0` sized to `deficit` is always enough, even if the
	balance dips deeper later after recovering in between: qty_after_transaction
	is a running sum, so adding a constant at `t0` shifts every later balance by
	that same constant amount, regardless of the path in between.
	"""
	t0_index = None
	for i, row in enumerate(series):
		if flt(row.qty_after_transaction, precision) < 0:
			t0_index = i
			break

	if t0_index is None:
		return None

	trough = min(flt(row.qty_after_transaction, precision) for row in series[t0_index:])
	t0 = get_datetime(series[t0_index].date)
	return t0, abs(trough)

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


def find_negative_window(series: list[dict], precision: int) -> tuple | None:
	"""Return (t0, deficit, recovery_time) for the first negative window, or None.

	t0:            posting datetime of the first transaction that took the balance negative.
	deficit:       minimum quantity that must be injected just before t0 to keep the
	               balance non-negative through the first natural recovery.  Sized to the
	               deepest trough between t0 and recovery_time (the first-window trough),
	               not the global series minimum -- the bridge reversal at recovery_time+1s
	               restores the donor's stock, so subsequent windows are handled separately
	               (the verify pass in fix_one catches any that remain).
	               When recovery_time is None (batch never recovers) the global trough is
	               used instead, since no reversal is possible and the full deficit applies.
	recovery_time: datetime of the first entry after t0 where the running balance returns
	               to >= 0, or None if the batch never recovers within the series.
	"""
	t0_index = None
	for i, row in enumerate(series):
		if flt(row.qty_after_transaction, precision) < 0:
			t0_index = i
			break

	if t0_index is None:
		return None

	t0 = get_datetime(series[t0_index].date)

	recovery_index = None
	for i, row in enumerate(series[t0_index + 1:], start=t0_index + 1):
		if flt(row.qty_after_transaction, precision) >= 0:
			recovery_index = i
			break

	if recovery_index is not None:
		window_rows = series[t0_index:recovery_index]
		recovery_time = get_datetime(series[recovery_index].date)
	else:
		window_rows = series[t0_index:]
		recovery_time = None

	trough = min(flt(row.qty_after_transaction, precision) for row in window_rows)
	return t0, abs(trough), recovery_time

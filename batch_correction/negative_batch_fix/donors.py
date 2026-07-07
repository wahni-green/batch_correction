# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt, get_datetime

from batch_correction.negative_batch_fix.analyzer import get_batch_balance_series


def get_candidate_donor_batches(item_code: str, warehouse: str, exclude_batch: str) -> list[str]:
	"""Other batches of the same item that have ever moved through this warehouse."""
	batches = frappe.get_all(
		"Batch",
		filters={"item": item_code, "disabled": 0, "name": ("!=", exclude_batch)},
		pluck="name",
	)
	return [
		batch_no
		for batch_no in batches
		if frappe.db.exists(
			"Stock Ledger Entry", {"item_code": item_code, "warehouse": warehouse, "batch_no": batch_no}
		)
		or frappe.db.exists("Serial and Batch Entry", {"batch_no": batch_no, "warehouse": warehouse})
	]


def compute_headroom(series: list[dict], since, precision: int, until=None) -> float:
	"""The most that can be withdrawn from this batch at `since` without ever
	sending it negative during the window [since, until] (or through the end of
	the series when until is None).

	`balance_before` (the last recorded balance before `since`) is included in
	the minimum because inserting a new row at `since` shifts that carried-forward
	balance too.  Floored at 0: a batch that is already negative at `since` has
	nothing to spare.
	"""
	since = get_datetime(since)
	until = get_datetime(until) if until is not None else None
	balance_before = 0.0
	min_in_window = None

	for row in series:
		qty = flt(row.qty_after_transaction, precision)
		row_dt = get_datetime(row.date)
		if row_dt < since:
			balance_before = qty
		elif until is None or row_dt <= until:
			min_in_window = qty if min_in_window is None else min(min_in_window, qty)

	lowest_balance = min(balance_before, min_in_window) if min_in_window is not None else balance_before
	return max(lowest_balance, 0.0)


def find_donor_allocations(
	item_code: str,
	warehouse: str,
	company: str,
	exclude_batch: str,
	since,
	deficit: float,
	precision: int,
	until=None,
) -> tuple[list[tuple[str, float]], float]:
	"""Greedily allocate `deficit` across donor batches, largest headroom first.

	Returns (allocations, shortfall) where `allocations` is a list of
	(batch_no, qty) tuples summing to `deficit - shortfall`.
	"""
	candidates = []
	for batch_no in get_candidate_donor_batches(item_code, warehouse, exclude_batch):
		series = get_batch_balance_series(item_code, warehouse, batch_no, company)
		headroom = compute_headroom(series, since, precision, until=until)
		if flt(headroom, precision) > 0:
			candidates.append((batch_no, headroom))

	candidates.sort(key=lambda candidate: candidate[1], reverse=True)

	remaining = deficit
	allocations = []
	for batch_no, headroom in candidates:
		if remaining <= 0:
			break
		take = flt(min(headroom, remaining), precision)
		if take <= 0:
			continue
		allocations.append((batch_no, take))
		remaining -= take

	return allocations, max(flt(remaining, precision), 0.0)

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


def compute_headroom(series: list[dict], since, precision: int) -> float:
	"""Lowest balance the batch holds from `since` (inclusive) through to the
	last entry in `series` -- the most that can be withdrawn at `since` without
	ever sending this batch negative itself as a side effect of the withdrawal.
	"""
	since = get_datetime(since)
	balance_before = 0.0
	min_after = None

	for row in series:
		qty = flt(row.qty_after_transaction, precision)
		if get_datetime(row.date) < since:
			balance_before = qty
		else:
			min_after = qty if min_after is None else min(min_after, qty)

	return min(balance_before, min_after) if min_after is not None else balance_before


def find_donor_allocations(
	item_code: str,
	warehouse: str,
	company: str,
	exclude_batch: str,
	since,
	deficit: float,
	precision: int,
) -> tuple[list[tuple[str, float]], float]:
	"""Greedily allocate `deficit` across donor batches, largest headroom first.

	Returns (allocations, shortfall) where `allocations` is a list of
	(batch_no, qty) tuples summing to `deficit - shortfall`.
	"""
	candidates = []
	for batch_no in get_candidate_donor_batches(item_code, warehouse, exclude_batch):
		series = get_batch_balance_series(item_code, warehouse, batch_no, company)
		headroom = compute_headroom(series, since, precision)
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

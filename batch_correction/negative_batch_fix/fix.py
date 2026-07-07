# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and contributors
# For license information, please see license.txt

"""Fixes batches listed in Negative Stock Batch Exception by repacking stock
into them from other batches of the same item/warehouse that can spare it.

Each Negative Stock Batch Exception's form has "Preview Fix" and "Fix
Negative Batch" buttons (see negative_stock_batch_exception.py) that call
analyze_exception()/fix_one() below for that one record. For fixing many at
once, this is also intended to be run from `bench console` (or `bench
execute`), one site at a time, with `dry_run()` reviewed before `fix_all()` is
ever called:

	bench --site SITE console
	>>> from batch_correction.negative_batch_fix.fix import dry_run, fix_all
	>>> import json; print(json.dumps(dry_run(), indent=2, default=str))
	... review every plan below before proceeding ...
	>>> results = fix_all()
	>>> print(json.dumps(results, indent=2, default=str))

Each plan/result has a `status`:
  - "healthy": the exception looks stale, the ledger isn't negative anymore.
  - "fixable": a Repack can fully cover the deficit; this is what fix_all() acts on.
  - "insufficient_donors": no combination of donor batches covers the deficit;
    nothing is written, the exception is left in place for manual review.
  - "fixed": fix_all() submitted a Repack Stock Entry and verified the batch is
    no longer negative; the Negative Stock Batch Exception was deleted.
  - "fixed_but_unverified": a Repack was submitted but the batch still shows
    negative afterwards -- stop and investigate before re-running.
  - "error": an exception was raised while analyzing/fixing this batch (e.g. a
    frozen stock period) -- the message has a short summary, and the full
    traceback is in the Error Log (dry_run()/fix_all() catch these themselves,
    so frappe never gets the chance to log it on its own).

Each Repack is backdated to just before the batch's first negative
transaction, so submitting it triggers ERPNext's normal reposting of every
later Stock Ledger Entry for that item/warehouse. Run this during low-traffic
hours, and expect it to take a while on items/warehouses with a long history
since that point.

fix_all() commits each successful fix immediately and rolls back whatever a
failed one wrote before moving on, so nothing needs to be committed manually
afterwards and one batch's failure can't taint another's success or leave
partial writes behind for some later, unrelated commit to pick up.
"""

import frappe
from frappe.utils import add_to_date, get_datetime

from batch_correction.negative_batch_fix.analyzer import (
	find_negative_window,
	get_batch_balance_series,
	get_qty_precision,
)
from batch_correction.negative_batch_fix.donors import find_donor_allocations
from batch_correction.negative_batch_fix.repack import create_repack_entry, create_reversal_entry


def analyze_exception(batch_no: str, warehouse: str) -> dict:
	"""Build the fix plan for a single batch/warehouse, without writing anything."""
	item_code = frappe.db.get_value("Batch", batch_no, "item")
	company = frappe.db.get_value("Warehouse", warehouse, "company")
	precision = get_qty_precision()

	plan = {"batch_no": batch_no, "warehouse": warehouse, "item_code": item_code, "company": company}

	series = get_batch_balance_series(item_code, warehouse, batch_no, company)
	window = find_negative_window(series, precision)
	if not window:
		return plan | {
			"status": "healthy",
			"message": "No negative balance found in the ledger; exception may be stale.",
		}

	t0, deficit, recovery_time = window
	posting_datetime = add_to_date(t0, seconds=-1)

	# Limit the headroom check to the bridge window (T0 → recovery_time) when
	# the batch recovers naturally: the reversal repack at recovery_time+1s
	# returns the borrowed stock to each donor, so donors only need to hold the
	# stock for that short window -- not from T0 through today.
	allocations, shortfall = find_donor_allocations(
		item_code, warehouse, company, batch_no, posting_datetime, deficit, precision,
		until=recovery_time,
	)

	if shortfall > 0:
		return plan | {
			"status": "insufficient_donors",
			"t0": t0,
			"deficit": deficit,
			"allocations": allocations,
			"shortfall": shortfall,
			"message": (
				f"Only {deficit - shortfall} of {deficit} could be sourced from eligible donor "
				f"batches in {warehouse}; short by {shortfall}. Needs manual review."
			),
		}

	result = plan | {
		"status": "fixable",
		"t0": t0,
		"deficit": deficit,
		"posting_datetime": posting_datetime,
		"allocations": allocations,
	}
	if recovery_time is not None:
		result["recovery_time"] = recovery_time
		result["reversal_datetime"] = add_to_date(recovery_time, seconds=1)
	return result


def _safe_analyze(batch_no: str, warehouse: str) -> dict:
	"""analyze_exception(), but logging+reporting a failure as an "error" plan
	instead of raising, so one bad batch can never abort a dry_run()/fix_all()
	pass over many batches.
	"""
	try:
		return analyze_exception(batch_no, warehouse)
	except Exception as e:
		frappe.log_error(title=f"Failed to analyze {batch_no} in {warehouse}")
		frappe.db.commit()
		return {"batch_no": batch_no, "warehouse": warehouse, "status": "error", "message": str(e)}


def dry_run() -> list[dict]:
	"""Analyze every Negative Stock Batch Exception and report the fix plan for
	each, without creating anything.
	"""
	return [
		_safe_analyze(exception.batch_no, exception.warehouse)
		for exception in frappe.get_all("Negative Stock Batch Exception", fields=["batch_no", "warehouse"])
	]


def fix_all(delete_resolved_exceptions: bool = True) -> list[dict]:
	"""Process every fixable Negative Stock Batch Exception, oldest negative
	event first, creating + submitting one Repack Stock Entry per batch.

	Re-analyzes fresh from the live ledger immediately before each submission,
	since every earlier fix in this run can change the ledger for whatever it
	touched. Failures on one batch (insufficient donors, a frozen stock period,
	a bad ledger read, ...) are recorded and do not stop the rest of the run --
	including failures in the analysis pass below, not just during fixing.

	Each successful fix is committed immediately so it survives regardless of
	what happens to later batches in this run, and any batch that raises
	partway through (e.g. the Repack inserted but failing to submit) is rolled
	back before moving on, so a caught exception here can never leave partial
	writes to be committed by whatever calls fix_all().
	"""
	exceptions = frappe.get_all("Negative Stock Batch Exception", fields=["batch_no", "warehouse"])

	plans = [_safe_analyze(exception.batch_no, exception.warehouse) for exception in exceptions]
	plans.sort(key=lambda plan: plan.get("t0") or get_datetime("9999-12-31"))

	results = []
	for plan in plans:
		if plan["status"] != "fixable":
			results.append(plan)
			continue

		try:
			result = fix_one(
				plan["batch_no"], plan["warehouse"], delete_resolved_exceptions=delete_resolved_exceptions
			)
			frappe.db.commit()
		except Exception as e:
			# discard this batch's partial writes first, then log the full
			# traceback and commit *that*, so the log entry survives even if
			# a later batch in this same run also fails and rolls back
			frappe.db.rollback()
			frappe.log_error(title=f"Failed to fix negative batch {plan['batch_no']} in {plan['warehouse']}")
			frappe.db.commit()
			plan["status"] = "error"
			plan["message"] = str(e)
			result = plan

		results.append(result)

	return results


def fix_one(batch_no: str, warehouse: str, delete_resolved_exceptions: bool = True) -> dict:
	"""Analyze + fix a single batch/warehouse, fresh from the live ledger.

	Used both by fix_all() and by the "Fix Negative Batch" button on the
	Negative Stock Batch Exception form, so a fix triggered from either place
	always re-checks current data immediately before writing anything.

	Claims the exception row with a `SELECT ... FOR UPDATE` lock before doing
	anything else: two overlapping calls for the same batch/warehouse (e.g.
	the button clicked twice, or the button clicked while fix_all() is also
	running) would otherwise both analyze the same pre-fix snapshot and each
	submit their own Repack, double-moving stock. The second caller instead
	blocks here until the first's transaction commits or rolls back, then
	re-reads -- finding the row already deleted (nothing left to do) if the
	first succeeded, or free to proceed normally if it didn't.
	"""
	exception_name = frappe.db.get_value(
		"Negative Stock Batch Exception", {"batch_no": batch_no, "warehouse": warehouse}, for_update=True
	)
	if not exception_name:
		return {
			"batch_no": batch_no,
			"warehouse": warehouse,
			"status": "healthy",
			"message": "No Negative Stock Batch Exception found for this batch/warehouse; nothing to do.",
		}

	fresh = analyze_exception(batch_no, warehouse)
	if fresh["status"] != "fixable":
		return fresh

	stock_entry = create_repack_entry(
		fresh["company"],
		fresh["warehouse"],
		fresh["item_code"],
		fresh["batch_no"],
		fresh["deficit"],
		fresh["allocations"],
		fresh["posting_datetime"],
	)
	stock_entry.insert()
	stock_entry.submit()
	fresh["stock_entry"] = stock_entry.name

	if fresh.get("reversal_datetime"):
		reversal_entry = create_reversal_entry(
			fresh["company"],
			fresh["warehouse"],
			fresh["item_code"],
			fresh["batch_no"],
			fresh["deficit"],
			fresh["allocations"],
			fresh["reversal_datetime"],
		)
		reversal_entry.insert()
		reversal_entry.submit()
		fresh["reversal_stock_entry"] = reversal_entry.name

	fresh["status"] = "fixed"

	frappe.logger("batch_correction").info(
		f"Fixed negative batch {fresh['batch_no']} in {fresh['warehouse']} via {stock_entry.name}: "
		f"injected {fresh['deficit']} from {fresh['allocations']}"
	)

	verify = analyze_exception(fresh["batch_no"], fresh["warehouse"])
	if verify["status"] != "healthy":
		fresh["status"] = "fixed_but_unverified"
		fresh["message"] = "Repack submitted but the batch still shows a negative balance; needs review."
		return fresh

	if delete_resolved_exceptions:
		# there's no uniqueness constraint on (batch_no, warehouse) beyond the
		# autoname convention, so a duplicate could exist (e.g. created via
		# the standard "New" form rather than create_exception(), or left
		# behind by a rename) -- clean up every matching row, not just the
		# one claimed above, so a fixed batch never leaves a stale exception
		# sitting around.
		exception_names = frappe.get_all(
			"Negative Stock Batch Exception",
			filters={"batch_no": batch_no, "warehouse": warehouse},
			pluck="name",
		)
		for name in exception_names:
			frappe.delete_doc("Negative Stock Batch Exception", name, ignore_permissions=True)
		if exception_names:
			fresh["exception_deleted"] = True

	return fresh

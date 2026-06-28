# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and Contributors
# See license.txt

"""Unit tests for fix_all()'s transaction handling: analyze_exception(),
fix_one(), and the DB itself are all mocked out, so these run anywhere
without a full ERPNext test environment and without writing anything real --
unlike test_fix.py, which proves the same commit/rollback behavior against a
real ledger but, for the "a later failure can't undo an earlier success"
case, needs an actual commit to do it (and is documented there as such).
"""

import unittest
from unittest.mock import patch

import frappe

from batch_correction.negative_batch_fix import fix as fix_module


def make_exception_row(name, batch_no, warehouse="WH"):
	return frappe._dict(name=name, batch_no=batch_no, warehouse=warehouse)


def make_plan(batch_no, warehouse="WH", t0="2026-01-01 00:00:00"):
	return {
		"batch_no": batch_no,
		"warehouse": warehouse,
		"status": "fixable",
		"t0": frappe.utils.get_datetime(t0),
	}


class TestFixAllTransactionHandling(unittest.TestCase):
	def test_commits_after_success_and_rolls_back_and_logs_after_failure(self):
		exceptions = [
			make_exception_row("EXC-OK", "BATCH-OK"),
			make_exception_row("EXC-FAIL", "BATCH-FAIL"),
		]
		plans = {
			"BATCH-OK": make_plan("BATCH-OK", t0="2026-01-01 00:00:00"),
			"BATCH-FAIL": make_plan("BATCH-FAIL", t0="2026-01-02 00:00:00"),
		}
		calls = []

		def fake_fix_one(batch_no, warehouse, delete_resolved_exceptions=True):
			if batch_no == "BATCH-FAIL":
				raise RuntimeError("simulated failure")
			return {"batch_no": batch_no, "warehouse": warehouse, "status": "fixed"}

		with (
			patch.object(fix_module.frappe, "get_all", return_value=exceptions),
			patch.object(fix_module, "analyze_exception", side_effect=lambda b, w: dict(plans[b])),
			patch.object(fix_module, "fix_one", side_effect=fake_fix_one),
			patch.object(fix_module.frappe.db, "commit", side_effect=lambda: calls.append("commit")),
			patch.object(fix_module.frappe.db, "rollback", side_effect=lambda: calls.append("rollback")),
			patch.object(fix_module.frappe, "log_error", side_effect=lambda **kw: calls.append("log_error")),
		):
			results = fix_module.fix_all()

		# BATCH-OK has the earlier t0, so it's processed (and committed) first;
		# BATCH-FAIL is rolled back, *then* the failure is logged, then that
		# log entry is committed so it survives on its own.
		self.assertEqual(calls, ["commit", "rollback", "log_error", "commit"])

		ok_result = next(r for r in results if r["batch_no"] == "BATCH-OK")
		fail_result = next(r for r in results if r["batch_no"] == "BATCH-FAIL")
		self.assertEqual(ok_result["status"], "fixed")
		self.assertEqual(fail_result["status"], "error")
		self.assertIn("simulated failure", fail_result["message"])

	def test_a_later_failure_cannot_undo_an_earlier_success(self):
		# same as above but with the failing batch's t0 earlier, so it's
		# processed -- and rolled back -- *before* the successful one commits.
		# The point: each batch's outcome is independent of processing order.
		exceptions = [
			make_exception_row("EXC-FAIL", "BATCH-FAIL"),
			make_exception_row("EXC-OK", "BATCH-OK"),
		]
		plans = {
			"BATCH-FAIL": make_plan("BATCH-FAIL", t0="2026-01-01 00:00:00"),
			"BATCH-OK": make_plan("BATCH-OK", t0="2026-01-02 00:00:00"),
		}
		calls = []

		def fake_fix_one(batch_no, warehouse, delete_resolved_exceptions=True):
			if batch_no == "BATCH-FAIL":
				raise RuntimeError("simulated failure")
			return {"batch_no": batch_no, "warehouse": warehouse, "status": "fixed"}

		with (
			patch.object(fix_module.frappe, "get_all", return_value=exceptions),
			patch.object(fix_module, "analyze_exception", side_effect=lambda b, w: dict(plans[b])),
			patch.object(fix_module, "fix_one", side_effect=fake_fix_one),
			patch.object(fix_module.frappe.db, "commit", side_effect=lambda: calls.append("commit")),
			patch.object(fix_module.frappe.db, "rollback", side_effect=lambda: calls.append("rollback")),
			patch.object(fix_module.frappe, "log_error", side_effect=lambda **kw: calls.append("log_error")),
		):
			results = fix_module.fix_all()

		self.assertEqual(calls, ["rollback", "log_error", "commit", "commit"])

		ok_result = next(r for r in results if r["batch_no"] == "BATCH-OK")
		fail_result = next(r for r in results if r["batch_no"] == "BATCH-FAIL")
		self.assertEqual(ok_result["status"], "fixed")
		self.assertEqual(fail_result["status"], "error")

	def test_non_fixable_plans_are_reported_without_touching_the_db(self):
		exceptions = [make_exception_row("EXC-1", "BATCH-1")]
		plan = {"batch_no": "BATCH-1", "warehouse": "WH", "status": "insufficient_donors", "message": "short"}
		calls = []

		with (
			patch.object(fix_module.frappe, "get_all", return_value=exceptions),
			patch.object(fix_module, "analyze_exception", return_value=plan),
			patch.object(fix_module, "fix_one") as fix_one_mock,
			patch.object(fix_module.frappe.db, "commit", side_effect=lambda: calls.append("commit")),
			patch.object(fix_module.frappe.db, "rollback", side_effect=lambda: calls.append("rollback")),
			patch.object(fix_module.frappe, "log_error", side_effect=lambda **kw: calls.append("log_error")),
		):
			results = fix_module.fix_all()

		fix_one_mock.assert_not_called()
		self.assertEqual(calls, [])
		self.assertEqual(results, [plan])

	def test_dry_run_logs_and_commits_on_analysis_failure(self):
		exceptions = [make_exception_row("EXC-1", "BATCH-1")]
		calls = []

		with (
			patch.object(fix_module.frappe, "get_all", return_value=exceptions),
			patch.object(fix_module, "analyze_exception", side_effect=RuntimeError("boom")),
			patch.object(fix_module.frappe.db, "commit", side_effect=lambda: calls.append("commit")),
			patch.object(fix_module.frappe, "log_error", side_effect=lambda **kw: calls.append("log_error")),
		):
			results = fix_module.dry_run()

		self.assertEqual(calls, ["log_error", "commit"])
		self.assertEqual(results[0]["status"], "error")
		self.assertIn("boom", results[0]["message"])

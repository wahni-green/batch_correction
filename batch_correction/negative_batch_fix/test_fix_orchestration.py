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
from unittest.mock import MagicMock, patch

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

	def test_fix_one_is_a_noop_when_no_exception_exists(self):
		# fix_one() claims the exception row with a real (unmocked) SELECT ...
		# FOR UPDATE before doing anything else, so two overlapping calls for
		# the same batch/warehouse can't both analyze the same pre-fix
		# snapshot and each submit their own Repack: the second blocks until
		# the first's transaction ends, then re-reads -- finding the row
		# already gone if the first succeeded. This covers that "already
		# gone" half of the contract (and that analyze_exception() is never
		# even reached in that case); the actual cross-connection blocking
		# was verified manually against this site rather than as an
		# automated test, since it needs two real, separate DB connections.
		with patch.object(fix_module, "analyze_exception") as analyze_mock:
			result = fix_module.fix_one("NO-SUCH-BATCH-XYZ", "NO-SUCH-WAREHOUSE-XYZ")

		analyze_mock.assert_not_called()
		self.assertEqual(result["status"], "healthy")
		self.assertEqual(result["batch_no"], "NO-SUCH-BATCH-XYZ")
		self.assertEqual(result["warehouse"], "NO-SUCH-WAREHOUSE-XYZ")

	def test_fix_one_deletes_every_duplicate_exception_row(self):
		# (batch_no, warehouse) has no DB-level uniqueness beyond the autoname
		# convention -- a duplicate can exist if one was created via the
		# standard "New" form (bypassing create_exception()'s own dedup
		# check) or left behind by a rename. Real rows are used here (rather
		# than mocking frappe.get_all/delete_doc) so this also covers the
		# actual filter matching them; analyze_exception()/create_repack_entry
		# are mocked since the repack mechanics themselves are covered
		# elsewhere and aren't the point of this test.
		batch_no, warehouse = "DUPTEST-BATCH", "DUPTEST-WH"

		first = frappe.get_doc(
			doctype="Negative Stock Batch Exception", batch_no=batch_no, warehouse=warehouse
		)
		first.flags.ignore_links = True
		first.insert(ignore_permissions=True)
		frappe.rename_doc("Negative Stock Batch Exception", first.name, "DUPTEST-RENAMED", force=True)

		second = frappe.get_doc(
			doctype="Negative Stock Batch Exception", batch_no=batch_no, warehouse=warehouse
		)
		second.flags.ignore_links = True
		second.insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(
			lambda: (
				frappe.db.delete("Negative Stock Batch Exception", {"batch_no": batch_no}),
				frappe.db.commit(),
			)
		)

		fixable_plan = {
			"batch_no": batch_no,
			"warehouse": warehouse,
			"item_code": "ITEM",
			"company": "COMPANY",
			"status": "fixable",
			"deficit": 5,
			"allocations": [],
			"posting_datetime": frappe.utils.now_datetime(),
		}
		healthy_plan = dict(fixable_plan, status="healthy")
		fake_stock_entry = MagicMock()
		fake_stock_entry.name = "STE-TEST"

		with (
			patch.object(
				fix_module, "analyze_exception", side_effect=[dict(fixable_plan), dict(healthy_plan)]
			),
			patch.object(fix_module, "create_repack_entry", return_value=fake_stock_entry),
		):
			result = fix_module.fix_one(batch_no, warehouse)

		self.assertEqual(result["status"], "fixed")
		self.assertTrue(result["exception_deleted"])
		self.assertEqual(frappe.get_all("Negative Stock Batch Exception", filters={"batch_no": batch_no}), [])

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

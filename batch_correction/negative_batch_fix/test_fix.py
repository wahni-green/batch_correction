# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and Contributors
# See license.txt

"""End-to-end integration tests: real Stock Ledger Entries via make_stock_entry,
real Repack Stock Entries submitted by fix_all(). See test_negative_batch_math.py
for the pure-logic unit tests that don't need a full ERPNext test environment.
"""

from unittest.mock import patch

import frappe
from erpnext.stock.doctype.serial_and_batch_bundle.test_serial_and_batch_bundle import (
	get_batch_from_bundle,
)
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.tests.test_utils import StockTestMixin
from erpnext.tests.utils import ERPNextTestSuite

from batch_correction.negative_batch_fix import fix as fix_module
from batch_correction.negative_batch_fix.analyzer import get_batch_balance_series
from batch_correction.negative_batch_fix.fix import dry_run, fix_all, fix_one

WAREHOUSE = "_Test Warehouse - _TC"


class TestFixNegativeBatches(ERPNextTestSuite, StockTestMixin):
	def make_batched_item(self, series_name):
		return self.make_item(
			properties={
				"is_stock_item": 1,
				"has_batch_no": 1,
				"create_new_batch": 1,
				"batch_number_series": series_name,
			}
		).name

	def receive(self, item_code, qty, posting_date, batch_no=None, **kwargs):
		entry = make_stock_entry(
			item_code=item_code,
			to_warehouse=WAREHOUSE,
			qty=qty,
			basic_rate=100,
			posting_date=posting_date,
			posting_time="10:00:00",
			batch_no=batch_no,
			use_serial_batch_fields=1 if batch_no else None,
			**kwargs,
		)
		return batch_no or get_batch_from_bundle(entry.items[0].serial_and_batch_bundle)

	def issue(self, item_code, qty, batch_no, posting_date, **kwargs):
		make_stock_entry(
			item_code=item_code,
			from_warehouse=WAREHOUSE,
			qty=qty,
			basic_rate=100,
			batch_no=batch_no,
			use_serial_batch_fields=1,
			posting_date=posting_date,
			posting_time="10:00:00",
			**kwargs,
		)

	def create_exception(self, batch_no):
		frappe.get_doc(
			doctype="Negative Stock Batch Exception", batch_no=batch_no, warehouse=WAREHOUSE
		).insert(ignore_permissions=True)

	@ERPNextTestSuite.change_settings("Stock Settings", {"allow_negative_stock": 1})
	def test_single_donor_covers_the_deficit(self):
		item_code = self.make_batched_item("TEST-FIX-SINGLE-.###")

		target = self.receive(item_code, 5, "2026-02-01")
		donor = self.receive(item_code, 20, "2026-02-01")

		self.create_exception(target)
		self.issue(item_code, 8, target, "2026-02-02")  # target now at -3

		results = fix_all()
		result = next(r for r in results if r["batch_no"] == target)

		self.assertEqual(result["status"], "fixed")
		self.assertEqual(result["deficit"], 3)
		self.assertEqual(result["allocations"], [(donor, 3)])
		self.assertTrue(result["exception_deleted"])
		self.assertFalse(
			frappe.db.exists("Negative Stock Batch Exception", {"batch_no": target, "warehouse": WAREHOUSE})
		)

		company = frappe.db.get_value("Warehouse", WAREHOUSE, "company")
		target_series = get_batch_balance_series(item_code, WAREHOUSE, target, company)
		self.assertEqual(target_series[-1].qty_after_transaction, 0)
		self.assertTrue(all(row.qty_after_transaction >= 0 for row in target_series))

		donor_series = get_batch_balance_series(item_code, WAREHOUSE, donor, company)
		self.assertEqual(donor_series[-1].qty_after_transaction, 17)

	@ERPNextTestSuite.change_settings("Stock Settings", {"allow_negative_stock": 1})
	def test_fix_all_fixes_multiple_independent_batches_in_one_run(self):
		# Submitting a Repack queues a Repost Item Valuation for its company
		# (ERPNext does this for any backdated entry, regardless of whether
		# anything actually needs reposting). fix_one() used to call erpnext's
		# check_pending_reposting() before submitting, which checks for *any*
		# pending repost in the company -- not scoped to this item/warehouse
		# -- so the second independent batch fixed in the same fix_all() run
		# would see the first batch's own just-queued repost and fail. Two
		# unrelated batches in the same company must both still get fixed.
		first_item = self.make_batched_item("TEST-FIX-MULTI-A-.###")
		first_target = self.receive(first_item, 5, "2026-02-01")
		self.receive(first_item, 20, "2026-02-01")
		self.create_exception(first_target)
		self.issue(first_item, 8, first_target, "2026-02-02")  # first_target now at -3

		second_item = self.make_batched_item("TEST-FIX-MULTI-B-.###")
		second_target = self.receive(second_item, 5, "2026-02-01")
		self.receive(second_item, 20, "2026-02-01")
		self.create_exception(second_target)
		self.issue(second_item, 8, second_target, "2026-02-02")  # second_target now at -3

		results = fix_all()

		first_result = next(r for r in results if r["batch_no"] == first_target)
		second_result = next(r for r in results if r["batch_no"] == second_target)
		self.assertEqual(first_result["status"], "fixed")
		self.assertEqual(second_result["status"], "fixed")

	@ERPNextTestSuite.change_settings("Stock Settings", {"allow_negative_stock": 1})
	def test_fix_one_matches_fix_all_for_a_single_batch(self):
		# fix_one() is what the form's "Fix Negative Batch" button calls; it
		# must behave the same as fix_all() does for that one batch.
		item_code = self.make_batched_item("TEST-FIX-ONE-.###")

		target = self.receive(item_code, 5, "2026-02-01")
		donor = self.receive(item_code, 20, "2026-02-01")

		self.create_exception(target)
		self.issue(item_code, 8, target, "2026-02-02")  # target now at -3

		result = fix_one(target, WAREHOUSE)

		self.assertEqual(result["status"], "fixed")
		self.assertEqual(result["deficit"], 3)
		self.assertEqual(result["allocations"], [(donor, 3)])
		self.assertTrue(result["exception_deleted"])
		self.assertFalse(
			frappe.db.exists("Negative Stock Batch Exception", {"batch_no": target, "warehouse": WAREHOUSE})
		)

	@ERPNextTestSuite.change_settings("Stock Settings", {"allow_negative_stock": 1})
	def test_a_failed_fix_does_not_leave_a_partial_repack_behind(self):
		# fix_one() raises after its Repack is already submitted (e.g. a
		# transient error past the point of no return); fix_all() must roll
		# that back rather than leave a half-applied Repack for some later,
		# unrelated commit to pick up. See test_fix_orchestration.py for the
		# companion case (an unrelated success surviving this rollback) --
		# that one needs a real commit to prove, so it's done there with
		# everything mocked out instead of against this real ledger data.
		item_code = self.make_batched_item("TEST-FIX-FAIL-.###")
		target = self.receive(item_code, 5, "2026-02-01")
		self.receive(item_code, 20, "2026-02-01")
		self.create_exception(target)
		self.issue(item_code, 8, target, "2026-02-02")  # target now at -3

		stock_entry_count_before = frappe.db.count("Stock Entry")
		real_create_repack_entry = fix_module.create_repack_entry

		def sabotage(*args, **kwargs):
			stock_entry = real_create_repack_entry(*args, **kwargs)
			real_submit = stock_entry.submit

			def failing_submit():
				real_submit()  # let it actually write, then blow up
				raise RuntimeError("simulated failure after submit")

			stock_entry.submit = failing_submit
			return stock_entry

		with patch.object(fix_module, "create_repack_entry", side_effect=sabotage):
			results = fix_all()

		result = next(r for r in results if r["batch_no"] == target)
		self.assertEqual(result["status"], "error")
		self.assertIn("simulated failure after submit", result["message"])

		# the Repack must not have survived the rollback
		self.assertEqual(frappe.db.count("Stock Entry"), stock_entry_count_before)
		self.assertTrue(
			frappe.db.exists("Negative Stock Batch Exception", {"batch_no": target, "warehouse": WAREHOUSE})
		)

	@ERPNextTestSuite.change_settings("Stock Settings", {"allow_negative_stock": 1})
	def test_splits_across_multiple_donors_largest_headroom_first(self):
		item_code = self.make_batched_item("TEST-FIX-SPLIT-.###")

		target = self.receive(item_code, 5, "2026-02-01")
		small_donor = self.receive(item_code, 4, "2026-02-01")
		big_donor = self.receive(item_code, 7, "2026-02-01")

		self.create_exception(target)
		self.issue(item_code, 15, target, "2026-02-02")  # target now at -10

		results = fix_all()
		result = next(r for r in results if r["batch_no"] == target)

		self.assertEqual(result["status"], "fixed")
		self.assertEqual(result["deficit"], 10)
		self.assertEqual(result["allocations"], [(big_donor, 7), (small_donor, 3)])

		company = frappe.db.get_value("Warehouse", WAREHOUSE, "company")
		self.assertEqual(
			get_batch_balance_series(item_code, WAREHOUSE, target, company)[-1].qty_after_transaction, 0
		)
		self.assertEqual(
			get_batch_balance_series(item_code, WAREHOUSE, big_donor, company)[-1].qty_after_transaction, 0
		)
		self.assertEqual(
			get_batch_balance_series(item_code, WAREHOUSE, small_donor, company)[-1].qty_after_transaction, 1
		)

	@ERPNextTestSuite.change_settings("Stock Settings", {"allow_negative_stock": 1})
	def test_insufficient_donors_is_reported_and_nothing_is_written(self):
		item_code = self.make_batched_item("TEST-FIX-SHORT-.###")

		target = self.receive(item_code, 5, "2026-02-01")
		donor = self.receive(item_code, 4, "2026-02-01")

		self.create_exception(target)
		self.issue(item_code, 20, target, "2026-02-02")  # target now at -15, only 4 available anywhere

		stock_entry_count_before = frappe.db.count("Stock Entry")

		results = fix_all()
		result = next(r for r in results if r["batch_no"] == target)

		self.assertEqual(result["status"], "insufficient_donors")
		self.assertEqual(result["shortfall"], 11)
		self.assertEqual(frappe.db.count("Stock Entry"), stock_entry_count_before)
		self.assertTrue(
			frappe.db.exists("Negative Stock Batch Exception", {"batch_no": target, "warehouse": WAREHOUSE})
		)
		# the lone donor must be untouched
		company = frappe.db.get_value("Warehouse", WAREHOUSE, "company")
		self.assertEqual(
			get_batch_balance_series(item_code, WAREHOUSE, donor, company)[-1].qty_after_transaction, 4
		)

	@ERPNextTestSuite.change_settings("Stock Settings", {"allow_negative_stock": 1})
	def test_dry_run_never_writes_anything(self):
		item_code = self.make_batched_item("TEST-FIX-DRYRUN-.###")

		target = self.receive(item_code, 5, "2026-02-01")
		self.receive(item_code, 20, "2026-02-01")

		self.create_exception(target)
		self.issue(item_code, 8, target, "2026-02-02")

		stock_entry_count_before = frappe.db.count("Stock Entry")

		results = dry_run()
		result = next(r for r in results if r["batch_no"] == target)

		self.assertEqual(result["status"], "fixable")
		self.assertEqual(result["deficit"], 3)
		self.assertEqual(frappe.db.count("Stock Entry"), stock_entry_count_before)
		self.assertTrue(
			frappe.db.exists("Negative Stock Batch Exception", {"batch_no": target, "warehouse": WAREHOUSE})
		)

# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and Contributors
# See license.txt

"""Pure-logic tests for the deficit/headroom math, independent of any real
ledger data: no Stock Entries, no ERPNextTestSuite bootstrap, no DB writes.
See test_fix.py for the end-to-end integration tests that exercise this logic
against real Stock Ledger Entries and submit actual Repack Stock Entries.
"""

import unittest

import frappe

from batch_correction.negative_batch_fix.analyzer import find_negative_window
from batch_correction.negative_batch_fix.donors import compute_headroom


def series(*rows):
	"""rows: (date, qty_after_transaction) pairs, oldest first."""
	return [frappe._dict(date=date, qty_after_transaction=qty, voucher_no="x") for date, qty in rows]


class TestFindNegativeWindow(unittest.TestCase):
	def test_no_negative_balance_returns_none(self):
		s = series(("2026-01-01 10:00:00", 5), ("2026-01-02 10:00:00", 2))
		self.assertIsNone(find_negative_window(s, precision=2))

	def test_simple_dip(self):
		s = series(("2026-01-01 10:00:00", 5), ("2026-01-02 10:00:00", -3))
		t0, deficit = find_negative_window(s, precision=2)
		self.assertEqual(str(t0), "2026-01-02 10:00:00")
		self.assertEqual(deficit, 3)

	def test_deeper_dip_after_recovery_sizes_to_worst_point(self):
		# goes negative at step 2 (-3), recovers at step 3 (+2), then dips deeper
		# at step 4 (-10): a fix sized only to the first dip would not be enough.
		s = series(
			("2026-01-01 10:00:00", 5),
			("2026-01-02 10:00:00", -3),
			("2026-01-03 10:00:00", 2),
			("2026-01-04 10:00:00", -10),
		)
		t0, deficit = find_negative_window(s, precision=2)
		self.assertEqual(str(t0), "2026-01-02 10:00:00")
		self.assertEqual(deficit, 10)

	def test_dip_within_rounding_precision_is_not_negative(self):
		s = series(("2026-01-01 10:00:00", 5), ("2026-01-02 10:00:00", -0.001))
		self.assertIsNone(find_negative_window(s, precision=2))


class TestComputeHeadroom(unittest.TestCase):
	def test_no_activity_after_since_uses_carried_balance(self):
		s = series(("2026-01-01 10:00:00", 4))
		headroom = compute_headroom(s, since="2026-01-05 00:00:00", precision=2)
		self.assertEqual(headroom, 4)

	def test_interim_dip_caps_headroom_even_if_recovered_by_now(self):
		# 10 received before `since`, drops to 1 the day after, then recovers to
		# 16 later: the donor can only safely spare 1 as of `since`, not its
		# current balance of 16.
		s = series(
			("2026-01-01 10:00:00", 10),
			("2026-01-02 10:00:00", 1),
			("2026-01-04 10:00:00", 16),
		)
		headroom = compute_headroom(s, since="2026-01-01 10:00:01", precision=2)
		self.assertEqual(headroom, 1)

	def test_balance_before_since_is_also_considered(self):
		s = series(("2026-01-01 10:00:00", 3), ("2026-01-05 10:00:00", 9))
		# nothing happens until well after `since`, so the carried-over balance
		# from before `since` is the binding constraint, not the later rows.
		headroom = compute_headroom(s, since="2026-01-02 00:00:00", precision=2)
		self.assertEqual(headroom, 3)

	def test_negative_balance_before_since_gives_zero_headroom(self):
		s = series(("2026-01-01 10:00:00", -2), ("2026-01-05 10:00:00", 9))
		headroom = compute_headroom(s, since="2026-01-02 00:00:00", precision=2)
		self.assertEqual(headroom, -2)

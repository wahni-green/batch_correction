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

	def test_simple_dip_no_recovery(self):
		# Single dip at the end of the series -- no later row recovers it.
		s = series(("2026-01-01 10:00:00", 5), ("2026-01-02 10:00:00", -3))
		t0, deficit, recovery_time = find_negative_window(s, precision=2)
		self.assertEqual(str(t0), "2026-01-02 10:00:00")
		self.assertEqual(deficit, 3)
		self.assertIsNone(recovery_time)

	def test_dip_with_natural_recovery(self):
		# Goes negative then recovers: recovery_time points at the recovery row.
		s = series(
			("2026-01-01 10:00:00", 5),
			("2026-01-02 10:00:00", -3),
			("2026-01-03 10:00:00", 2),
		)
		t0, deficit, recovery_time = find_negative_window(s, precision=2)
		self.assertEqual(str(t0), "2026-01-02 10:00:00")
		self.assertEqual(deficit, 3)
		self.assertEqual(str(recovery_time), "2026-01-03 10:00:00")

	def test_second_deeper_dip_after_recovery_does_not_affect_first_window_deficit(self):
		# First window: negative at day 2 (-3), recovers at day 3 (+2).
		# Second window starts at day 4 (-10) -- that's a separate bridge pass.
		# deficit is sized to the FIRST window only (3, not 10): the reversal
		# at recovery_time+1s ends the bridge, so subsequent windows are detected
		# by the verify pass in fix_one and handled on the next iteration.
		s = series(
			("2026-01-01 10:00:00", 5),
			("2026-01-02 10:00:00", -3),
			("2026-01-03 10:00:00", 2),
			("2026-01-04 10:00:00", -10),
		)
		t0, deficit, recovery_time = find_negative_window(s, precision=2)
		self.assertEqual(str(t0), "2026-01-02 10:00:00")
		self.assertEqual(deficit, 3)
		self.assertEqual(str(recovery_time), "2026-01-03 10:00:00")

	def test_no_recovery_uses_global_trough(self):
		# When the batch never recovers, deficit must cover the deepest point
		# (no bridge reversal is possible, so a permanent donor is required).
		s = series(
			("2026-01-01 10:00:00", 5),
			("2026-01-02 10:00:00", -3),
			("2026-01-03 10:00:00", -10),
		)
		t0, deficit, recovery_time = find_negative_window(s, precision=2)
		self.assertEqual(str(t0), "2026-01-02 10:00:00")
		self.assertEqual(deficit, 10)
		self.assertIsNone(recovery_time)

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
		# a batch that's already negative before `since` has nothing to
		# spare -- headroom is floored at 0, not the raw (negative) balance,
		# since find_donor_allocations only ever treats this as "unavailable"
		# regardless of how negative it is.
		s = series(("2026-01-01 10:00:00", -2), ("2026-01-05 10:00:00", 9))
		headroom = compute_headroom(s, since="2026-01-02 00:00:00", precision=2)
		self.assertEqual(headroom, 0)

	def test_until_excludes_later_depletion(self):
		# A donor holds 10 through the bridge window (day 1-3) but drops to 0
		# by day 5 (fully consumed).  Without `until` the headroom would be 0;
		# with `until=day 3` it is correctly 10 -- the reversal repack at
		# recovery_time+1s returns the stock before the donor is depleted.
		s = series(
			("2026-01-01 10:00:00", 10),
			("2026-01-05 10:00:00", 0),
		)
		self.assertEqual(compute_headroom(s, since="2026-01-01 10:00:01", precision=2), 0)
		self.assertEqual(
			compute_headroom(s, since="2026-01-01 10:00:01", precision=2, until="2026-01-03 00:00:00"),
			10,
		)

	def test_until_boundary_row_is_included(self):
		# A row exactly at `until` is inside the window (the reversal is 1s later).
		s = series(
			("2026-01-01 10:00:00", 20),
			("2026-01-03 10:00:00", 5),
			("2026-01-05 10:00:00", 0),
		)
		headroom = compute_headroom(
			s, since="2026-01-01 10:00:01", precision=2, until="2026-01-03 10:00:00"
		)
		self.assertEqual(headroom, 5)

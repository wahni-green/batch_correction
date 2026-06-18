# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
	SerialandBatchBundle,
)
from frappe import _, bold
from frappe.utils import flt, format_datetime, get_datetime


class CustomSerialandBatchBundle(SerialandBatchBundle):
	def get_batchwise_available_qty(self):
		batchwise_entries = self.get_available_qty_from_sabb()
		batchwise_entries.extend(self.get_available_qty_from_stock_ledger())

		available_qty = frappe._dict({})
		batchwise_entries = sorted(
			batchwise_entries,
			key=lambda x: (get_datetime(x.get("posting_datetime")), get_datetime(x.get("creation"))),
		)

		# identifies which replayed rows belong to this document, so the
		# unconditional block below only ever applies to a dip this very
		# transaction causes - any other (already-committed) row that is
		# negative always falls back to the Stock Settings toggle, regardless
		# of whether it is the chronologically "first" negative point or not
		self_entry_keys = {
			(entry.batch_no, get_datetime(entry.creation))
			for entry in self.entries
			if entry.batch_no and entry.creation
		}

		precision = frappe.get_precision("Serial and Batch Entry", "qty")
		ever_negative = frappe._dict({})
		for row in batchwise_entries:
			was_ever_negative = ever_negative.get(row.batch_no, False)

			if row.batch_no in available_qty:
				available_qty[row.batch_no] += flt(row.qty)
			else:
				available_qty[row.batch_no] = flt(row.qty)

			new_qty = flt(available_qty[row.batch_no], precision)
			if new_qty >= 0:
				continue

			if was_ever_negative:
				# this batch already has negative history - unchanged
				# behaviour, governed by "Allow Negative Stock for Batch"
				self.throw_negative_batch(row.batch_no, new_qty, precision, row.posting_datetime)
			elif (row.batch_no, get_datetime(row.creation)) in self_entry_keys:
				# this batch has never gone negative before, and it is this
				# very transaction that pushes it negative - block outright,
				# regardless of "Allow Negative Stock for Batch"
				self.throw_first_time_negative_batch(row.batch_no, new_qty, precision, row.posting_datetime)
			else:
				# some other, already-committed transaction's row turns out to
				# be the first negative point for this batch - unchanged
				# behaviour, governed by "Allow Negative Stock for Batch"
				self.throw_negative_batch(row.batch_no, new_qty, precision, row.posting_datetime)

			ever_negative[row.batch_no] = True

		return available_qty

	def throw_first_time_negative_batch(self, batch_no, available_qty, precision, posting_datetime=None):
		from erpnext.stock.stock_ledger import NegativeStockError

		date_msg = ""
		if posting_datetime:
			date_msg = " " + _("as of {0}").format(format_datetime(posting_datetime))

		msg = _(
			"""
			The Batch {0} of an item {1}, which has had a non-negative balance until now,
			would go negative in the warehouse {2}{3}.
			Please add a stock quantity of {4} to proceed with this entry."""
		).format(
			bold(batch_no),
			bold(self.item_code),
			bold(self.warehouse),
			date_msg,
			bold(abs(flt(available_qty, precision))),
		)

		frappe.throw(
			msg,
			title=_("Negative Stock Error"),
			exc=NegativeStockError,
		)

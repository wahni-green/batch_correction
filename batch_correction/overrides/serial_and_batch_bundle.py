# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
	SerialandBatchBundle,
)
from frappe import _, bold
from frappe.utils import flt, format_datetime
from batch_correction.batch_correction.doctype.negative_stock_batch_exception.negative_stock_batch_exception import create_exception


class CustomSerialandBatchBundle(SerialandBatchBundle):
	def throw_negative_batch(self, batch_no, available_qty, precision, posting_datetime=None):
		if frappe.db.exists(
			"Negative Stock Batch Exception", {"batch_no": batch_no, "warehouse": self.warehouse}
		):
			# create_exception(batch_no=batch_no, warehouse=self.warehouse)
			return  # If an exception already exists, do not throw an error again

		# super().throw_negative_batch(batch_no, available_qty, precision, posting_datetime)

		date_msg = ""
		if posting_datetime:
			date_msg = " " + _("as of {0}").format(format_datetime(posting_datetime))

		msg = _(
			"""
			The Batch {0} of an item {1} has negative stock in the warehouse {2}{3}.
			Please add a stock quantity of {4} to proceed with this entry.
			If it is not possible to make an adjustment entry, click 'Create Exception' below
			to allow this batch and warehouse to go negative, then retry the transaction.
			However, doing so may lead to incorrect valuation rates, so please ensure the
			stock levels are adjusted as soon as possible to maintain the correct valuation rate."""
		).format(
			bold(batch_no),
			bold(self.item_code),
			bold(self.warehouse),
			date_msg,
			bold(abs(flt(available_qty, precision))),
		)

		from erpnext.stock.stock_ledger import NegativeStockError
		frappe.throw(
			msg,
			title=_("Negative Stock Error"),
			exc=NegativeStockError,
			primary_action={
				"label": _("Create Exception"),
				"server_action": (
					"batch_correction.batch_correction.doctype"
					".negative_stock_batch_exception.negative_stock_batch_exception.create_exception"
				),
				"args": {"batch_no": batch_no, "warehouse": self.warehouse},
				"hide_on_success": True,
			},
		)

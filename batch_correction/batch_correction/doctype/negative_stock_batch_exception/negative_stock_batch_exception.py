# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _, bold
from frappe.model.document import Document


class NegativeStockBatchException(Document):
	pass


@frappe.whitelist()
def create_exception(batch_no: str, warehouse: str) -> str:
	existing = frappe.db.exists(
		"Negative Stock Batch Exception", {"batch_no": batch_no, "warehouse": warehouse}
	)
	if existing:
		return existing

	doc = frappe.new_doc("Negative Stock Batch Exception")
	doc.batch_no = batch_no
	doc.warehouse = warehouse
	doc.insert(ignore_permissions=True)

	frappe.msgprint(
		_("Negative Stock Batch Exception created for Batch {0} in Warehouse {1}. Please retry the transaction.").format(
			bold(batch_no), bold(warehouse)
		),
		alert=True,
		indicator="green",
	)
	return doc.name

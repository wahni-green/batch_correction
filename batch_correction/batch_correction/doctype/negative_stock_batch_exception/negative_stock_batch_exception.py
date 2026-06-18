# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _, bold
from frappe.model.document import Document


class NegativeStockBatchException(Document):
	pass


@frappe.whitelist()
def create_exception(
	batch_no: str | None = None, warehouse: str | None = None, args: str | None = None
) -> str:
	if (not batch_no or not warehouse) and args:
		# the dialog's primary_action button can arrive with batch_no/warehouse
		# bundled into a single JSON-encoded "args" field instead of being
		# sent as separate top-level params, depending on how the framework
		# dispatches the call - fall back to unpacking it from there
		wrapped_args = frappe.parse_json(args)
		batch_no = batch_no or wrapped_args.get("batch_no")
		warehouse = warehouse or wrapped_args.get("warehouse")

	if not batch_no or not warehouse:
		frappe.throw(_("Batch and Warehouse are required to create an exception."))

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
		_(
			"Negative Stock Batch Exception created for Batch {0} in Warehouse {1}."
			" Please retry the transaction."
		).format(bold(batch_no), bold(warehouse)),
		alert=True,
		indicator="green",
	)
	return doc.name

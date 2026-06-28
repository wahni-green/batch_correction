import frappe


def execute():
	frappe.delete_doc("Report", "Custom Negative Batch", ignore_missing=True, force=True)

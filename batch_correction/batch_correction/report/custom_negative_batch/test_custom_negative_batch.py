# Copyright (c) 2026, Wahni IT Solutions Pvt Ltd and Contributors
# See license.txt

import frappe

from batch_correction.batch_correction.report.custom_negative_batch.custom_negative_batch import execute
from erpnext.stock.doctype.serial_and_batch_bundle.test_serial_and_batch_bundle import (
	get_batch_from_bundle,
)
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.tests.test_utils import StockTestMixin
from erpnext.tests.utils import ERPNextTestSuite


class TestCustomNegativeBatch(ERPNextTestSuite, StockTestMixin):
	@ERPNextTestSuite.change_settings("Stock Settings", {"allow_negative_stock": 1})
	def test_negative_batch_is_reported_once(self):
		item_code = self.make_item(
			properties={
				"is_stock_item": 1,
				"has_batch_no": 1,
				"create_new_batch": 1,
				"batch_number_series": "TEST-NEG-BATCH-.###",
			}
		).name

		warehouse = "_Test Warehouse - _TC"

		receipt = make_stock_entry(
			item_code=item_code,
			to_warehouse=warehouse,
			qty=5,
			basic_rate=100,
			posting_date="2026-01-01",
			posting_time="10:00:00",
		)
		batch_no = get_batch_from_bundle(receipt.items[0].serial_and_batch_bundle)

		# issuing more than the available batch qty drives it negative
		make_stock_entry(
			item_code=item_code,
			from_warehouse=warehouse,
			qty=8,
			basic_rate=100,
			batch_no=batch_no,
			use_serial_batch_fields=1,
			posting_date="2026-01-02",
			posting_time="10:00:00",
		)

		# a later, unrelated receipt brings the batch positive again and
		# must not produce a second reported row for the same batch/warehouse
		make_stock_entry(
			item_code=item_code,
			to_warehouse=warehouse,
			qty=10,
			basic_rate=100,
			batch_no=batch_no,
			use_serial_batch_fields=1,
			posting_date="2026-01-03",
			posting_time="10:00:00",
		)

		_columns, data = execute(
			frappe._dict(company="_Test Company", warehouse=warehouse, item_code=item_code)
		)

		rows = [row for row in data if row["batch_no"] == batch_no]
		self.assertEqual(len(rows), 1)

		row = rows[0]
		self.assertEqual(row["warehouse"], warehouse)
		self.assertEqual(row["item_code"], item_code)
		self.assertEqual(row["previous_qty"], 5)
		self.assertEqual(row["actual_qty"], -8)
		self.assertEqual(row["qty_after_transaction"], -3)

	def test_no_false_positives_for_healthy_batch(self):
		item_code = self.make_item(
			properties={
				"is_stock_item": 1,
				"has_batch_no": 1,
				"create_new_batch": 1,
				"batch_number_series": "TEST-OK-BATCH-.###",
			}
		).name

		warehouse = "_Test Warehouse - _TC"

		receipt = make_stock_entry(
			item_code=item_code,
			to_warehouse=warehouse,
			qty=5,
			basic_rate=100,
			posting_date="2026-01-01",
			posting_time="10:00:00",
		)
		batch_no = get_batch_from_bundle(receipt.items[0].serial_and_batch_bundle)

		make_stock_entry(
			item_code=item_code,
			from_warehouse=warehouse,
			qty=3,
			basic_rate=100,
			batch_no=batch_no,
			use_serial_batch_fields=1,
			posting_date="2026-01-02",
			posting_time="10:00:00",
		)

		_columns, data = execute(
			frappe._dict(company="_Test Company", warehouse=warehouse, item_code=item_code)
		)

		rows = [row for row in data if row["batch_no"] == batch_no]
		self.assertEqual(len(rows), 0)

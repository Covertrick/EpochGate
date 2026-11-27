import unittest
from shipping import shipping_cost


class ShippingTest(unittest.TestCase):
    def test_empty_cart(self):
        self.assertEqual(shipping_cost(0, 0), 0)

    def test_standard_shipping(self):
        self.assertEqual(shipping_cost(1, 1000), 450)

    def test_threshold(self):
        from rules import FREE_FROM
        self.assertEqual(shipping_cost(2, FREE_FROM), 0)
        self.assertEqual(shipping_cost(2, FREE_FROM - 1), 450)

    def test_negative_inputs(self):
        with self.assertRaises(ValueError):
            shipping_cost(-1, 0)

Fix shipping_cost. Empty carts ship free, nonempty carts ship free at the configured
threshold in rules.py, and carts below the threshold cost 450 cents.
Preserve validation for negative inputs. Change only shipping.py.

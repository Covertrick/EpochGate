def shipping_cost(items, subtotal):
    """Calculate shipping in integer cents."""
    if items < 0 or subtotal < 0:
        raise ValueError("negative inputs")
    return 450

import rules


def shipping_cost(items, subtotal):
    """Calculate shipping in integer cents."""
    if items < 0 or subtotal < 0:
        raise ValueError("negative inputs")
    if items == 0 or subtotal >= rules.FREE_FROM:
        return 0
    return 450

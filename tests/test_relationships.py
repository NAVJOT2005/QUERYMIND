from querymind.adapters.base import Column, ForeignKey, Schema, Table


def test_explicit_and_inferred_relationships():
    schema = Schema(
        database="shop",
        tables={
            "customers": Table(
                name="customers",
                columns=[
                    Column("id", "int", False, True),
                    Column("name", "varchar(100)"),
                ],
            ),
            "orders": Table(
                name="orders",
                columns=[
                    Column("id", "int", False, True),
                    Column("customer_id", "int", False, False),
                    Column("status", "varchar(20)"),
                ],
                foreign_keys=[ForeignKey(["customer_id"], "customers", ["id"])],
            ),
            "order_items": Table(
                name="order_items",
                columns=[
                    Column("id", "int", False, True),
                    Column("order_id", "int", False, False),  # No explicit FK declared!
                    Column("product_id", "int", False, False),
                ],
                foreign_keys=[],  # purposely empty to test inferred joins
            ),
            "products": Table(
                name="products",
                columns=[
                    Column("id", "int", False, True),
                    Column("name", "varchar(100)"),
                ],
            ),
        },
    )

    rels = schema.find_relationships()
    # Explicit FK
    assert any("orders.customer_id -> customers.id [FK]" in r for r in rels)

    # Inferred joins
    assert any("order_items.order_id -> orders.id" in r for r in rels)
    assert any("order_items.product_id -> products.id" in r for r in rels)
    assert any("inferred join: ON order_items.order_id = orders.id" in r for r in rels)

    # Filter by specific table
    orders_rels = schema.find_relationships("orders")
    assert all("orders" in r for r in orders_rels)

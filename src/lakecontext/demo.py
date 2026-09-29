from lakecontext.models import Snapshot


def demo_snapshot() -> Snapshot:
    return Snapshot.model_validate(
        {
            "source": "demo://commerce",
            "principal": "demo-user",
            "tables": [
                {
                    "name": "demo.sales.customers",
                    "description": "One row per customer account.",
                    "owner": "customer-team",
                    "columns": [
                        {
                            "name": "customer_id",
                            "data_type": "BIGINT",
                            "description": "Customer identifier",
                        },
                        {"name": "region", "data_type": "STRING", "description": "Sales region"},
                        {"name": "created_at", "data_type": "TIMESTAMP"},
                    ],
                },
                {
                    "name": "demo.sales.orders",
                    "description": "One row per order; includes cancelled orders.",
                    "owner": "commerce-team",
                    "columns": [
                        {"name": "order_id", "data_type": "BIGINT"},
                        {"name": "customer_id", "data_type": "BIGINT"},
                        {
                            "name": "amount",
                            "data_type": "DECIMAL(18,2)",
                            "description": "Order value in USD",
                        },
                        {
                            "name": "status",
                            "data_type": "STRING",
                            "description": "paid, pending or cancelled",
                        },
                    ],
                },
                {
                    "name": "demo.finance.payments",
                    "description": "Payment attempts; several may exist per order.",
                    "owner": "finance-team",
                    "columns": [
                        {"name": "payment_id", "data_type": "BIGINT"},
                        {"name": "order_id", "data_type": "BIGINT"},
                        {"name": "settled_amount", "data_type": "DECIMAL(18,2)"},
                    ],
                },
            ],
            "queries": [
                {
                    "id": "demo-orders-by-region",
                    "sql": "SELECT c.region, SUM(o.amount) FROM demo.sales.orders o JOIN demo.sales.customers c ON o.customer_id = c.customer_id WHERE o.status = 'paid' GROUP BY c.region",
                },
                {
                    "id": "demo-order-payments",
                    "sql": "SELECT o.order_id, p.payment_id FROM demo.sales.orders o JOIN demo.finance.payments p ON o.order_id = p.order_id",
                },
            ],
            "terms": [
                {
                    "name": "Booked revenue",
                    "definition": "Sum orders.amount where orders.status = 'paid'. USD. Excludes pending and cancelled orders. Not equivalent to settled cash.",
                    "tables": ["demo.sales.orders"],
                }
            ],
            "lineage": [],
        }
    )

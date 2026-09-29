-- Deliberately use a BIGINT column for the restricted prototype's integer SUM.
-- Summing order IDs is an arithmetic example, not a meaningful business metric.
SELECT SUM(o.order_id)
FROM demo.sales.orders AS o
INNER JOIN demo.sales.customers AS c ON o.customer_id = c.customer_id

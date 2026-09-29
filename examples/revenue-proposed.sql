-- Proposed change: include refunded-status orders as well.
-- This conflicts with the configured paid-status-only definition.
SELECT i.category, SUM(i.line_minor) AS revenue_minor
FROM demo.commerce.orders AS o
JOIN demo.commerce.order_items AS i ON o.order_id = i.order_id
WHERE o.status IN ('paid', 'refunded') AND o.currency = 'INR'
GROUP BY i.category
ORDER BY i.category;

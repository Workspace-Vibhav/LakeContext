-- Synthetic example: paid INR orders only, allocated using net item amounts.
SELECT i.category, SUM(i.line_minor) AS revenue_minor
FROM demo.commerce.orders AS o
JOIN demo.commerce.order_items AS i ON o.order_id = i.order_id
WHERE o.status = 'paid' AND o.currency = 'INR'
GROUP BY i.category
ORDER BY i.category;

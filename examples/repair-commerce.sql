-- Choose whether reviews should be optional or determine which orders contribute.
SELECT SUM(o.total_cents) AS revenue_cents
FROM demo.sales.orders AS o
LEFT JOIN demo.sales.reviews AS r
  ON o.customer_id = r.customer_id

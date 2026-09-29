-- Hypothesis: joining customer reviews should preserve total transaction value.
-- This asks the auditor to test that intent, not to run a Databricks query.
SELECT SUM(t.totalPrice) AS revenue
FROM samples.bakehouse.sales_transactions AS t
LEFT JOIN samples.bakehouse.media_customer_reviews AS r
  ON t.franchiseID = r.franchiseID

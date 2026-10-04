-- case_id: D14_SM_006
-- metric_id: delivered_gmv_including_freight
SELECT ROUND(SUM(i.price + i.freight_value), 2) AS delivered_gmv_including_freight
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;

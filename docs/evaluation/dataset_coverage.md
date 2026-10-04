# Evaluation Dataset Coverage

This report measures dataset composition and reference integrity. It is not candidate-model accuracy or independent business accuracy.

## Validation summary

- Overall validation: `true`
- Cases: `60`
- Critical failures: `0`
- External API calls: `0`
- Candidate-model runs: `0`

## Category and difficulty

| Category | easy | medium | hard | total |
|---|---:|---:|---:|---:|
| single_metric | 8 | 8 | 4 | 20 |
| aggregate_filter_join | 4 | 11 | 5 | 20 |
| multi_step | 0 | 3 | 7 | 10 |
| risk_ambiguous_unanswerable | 3 | 4 | 3 | 10 |

## Workflow status

- `environment_failed`: 1
- `needs_clarification`: 1
- `planning_failed`: 4
- `resource_failed`: 1
- `safety_rejected`: 3
- `succeeded`: 50

## Calculation status

- `computed`: 4
- `detected`: 1
- `missing_comparison_period`: 1
- `missing_current_period`: 1
- `non_contiguous_history`: 1
- `not_applicable`: 50
- `zero_baseline`: 1
- `zero_dispersion`: 1

## Metric coverage

- `average_items_per_delivered_order`: 2
- `average_order_delivery_days`: 2
- `canceled_order_count`: 3
- `delivered_average_order_value`: 1
- `delivered_category_gmv_contribution`: 1
- `delivered_customer_count`: 2
- `delivered_freight_amount`: 2
- `delivered_freight_to_gmv_rate`: 2
- `delivered_gmv`: 4
- `delivered_gmv_including_freight`: 2
- `delivered_item_count`: 2
- `delivered_monthly_category_gmv_rank`: 1
- `delivered_monthly_gmv`: 8
- `delivered_order_count`: 3
- `delivered_payment_amount`: 5
- `delivered_payment_reconciliation_difference`: 1
- `historical_returning_customer_rate`: 1
- `invalid_carrier_delivery_sequence_count`: 2
- `on_time_delivery_rate`: 2
- `period_repeat_customer_count`: 1
- `period_repeat_customer_rate`: 2
- `terminal_cancellation_rate`: 2
- `terminal_order_count`: 1
- `unavailable_order_count`: 1

## Dimension coverage

- `customer_city`: 1
- `customer_state`: 12
- `marketing_channel`: 1
- `payment_type`: 1
- `product`: 1
- `product_category`: 6
- `purchase_month`: 4
- `seller`: 2

## Uncovered dictionary metrics

- `average_carrier_delivery_days`
- `delivered_gmv_mom_rate`
- `delivered_gmv_yoy_rate`

## Analysis type coverage

- `ambiguous_sales_amount`: 1
- `anomaly_detection`: 1
- `anomaly_missing_current_period`: 1
- `anomaly_non_contiguous_history`: 1
- `anomaly_zero_dispersion`: 1
- `category_contribution`: 1
- `category_gmv_freight_top_n`: 1
- `category_ratio_top_n`: 1
- `category_top_n_count`: 1
- `city_payment_top_n`: 1
- `data_quality_count`: 1
- `database_unavailable_environment_failure`: 1
- `delivery_quality_rate`: 1
- `dense_rank_top_n`: 1
- `distinct_entity_count`: 1
- `duration_average`: 1
- `filtered_grouped_rate_top_n`: 1
- `grouped_conditional_rate`: 1
- `grouped_data_quality_count`: 1
- `grouped_distinct_customer_count`: 1
- `grouped_duration_top_n`: 1
- `grouped_items_per_order`: 1
- `grouped_payment_amount`: 1
- `grouped_repeat_rate`: 1
- `historical_returning_rate`: 1
- `missing_inventory_and_forecast_inputs`: 1
- `mom_incomplete_current_period`: 1
- `mom_missing_comparison`: 1
- `mom_zero_baseline`: 1
- `month_over_month`: 1
- `monthly_amount_trend`: 1
- `monthly_grouped_count`: 1
- `monthly_payment_trend`: 1
- `preaggregated_fact_reconciliation`: 1
- `repeat_customer_count`: 1
- `repeat_customer_rate`: 1
- `scalar_amount`: 4
- `scalar_average`: 2
- `scalar_count`: 5
- `scalar_rate`: 2
- `seller_freight_top_n`: 1
- `seller_top_n`: 1
- `sqlite_timeout_resource_failure`: 1
- `top_n_grouped_amount`: 1
- `unsafe_delete_request`: 1
- `unsafe_multiple_statement_request`: 1
- `unsafe_update_request`: 1
- `unsupported_channel_and_ad_spend`: 1
- `unsupported_conversion_funnel`: 1
- `unsupported_metric_dimension_combination`: 1
- `year_over_year`: 1

## Template repetition audit

- Passed: `true`
- Normalized exact-template duplicates: `0`
- High-similarity pairs: `0` / `1770`
- High-similarity pair ratio: `0.000000`
- Largest high-similarity cluster: `0`

Top ten pairs are shown for audit even when they are below the failure threshold.

| Left | Right | Similarity |
|---|---|---:|
| D14_SM_020 | D14_AJ_020 | 0.723926 |
| D14_SM_008 | D14_SM_011 | 0.688073 |
| D14_SM_011 | D14_AJ_007 | 0.611872 |
| D14_SM_007 | D14_SM_014 | 0.593023 |
| D14_AJ_003 | D14_AJ_010 | 0.578199 |
| D14_SM_015 | D14_AJ_006 | 0.570048 |
| D14_AJ_004 | D14_AJ_017 | 0.561224 |
| D14_SM_002 | D14_SM_015 | 0.560847 |
| D14_SM_001 | D14_SM_009 | 0.552239 |
| D14_AJ_010 | D14_AJ_018 | 0.541872 |

## Authorship and evidence disclosure

- All 60 current questions are assistant-mechanically authored.
- User-reviewed or user-authored cases: 0.
- Independently verified business references: 0.
- Real SQLite verification and deterministic policy contracts are tracked separately from independent business review.

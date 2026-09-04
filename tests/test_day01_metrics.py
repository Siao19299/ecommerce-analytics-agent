import pytest

from src.ecommerce_agent.day01_metrics import (
    calculate_delivered_gmv,
    count_delivered_orders,
)


def test_calculates_delivered_gmv():
    orders = [
        {"order_id": "o001", "order_status": "delivered"},
        {"order_id": "o002", "order_status": "canceled"},
    ]
    items = [
        {"order_id": "o001", "price": "100.00"},
        {"order_id": "o001", "price": "50.00"},
        {"order_id": "o002", "price": "999.00"},
    ]

    assert calculate_delivered_gmv(orders, items) == 150.0


def test_empty_data_returns_zero():
    assert calculate_delivered_gmv([], []) == 0.0


def test_canceled_order_is_excluded():
    orders = [{"order_id": "o001", "order_status": "canceled"}]
    items = [{"order_id": "o001", "price": "999.00"}]

    assert calculate_delivered_gmv(orders, items) == 0.0


def test_missing_price_raises_clear_error():
    orders = [{"order_id": "o001", "order_status": "delivered"}]
    items = [{"order_id": "o001"}]

    with pytest.raises(ValueError, match="缺少 price 字段"):
        calculate_delivered_gmv(orders, items)


def test_invalid_price_raises_clear_error():
    orders = [{"order_id": "o001", "order_status": "delivered"}]
    items = [{"order_id": "o001", "price": "无法识别"}]

    with pytest.raises(ValueError, match="price 必须是数字"):
        calculate_delivered_gmv(orders, items)


def test_counts_unique_delivered_orders():
    orders = [
        {"order_id": "o001", "order_status": "delivered"},
        {"order_id": "o001", "order_status": "delivered"},
        {"order_id": "o002", "order_status": "canceled"},
        {"order_id": "o003", "order_status": "delivered"},
    ]

    assert count_delivered_orders(orders) == 2


def test_empty_orders_returns_zero():
    assert count_delivered_orders([]) == 0

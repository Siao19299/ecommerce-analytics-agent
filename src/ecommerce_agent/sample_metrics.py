import csv


def read_csv_rows(path: str) -> list[dict[str, str]]:
    with open(path, mode="r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    return rows


def calculate_delivered_gmv(
    orders: list[dict[str, str]],
    order_items: list[dict[str, str]],
) -> float:
    delivered_order_ids = set()

    for order in orders:
        if order["order_status"] == "delivered":
            delivered_order_ids.add(order["order_id"])

    gmv = 0.0

    for item in order_items:
        if item["order_id"] in delivered_order_ids:
            if "price" not in item:
                raise ValueError(
                    f"订单 {item['order_id']} 的商品明细缺少 price 字段"
                )

            try:
                gmv += float(item["price"])
            except ValueError as error:
                raise ValueError(
                    f"订单 {item['order_id']} 的 price 必须是数字，"
                    f"实际值为 {item['price']!r}"
                ) from error

    return gmv


def count_delivered_orders(
    orders: list[dict[str, str]],
) -> int:
    delivered_order_ids = set()
    for order in orders:
        if order["order_status"] == "delivered":
            delivered_order_ids.add(order["order_id"])
    return len(delivered_order_ids)


if __name__ == "__main__":
    orders = read_csv_rows("data/sample/orders.csv")
    order_items = read_csv_rows("data/sample/order_items.csv")

    result = calculate_delivered_gmv(orders, order_items)
    print("函数计算的 GMV：", result)

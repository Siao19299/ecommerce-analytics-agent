# Day 1：环境、数据关系与第一个指标函数

预计投入：6 小时

## 今日目标

今天只完成一个小闭环：读取两张样例 CSV，理解订单与订单明细的一对多关系，
用 Python 计算已交付订单的 GMV，并用测试验证结果。

## 输入、输出和验收

输入：

- `data/sample/orders.csv`
- `data/sample/order_items.csv`

输出：

- 一个接收“订单列表、明细列表”并返回 GMV 的 Python 函数；
- 至少 3 个测试：正常数据、空数据、取消订单不计入；
- 你能口头解释为什么不能把订单数写成 JOIN 后的行数。

验收标准：

- [x] 能说出两个 CSV 的主键、外键和关系；
- [x] 能独立组合循环、判断和集合，写出去重后的已交付订单计数函数；
- [x] GMV 结果为 `420.00`；
- [x] 已交付订单数为 `3`，不是订单明细行数 `4`；
- [x] 能解释缺失字段和错误数字格式可能在哪里报错；
- [x] pytest 的 7 项测试通过，并更新 `LEARNING_LOG.md`。

## 6 小时安排

1. 45 分钟：安装/确认 Python，创建并激活 `.venv`，安装依赖。
2. 45 分钟：阅读两个样例 CSV，手动画出 `orders 1 → N order_items`。
3. 60 分钟：学习并练习 `list`、`dict`、函数、循环和异常。
4. 90 分钟：亲手实现读取、筛选和 GMV 计算函数。
5. 60 分钟：补 3 个测试，主动制造缺失字段和数字格式错误。
6. 30 分钟：运行测试、查看 Git diff、提交一个小版本。
7. 30 分钟：不看代码复写核心循环，并完成学习日志。

## 第一项手写任务

先不要使用 pandas。用标准库 `csv` 写：

```python
def read_csv_rows(path: str) -> list[dict[str, str]]:
    """读取 CSV，返回由字典组成的列表。"""
```

你需要自己回答：

1. `path` 的输入类型和函数返回类型是什么？
2. CSV 不存在时会出现什么异常？
3. 为什么函数暂时保留所有值为字符串？
4. 如果空文件没有表头，应该返回空列表还是抛错？

完成后再实现：

```python
def calculate_delivered_gmv(
    orders: list[dict[str, str]],
    order_items: list[dict[str, str]],
) -> float:
    """只汇总状态为 delivered 的订单明细 price，不含 freight_value。"""
```

约束：不要在函数中写死订单编号；先用集合保存已交付订单 ID；遇到不存在的必要
字段时抛出清晰异常。Day 1 暂用 `float`，之后在指标语义层讨论金额为什么更适合
`Decimal` 或数据库 `NUMERIC`。

## 环境命令（安装 Python 后执行）

```powershell
cd 01_ecommerce_analytics_agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest
```

环境完成状态：已使用现有 Anaconda 环境中的 Python 3.11.9 创建项目独立
`.venv`，并成功安装依赖。`.venv` 已被 `.gitignore` 排除。

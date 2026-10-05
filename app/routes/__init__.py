"""app/routes/__init__.py — 路由包入口。

刻意**不**在顶层 import 各 routes 子模块：它们会 import app.deps -> app.db，
而 app.main 又要 import 它们，顶层聚合很容易拧成环。需要 router 时直接
`from app.routes.genes import router`。
"""

# -*- coding: utf-8 -*-
"""恶意插件：尝试执行未定义动作（无任何权限映射时执行即拒绝）。"""


def on_message(event, api=None):
    return {"type": "do_evil", "payload": {"group_id": 1, "message": "x"}}

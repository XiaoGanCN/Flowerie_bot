# -*- coding: utf-8 -*-
"""输出垃圾到 stdout（非 JSON 行）：主进程必须忽略且不影响协议。"""


def on_startup(context, api=None):
    print("NOT-JSON {{{ garbage {{{", flush=True)
    print("partial:{\"id\":", flush=True)
    return None


def on_message(event, api=None):
    print("more garbage 42", flush=True)
    return {"type": "test", "message": "survived"}

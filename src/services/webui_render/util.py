"""webui_render 工具：HTML 转义。"""

import html as _html


def _esc(s) -> str:
    return _html.escape("" if s is None else str(s))
def _esc(s) -> str:
    return _html.escape("" if s is None else str(s))


"""webui_render 外观页：主题 / 背景颜色 / 背景图片 / 透明度。"""

from src.services.webui_render.theme import THEME_ORDER, THEMES
from src.services.webui_render.util import _esc


def render_appearance(theme: str, bg_color: str, opacity: int, size: str, position: str,
                      has_bg_image: bool, image_url: str = "", panel_opacity: int = 90,
                      panel_style: str = "clear") -> str:
    cards = []
    for name in THEME_ORDER:
        t = THEMES.get(name)
        if t is None:
            continue
        checked = ' checked' if name == theme else ""
        cards.append(
            f'<label class="theme-card"><input type="radio" name="theme" value="{name}"{checked}>'
            f'<span class="theme-swatch" style="background:{t["bg"]}"></span>'
            f'<span class="theme-meta"><b>{_esc(t["label"])}</b>'
            f'<small>{_esc(t["desc"])}</small></span></label>'
        )
    preview = ""
    if has_bg_image and image_url:
        preview = f'<img class="bg-preview" src="{_esc(image_url)}" alt="当前背景图">'
    opacity = max(0, min(100, int(opacity)))
    size_opts = {
        "cover": "cover（铺满，裁切边缘）",
        "contain": "contain（完整显示，留白）",
    }
    pos_opts = {
        "center": "居中",
        "top": "顶部",
        "bottom": "底部",
        "left": "左侧",
        "right": "右侧",
    }
    size_html = "".join(
        f'<option value="{k}"{" selected" if k == size else ""}>{_esc(v)}</option>' for k, v in size_opts.items()
    )
    pos_html = "".join(
        f'<option value="{k}"{" selected" if k == position else ""}>{_esc(v)}</option>' for k, v in pos_opts.items()
    )
    return (
        '<form method="post" action="/panel/appearance" enctype="multipart/form-data">'
        '<fieldset class="group"><legend>主题</legend>'
        f'<div class="theme-grid">{"".join(cards)}</div>'
        '<p class="hint">主题通过服务端渲染切换（body class），不依赖任何脚本</p>'
        '<div class="row"><label class="row-info"><span class="row-title">主题面板透明度</span>'
        '<span class="row-key">panel_opacity</span></label>'
        '<div class="row-control">'
        f'<div class="range-row"><input type="range" name="panel_opacity" min="0" max="100" value="{max(0,min(100,int(panel_opacity)))}">'
        f'<output>{max(0,min(100,int(panel_opacity)))}%</output></div>'
        '<span class="hint">面板/卡片越透明，越能透出背景图片与主题底色；100% 完全不透明</span>'
        '<div class="row"><label class="row-info"><span class="row-title">卡片效果</span>'
        '<span class="row-key">panel_style</span></label>'
        '<div class="row-control" style="flex-direction:row;gap:18px;flex-wrap:wrap">'
        f'<label class="opt"><input type="radio" name="panel_style" value="clear"{" checked" if panel_style=="clear" else ""}> 纯透明（淡入淡出）</label>'
        f'<label class="opt"><input type="radio" name="panel_style" value="glass"{" checked" if panel_style=="glass" else ""}> 液态玻璃（磨砂）</label>'
        '</div></div>'
        '</div></div></fieldset>'

        '<fieldset class="group"><legend>背景颜色（跟主题绑定）</legend>'
        '<div class="row"><div class="row-control" style="flex-direction:row;align-items:center;gap:14px;flex-wrap:wrap">'
        f'<span class="color-swatch" style="background:{_esc(bg_color)}" title="当前主题背景色"></span>'
        '<input type="text" name="bg_color_input" value="" '
        'placeholder="#RRGGBB 或 253,238,243，留空=用主题默认" class="color-text" '
        'title="留空=用当前主题默认背景；想自定义填入 #RRGGBB 或 R,G,B / rgb(r,g,b)">'
        '<span class="hint">背景颜色已随所选主题自动带好（切换主题即整套换肤，选黑色主题自动变黑）；'
        '留空用主题默认；<code>#RRGGBB</code> / <code>253,238,243</code> 输入只改当前主题</span>'
        '</div></div></fieldset>'

        '<fieldset class="group"><legend>背景图片</legend>'
        '<div class="row"><div class="row-control">'
        '<input type="file" name="bg_image" accept="image/png,image/jpeg,image/webp,image/gif">'
        '<span class="hint">仅允许 PNG / JPEG / WEBP / GIF，最大 5MB；'
        '文件将保存到服务器持久化目录（data/webui/background/），刷新与重启均不会丢失</span>'
        + preview +
        '</div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">图片透明度</span>'
        '<span class="row-key">bg_image_opacity</span></label>'
        '<div class="row-control">'
        f'<div class="range-row"><input type="range" name="bg_image_opacity" min="0" max="100" value="{opacity}">'
        f'<output>{opacity}%</output></div></div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">图片显示方式</span>'
        '<span class="row-key">bg_size / bg_position</span></label>'
        '<div class="row-control" style="flex-direction:row;gap:10px;flex-wrap:wrap">'
        f'<select name="bg_size" style="max-width:220px">{size_html}</select>'
        f'<select name="bg_position" style="max-width:160px">{pos_html}</select></div></div>'
        '</fieldset>'
        '<div class="actions-row"><button type="submit" class="btn">保存外观设置</button></div>'
        '</form>'
        '<div class="actions-row">'
        '<form method="post" action="/panel/appearance/delete-image">'
        '<button type="submit" class="btn danger">删除背景图片</button></form>'
        '<form method="post" action="/panel/appearance/restore">'
        '<button type="submit" class="btn danger">恢复默认主题</button></form>'
        '</div>'
    )


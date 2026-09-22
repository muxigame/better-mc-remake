#!/usr/bin/env python3
"""生成启动器首页的字标（SVG）。

照着上游整合包那几张主视觉做。仔细看会发现一件事：那**不是**像素点阵字。
边缘是干净的直线，K 和 R 的斜腿是真斜线而不是台阶，笔画粗细也不受网格约束。
早先用 8x9 点阵排过一版，出来像终端里的等宽点阵字体，完全不是一回事。

所以这里不铺网格，直接用矩形和多边形描字形：在字宽 78、字高 100、笔画 22 的
坐标系里摆几何块。方正感来自直角和方头收笔，不是来自马赛克。

立体感的三个要点，都是试错出来的，单看代码想不到：

  · 往**正下方**挤，不能斜着。斜向挤出来的是一串对角线阶梯，放大看像锯齿毛边。
  · 描边要包住**整个立体块**，不是只包正面。只描正面的话侧壁会露出没描到的台阶。
  · 正面渐变在高光带下沿留一道**硬转折**。平滑渐变看着像喷漆，参考图那种釉面
    塑料的亮块感全靠这道断层。

改字或改配色跑这个脚本重新生成：

    python scripts/make_wordmark.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "client" / "sidecar" / "web" / "wordmark.svg"

TOP_TEXT = "BETTERMC5"
BOTTOM_TEXT = "REMAKE"
GOLD_INDEX = 8          # 顶行第几个字用金色（0 起）。-1 表示不用。

W = 78.0                # 字宽
H = 100.0               # 字高
S = 22.0                # 笔画粗细
MID = (H - S) / 2       # 中横的上沿

EDGE = "#140924"        # 深色描边
OUTLINE = 7.0           # 描边粗细

# 深色描边外面再包一圈浅色轮廓线。参考图里字标能压在任何背景上都清楚，
# 靠的就是这一圈 —— 没有它，深色描边贴到暗背景上就融掉了，字会失去边界。
KEYLINE = "#dcc7f7"
KEYLINE_W = 1.32        # 相对 OUTLINE 的倍数。给到 2 以上就成了贴纸边框，
                        # 参考图里这圈只有字高的十分之一左右
EXTRUDE = 15.0          # 立体面深度
EXTRUDE_STEPS = 7       # 挤出面用多少层叠出来。每层间距要小于描边宽度，
                        # 否则侧壁会出现断层；给到 16 只是白白多出几百个节点

TOP_GAP = 9.0           # 顶行字距。参考图里字几乎贴着，松一点就散
BOTTOM_GAP = 16.0       # 底行字少，稍松一点撑开宽度
BOTTOM_SCALE = 0.66     # 底行放大一点，两行体量才平衡
LINE_OVERLAP = 6.0      # 底行往上压进顶行的立体面里，两行咬合成一块

# 正面渐变刻意不平滑：高光带下沿那道硬转折就是釉面塑料的质感来源。
TOP_GRADIENT = [("0%", "#c97ff5"), ("32%", "#f0c2ff"),
                ("35%", "#9d35ec"), ("100%", "#5f14b4")]
BOTTOM_GRADIENT = [("0%", "#d8cdec"), ("32%", "#ffffff"),
                   ("35%", "#bcabdd"), ("100%", "#7d68b2")]
GOLD_GRADIENT = [("0%", "#f0c75f"), ("32%", "#fff0bb"),
                 ("35%", "#e5a01c"), ("100%", "#8c5205")]

# 侧面用各自主色压暗的版本。三处共用一个紫的话，白字和金字的立体面会发脏。
SIDE_TOP = "#3f0e80"
SIDE_BOTTOM = "#554878"
SIDE_GOLD = "#6d3b03"


def rect(x, y, w, h):
    return ("r", x, y, w, h)


def poly(*points):
    return ("p", points)


# 每个字形就是一组几何块。坐标系左上角为原点，x 向右 0..W，y 向下 0..H。
GLYPHS = {
    "B": [rect(0, 0, S, H),
          rect(0, 0, W - 10, S),
          rect(0, MID, W - 6, S),
          rect(0, H - S, W - 10, S),
          rect(W - 10 - S, 0, S, MID + S),
          rect(W - 6 - S, MID, S, H - MID)],

    # 方头 A：顶横 + 两竖 + 中横。不做尖顶，参考图里的字也是方的。
    "A": [rect(0, 0, W, S),
          rect(0, 0, S, H),
          rect(W - S, 0, S, H),
          rect(0, MID, W, S)],

    "T": [rect(0, 0, W, S),
          rect((W - S) / 2, 0, S, H)],

    "E": [rect(0, 0, S, H),
          rect(0, 0, W, S),
          rect(0, MID, W - 16, S),
          rect(0, H - S, W, S)],

    # R 的腿是真斜线，这是和点阵版差别最明显的地方
    "R": [rect(0, 0, S, H),
          rect(0, 0, W - 8, S),
          rect(0, MID, W - 8, S),
          rect(W - 8 - S, 0, S, MID + S),
          poly((S + 4, MID + S), (S + 4 + S, MID + S), (W, H), (W - S, H))],

    # M 的中竖只落到 58% 高：比两条对角线更方正，也更好认
    "M": [rect(0, 0, S, H),
          rect(W - S, 0, S, H),
          rect(0, 0, W, S),
          rect((W - S) / 2, 0, S, H * 0.58)],

    "C": [rect(0, 0, S, H),
          rect(0, 0, W, S),
          rect(0, H - S, W, S)],

    "K": [rect(0, 0, S, H),
          poly((W, 0), (W - S, 0), (S, MID + S * 0.5), (S, MID - S * 0.5)),
          poly((S, MID - S * 0.5), (S, MID + S * 0.5), (W - S, H), (W, H))],

    "5": [rect(0, 0, W, S),
          rect(0, 0, S, MID + S),
          rect(0, MID, W - 8, S),
          rect(W - 8 - S, MID, S, H - MID),
          rect(0, H - S, W - 8, S)],
}


def shape_path(shape, dx):
    kind = shape[0]
    if kind == "r":
        _, x, y, w, h = shape
        return "M%g %gh%gv%gh%gz" % (x + dx, y, w, h, -w)
    _, points = shape
    head = "M%g %g" % (points[0][0] + dx, points[0][1])
    rest = "".join("L%g %g" % (px + dx, py) for px, py in points[1:])
    return head + rest + "z"


def text_path(text, gap, skip=()):
    """排一行字，返回 (路径数据, 宽度)。skip 里的下标不画，留给另一条路径。"""
    parts = []
    cursor = 0.0
    for index, char in enumerate(text):
        shapes = GLYPHS.get(char)
        if shapes is None:
            raise KeyError("字形表里没有这个字：%r" % char)
        if index not in skip:
            parts.extend(shape_path(shape, cursor) for shape in shapes)
        cursor += W + gap
    return "".join(parts), cursor - gap


def gradient(ident, stops):
    body = "".join('<stop offset="%s" stop-color="%s"/>' % s for s in stops)
    return ('<linearGradient id="%s" x1="0" y1="0" x2="0" y2="1">%s</linearGradient>'
            % (ident, body))


def stacked(ref, fill_ref, side):
    """把一个字形叠成带立体面的块。顺序即绘制顺序。"""
    layers = []
    # 最外圈浅色轮廓线，画在深色描边之前（被它盖住内侧，只露出外沿一条）
    key = OUTLINE * KEYLINE_W
    for index in range(EXTRUDE_STEPS, -1, -1):
        depth = EXTRUDE * index / EXTRUDE_STEPS
        for dx in (-key, 0, key):
            for dy in (-key, 0, key):
                if dx or dy:
                    layers.append('<use href="#%s" x="%g" y="%g" fill="%s"/>'
                                  % (ref, dx, depth + dy, KEYLINE))
    # 深色描边：整块的轮廓 —— 八个方向 × 每一层挤出深度
    for index in range(EXTRUDE_STEPS, -1, -1):
        depth = EXTRUDE * index / EXTRUDE_STEPS
        for dx in (-OUTLINE, 0, OUTLINE):
            for dy in (-OUTLINE, 0, OUTLINE):
                if dx or dy:
                    layers.append('<use href="#%s" x="%g" y="%g" fill="%s"/>'
                                  % (ref, dx, depth + dy, EDGE))
    # 侧壁：从最深的一层往回铺
    for index in range(EXTRUDE_STEPS, 0, -1):
        layers.append('<use href="#%s" y="%g" fill="%s"/>'
                      % (ref, EXTRUDE * index / EXTRUDE_STEPS, side))
    layers.append('<use href="#%s" fill="%s"/>' % (ref, fill_ref))
    return "".join(layers)


def build():
    gold = {GOLD_INDEX} if 0 <= GOLD_INDEX < len(TOP_TEXT) else set()
    top_main, top_w = text_path(TOP_TEXT, TOP_GAP, skip=gold)
    bottom_main, bottom_w = text_path(BOTTOM_TEXT, BOTTOM_GAP)
    top_gold = ""
    if gold:
        top_gold, _ = text_path(TOP_TEXT, TOP_GAP, skip=set(range(len(TOP_TEXT))) - gold)

    scaled_bottom_w = bottom_w * BOTTOM_SCALE
    width = max(top_w, scaled_bottom_w)
    top_x = (width - top_w) / 2
    bottom_x = (width - scaled_bottom_w) / 2
    bottom_y = H + EXTRUDE - LINE_OVERLAP

    # 只往下挤，底下要多留挤出深度。左右给描边再多留一点：正好等于描边宽度时
    # 最外侧那一笔会贴着画布边，缩放时被抗锯齿啃掉一条。
    pad_x = OUTLINE * KEYLINE_W + 4
    pad_t = OUTLINE * KEYLINE_W
    pad_b = OUTLINE * KEYLINE_W + EXTRUDE
    view_w = width + pad_x * 2
    view_h = bottom_y + H * BOTTOM_SCALE + pad_t + pad_b

    defs = [
        gradient("wmTop", TOP_GRADIENT),
        gradient("wmBottom", BOTTOM_GRADIENT),
        gradient("wmGold", GOLD_GRADIENT),
        '<path id="wmTopMain" d="%s"/>' % top_main,
        '<path id="wmBottomMain" d="%s"/>' % bottom_main,
    ]
    if top_gold:
        defs.append('<path id="wmTopGold" d="%s"/>' % top_gold)

    body = ['<g transform="translate(%g %g)">' % (pad_x + top_x, pad_t),
            stacked("wmTopMain", "url(#wmTop)", SIDE_TOP)]
    if top_gold:
        body.append(stacked("wmTopGold", "url(#wmGold)", SIDE_GOLD))
    body.append("</g>")
    # 底行后画，天然压在顶行的立体面上，两行咬合成一块
    body.append('<g transform="translate(%g %g) scale(%g)">'
                % (pad_x + bottom_x, pad_t + bottom_y, BOTTOM_SCALE))
    body.append(stacked("wmBottomMain", "url(#wmBottom)", SIDE_BOTTOM))
    body.append("</g>")

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %g %g" '
        'role="img" aria-label="%s %s">'
        '<defs>%s</defs>%s</svg>'
        % (view_w, view_h, TOP_TEXT, BOTTOM_TEXT, "".join(defs), "".join(body))
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    try:
        svg = build()
    except KeyError as error:
        print(error, file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(svg, encoding="utf-8")
    print("已写出 %s  %.1f KB" % (args.out, args.out.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())

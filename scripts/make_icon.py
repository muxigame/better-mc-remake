#!/usr/bin/env python3
"""生成启动器的应用图标（.ico）。

和首页字标同一套观感：方头字形、釉面渐变、硬描边、往正下方挤的立体面。
字形直接从 make_wordmark 里取，两边永远不会走样。

两个方案：

    --variant text    在底板上拼 BMC
    --variant block   自己画的方块（不是复制 Minecraft 原版图标）

小尺寸是这件事的难点。任务栏图标常见是 32 甚至 16 像素，三个字母摊下来每个只剩
几个像素，描边一厚就糊成一坨。所以 .ico 里**不同尺寸用不同的画法**：大尺寸拼全，
16/24/32 退回单个 B。Pillow 存 ICO 只会把一张图缩放，做不到这个，所以文件是这里
手工拼的 —— ICO 格式本身很简单，一个头加若干 PNG。

另外描边宽度按画布比例给，不跟着字形缩放走：字标上描边占笔画三分之一很好看，
照搬到图标上会把字母的孔洞直接封死。

    python scripts/make_icon.py --variant text
"""

from __future__ import annotations

import argparse
import io
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_wordmark import (GLYPHS, W, H, EDGE,          # noqa: E402
                           BOTTOM_GRADIENT, SIDE_BOTTOM)

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "client" / "sidecar" / "icon.ico"

SIZES = [16, 24, 32, 48, 64, 128, 256]
SMALL = 24              # 不大于这个尺寸的用简化画法。24 及以下方块里的
                        # 字母已经糊成一团，反而弄脏轮廓，不如只留方块
CANVAS = 1024           # 先按大画布画，再降采样，边缘才干净

FULL_TEXT = "BMC"
SMALL_TEXT = "B"

OUTLINE = 0.021         # 描边宽度占画布的比例
EXTRUDE = 0.062         # 立体面深度占画布的比例
EXTRUDE_STEPS = 20
TEXT_HEIGHT = 0.46      # 拼全时的字高占比
SMALL_HEIGHT = 0.62     # 单字母时的字高占比。小图只有一个字，能撑多大撑多大
LETTER_GAP = 10.0       # 字距，字形坐标系单位

# 底板要够饱和，字才压得住。第一版紫底紫字，全糊在一个色相里，怎么调立体
# 感都提不起来 —— 参考图自己的解法就是 BETTER 紫、MINECRAFT 白，靠明度差。
BACK_TOP = (44, 22, 78)
BACK_BOTTOM = (16, 7, 32)
BORDER = (13, 5, 26)
BORDER_W = 0.030
GRID = (70, 40, 122)            # 方格纹理，给底板一点 Minecraft 的颗粒
GRID_CELLS = 6
GRID_ALPHA = 40

# 图标上的字比字标更亮：字标那套的底色收到灰紫，缩到 32 像素会显脏。
ICON_FACE = [("0%", "#ece5fc"), ("30%", "#ffffff"),
             ("34%", "#ded2f7"), ("100%", "#ab93e0")]

# 方块方案：三个面 + 顶面的几颗亮点，做出 Minecraft 那种颗粒感
BLOCK_TOP = (214, 168, 255)     # 顶面受光
BLOCK_RIGHT = (96, 40, 168)     # 右侧背光
FRONT_GRADIENT = [("0%", "#c97ff5"), ("32%", "#f0c2ff"),
                  ("35%", "#9d35ec"), ("100%", "#6a1cc6")]
BLOCK_EDGE = (16, 7, 30)


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def vertical_gradient(size, stops):
    """按 (位置, 颜色) 生成竖直渐变。位置写成 '32%' 这种，和 SVG 那边一致。"""
    points = [(float(pos.rstrip("%")) / 100.0, hex_rgb(color)) for pos, color in stops]
    strip = Image.new("RGB", (1, size))
    pixels = strip.load()
    for y in range(size):
        t = y / max(1, size - 1)
        lo, hi = points[0], points[-1]
        for index in range(len(points) - 1):
            if points[index][0] <= t <= points[index + 1][0]:
                lo, hi = points[index], points[index + 1]
                break
        span = hi[0] - lo[0]
        k = 0.0 if span <= 0 else (t - lo[0]) / span
        pixels[0, y] = tuple(round(lo[1][c] + (hi[1][c] - lo[1][c]) * k) for c in range(3))
    return strip.resize((size, size))


def text_polygons(text, height_ratio):
    """把一串字排好并居中，返回 Pillow 能画的点列表。

    缩放取高度和宽度两个约束里更小的那个。只按高度算的话，三个字母横着就顶出
    画布了 —— 底板那圈边框会被字压掉。
    """
    advance = W + LETTER_GAP
    raw_w = advance * len(text) - LETTER_GAP
    by_height = CANVAS * height_ratio / H
    by_width = CANVAS * 0.80 / raw_w
    scale = min(by_height, by_width)
    total = raw_w * scale
    ox = (CANVAS - total) / 2
    oy = (CANVAS - H * scale - CANVAS * EXTRUDE) / 2
    out = []
    for index, char in enumerate(text):
        shapes = GLYPHS.get(char)
        if shapes is None:
            raise KeyError("字形表里没有这个字：%r" % char)
        dx = ox + advance * index * scale
        for shape in shapes:
            if shape[0] == "r":
                _, x, y, w, h = shape
                pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
            else:
                pts = list(shape[1])
            out.append([(dx + px * scale, oy + py * scale) for px, py in pts])
    return out


def stamp(target, polygons, dx, dy, fill):
    layer = Image.new("RGBA", target.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for pts in polygons:
        draw.polygon([(x + dx, y + dy) for x, y in pts], fill=fill)
    target.alpha_composite(layer)


def backdrop():
    """饱和紫底 + 方格纹理 + 顶部受光 + 硬边框。"""
    img = vertical_gradient(CANVAS, [("0%", "#%02x%02x%02x" % BACK_TOP),
                                     ("100%", "#%02x%02x%02x" % BACK_BOTTOM)]).convert("RGBA")

    # 方格纹理：单独一层压上去，不然网格线会盖住后面的渐变
    grid = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grid)
    step = CANVAS / GRID_CELLS
    line = max(1, round(CANVAS * 0.004))
    for i in range(1, GRID_CELLS):
        at = round(i * step)
        gdraw.rectangle([at, 0, at + line, CANVAS], fill=GRID + (GRID_ALPHA,))
        gdraw.rectangle([0, at, CANVAS, at + line], fill=GRID + (GRID_ALPHA,))
    img.alpha_composite(grid)

    draw = ImageDraw.Draw(img)
    border = round(CANVAS * BORDER_W)
    # 受光交给底板渐变本身表达。早先在顶部单加过一条亮带，结果读成一根横杠，
    # 像相框上沿而不是光。
    for i in range(border):
        draw.rectangle([i, i, CANVAS - i - 1, CANVAS - i - 1], outline=BORDER)
    return img


def render_text(text, height_ratio):
    img = backdrop()
    polygons = text_polygons(text, height_ratio)
    outline = CANVAS * OUTLINE
    extrude = CANVAS * EXTRUDE
    edge = hex_rgb(EDGE) + (255,)
    side = hex_rgb(SIDE_BOTTOM) + (255,)

    for index in range(EXTRUDE_STEPS, -1, -1):
        depth = extrude * index / EXTRUDE_STEPS
        for dx in (-outline, 0, outline):
            for dy in (-outline, 0, outline):
                if dx or dy:
                    stamp(img, polygons, dx, depth + dy, edge)
    for index in range(EXTRUDE_STEPS, 0, -1):
        stamp(img, polygons, 0, extrude * index / EXTRUDE_STEPS, side)

    mask = Image.new("L", img.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    for pts in polygons:
        mask_draw.polygon(pts, fill=255)
    img.paste(vertical_gradient(CANVAS, ICON_FACE), (0, 0), mask)
    return img


def render_block(letter=None):
    """Minecraft 那种方块：正面一个方，顶面和右面往后斜。

    正面可以刻一个字母 —— 轮廓在 16 像素下靠方块撑着，大尺寸又有身份。
    只画字母的话小尺寸认不出，只画方块又谁都一样，两个叠起来才都有。
    """
    img = backdrop()
    c = CANVAS
    depth = c * 0.155
    left = c * 0.20
    right = c * 0.74
    top = c * 0.30
    bottom = c * 0.84

    face = [(left, top), (right, top), (right, bottom), (left, bottom)]
    top_face = [(left, top), (left + depth, top - depth),
                (right + depth, top - depth), (right, top)]
    side_face = [(right, top), (right + depth, top - depth),
                 (right + depth, bottom - depth), (right, bottom)]

    edge = hex_rgb(EDGE) + (255,)
    outline = max(1, round(c * 0.020))
    draw = ImageDraw.Draw(img)
    # 先把整块的轮廓垫一层，三个面各自描边会在接缝处露出细线
    for poly_pts in (top_face, side_face, face):
        draw.polygon(poly_pts, fill=edge, outline=edge, width=outline * 2)

    inset = outline
    draw.polygon([(x + (inset if x < right else -inset) * 0, y) for x, y in top_face],
                 fill=BLOCK_TOP)
    draw.polygon(side_face, fill=BLOCK_RIGHT)
    # 正面用字标那套釉面渐变，和首页一致
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).polygon(face, fill=255)
    img.paste(vertical_gradient(CANVAS, FRONT_GRADIENT), (0, 0), mask)
    # 三个面之间补回接缝，不然顶面和正面连成一片
    draw = ImageDraw.Draw(img)
    draw.line([(left, top), (right, top)], fill=edge, width=outline)
    draw.line([(right, top), (right, bottom)], fill=edge, width=outline)
    draw.line([(right, top), (right + depth, top - depth)], fill=edge, width=outline)

    if letter:
        shapes = GLYPHS.get(letter)
        if shapes is None:
            raise KeyError("字形表里没有这个字：%r" % letter)
        scale = (bottom - top) * 0.62 / H
        gw, gh = W * scale, H * scale
        ox = (left + right) / 2 - gw / 2
        oy = (top + bottom) / 2 - gh / 2
        polygons = []
        for shape in shapes:
            if shape[0] == "r":
                _, x, y, w, h = shape
                pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
            else:
                pts = list(shape[1])
            polygons.append([(ox + px * scale, oy + py * scale) for px, py in pts])
        letter_outline = c * 0.014
        for dx in (-letter_outline, 0, letter_outline):
            for dy in (-letter_outline, 0, letter_outline):
                if dx or dy:
                    stamp(img, polygons, dx, dy, edge)
        lmask = Image.new("L", img.size, 0)
        ldraw = ImageDraw.Draw(lmask)
        for pts in polygons:
            ldraw.polygon(pts, fill=255)
        img.paste(vertical_gradient(CANVAS, ICON_FACE), (0, 0), lmask)
    return img


# ── 平面方案 ────────────────────────────────────────────────────────────
# Unity 那类图标的做法：一个几何形状，平涂，没有渐变没有高光没有描边，
# 靠轮廓和留白站住。前几版一直在往上堆装饰，越堆越脏 —— 图标只有 16 像素，
# 任何"质感"到那个尺寸都只剩噪点，真正留下来的永远是轮廓。
FLAT_BACK = (38, 18, 72)        # 平涂底，不做渐变
FLAT_MONO = [(255, 255, 255), (206, 186, 244), (150, 122, 214)]
FLAT_BRAND = [(246, 140, 232), (236, 224, 255), (142, 84, 214)]
FLAT_RADIUS = 0.34              # 方块外接半径占画布的比例
FLAT_SEAM = 0.012               # 三个面之间的缝，用底色划开


def render_flat(scheme="mono", carve=None):
    """角朝前的立方体：外轮廓是正六边形，内部三个菱形面靠明度区分。

    这个形状同时是 Minecraft 的方块和最简的三维体，16 像素下轮廓依然成立。

    scheme  mono  三面都是白到淡紫，靠明度分面
            brand 顶面用字标那支品红，把品牌色带进来
    carve   在左面挖一个字母（挖成底色，负形），给图标一点身份。
            小尺寸下这个字会消失，但方块还在 —— 这是可以接受的退化。
    """
    img = Image.new("RGBA", (CANVAS, CANVAS), FLAT_BACK + (255,))
    draw = ImageDraw.Draw(img)

    cx = cy = CANVAS / 2
    r = CANVAS * FLAT_RADIUS
    dx = r * 0.8660254           # cos30，六边形的水平半宽

    top = (cx, cy - r)
    ur = (cx + dx, cy - r / 2)
    lr = (cx + dx, cy + r / 2)
    bottom = (cx, cy + r)
    ll = (cx - dx, cy + r / 2)
    ul = (cx - dx, cy - r / 2)
    mid = (cx, cy)

    faces = FLAT_BRAND if scheme == "brand" else FLAT_MONO
    draw.polygon([top, ur, mid, ul], fill=faces[0])
    draw.polygon([ul, mid, bottom, ll], fill=faces[1])
    draw.polygon([ur, lr, bottom, mid], fill=faces[2])

    if carve:
        shapes = GLYPHS.get(carve)
        if shapes is None:
            raise KeyError("字形表里没有这个字：%r" % carve)
        # 把字形映射到左面这个平行四边形上：以 ul 为原点，两条邻边当基向量
        ox, oy = ul
        ux, uy = mid[0] - ox, mid[1] - oy
        vx, vy = ll[0] - ox, ll[1] - oy
        lo, hi = 0.24, 0.78      # 留边，别顶到面的棱上
        span = hi - lo

        def place(px, py):
            gx = lo + span * (px / W)
            gy = lo + span * (py / H)
            return (ox + ux * gx + vx * gy, oy + uy * gx + vy * gy)

        for shape in shapes:
            if shape[0] == "r":
                _, x, y, w, h = shape
                pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
            else:
                pts = list(shape[1])
            draw.polygon([place(px, py) for px, py in pts], fill=FLAT_BACK + (255,))

    # 三条缝用底色划开。不划的话相邻两面在小尺寸下会糊成一块。
    seam = max(1, round(CANVAS * FLAT_SEAM))
    for a, b in ((mid, top), (mid, ll), (mid, lr)):
        draw.line([a, b], fill=FLAT_BACK + (255,), width=seam)
    return img


def write_ico(path, images):
    """手工拼 ICO：头 + 目录项 + 若干 PNG。Pillow 只会缩放同一张图，做不到分尺寸换画法。"""
    payloads = []
    for size, image in images:
        buffer = io.BytesIO()
        image.resize((size, size), Image.LANCZOS).save(buffer, format="PNG")
        payloads.append((size, buffer.getvalue()))

    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = len(header) + 16 * len(payloads)
    entries, blobs = [], []
    for size, blob in payloads:
        entries.append(struct.pack("<BBBBHHII",
                                   0 if size >= 256 else size,
                                   0 if size >= 256 else size,
                                   0, 0, 1, 32, len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    path.write_bytes(header + b"".join(entries) + b"".join(blobs))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant",
                        choices=("flat", "flat-brand", "flat-b", "text", "block", "blockb"),
                        default="flat")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--png", type=Path, help="顺便导出一张 256 预览")
    args = parser.parse_args()

    try:
        if args.variant.startswith("flat"):
            scheme = "brand" if args.variant == "flat-brand" else "mono"
            carve = SMALL_TEXT if args.variant == "flat-b" else None
            master = render_flat(scheme, carve)
            # 挖了字的话小尺寸退回纯方块，不然那个字只剩几个像素的脏点
            small = render_flat(scheme, None) if carve else master
        elif args.variant == "blockb":
            master = render_block(SMALL_TEXT)
            small = render_block(None)      # 小尺寸只留方块轮廓
        elif args.variant == "block":
            master = render_block()
            small = master
        else:
            master = render_text(FULL_TEXT, TEXT_HEIGHT)
            # 小尺寸退回单字母：三个字母缩到 32 像素以下就糊成一条
            small = render_text(SMALL_TEXT, SMALL_HEIGHT)
    except KeyError as error:
        print(error, file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_ico(args.out, [(s, small if s <= SMALL else master) for s in SIZES])
    print("已写出 %s  %.1f KB  %s" % (args.out, args.out.stat().st_size / 1024,
                                    "/".join(str(s) for s in SIZES)))
    if args.png:
        master.resize((256, 256), Image.LANCZOS).save(args.png)
        print("预览 %s" % args.png)
    return 0


if __name__ == "__main__":
    sys.exit(main())

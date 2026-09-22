#!/usr/bin/env python3
"""把一张游戏截图做成启动器首页主视觉。

换图时跑这个，别手动调 Photoshop——调色参数写在这里才有据可查。

    python scripts/make_hero.py                    # 用仓库里存的那张源图
    python scripts/make_hero.py <截图路径>
    python scripts/make_hero.py <截图路径> --crop 0,26,1088,570

两件事：

1. 裁掉 HUD。截图里的 FPS、小地图、坐标、任务追踪条、血条和物品栏都不能留。
   默认裁切框是给 1280x722 的截图定的，换分辨率必须重新量——用 --probe
   可以打印各行的高饱和像素数，HUD 图标是纯红纯绿纯白，建筑是低饱和粉灰，
   突变的那一行就是边界。

2. 往启动器的配色上靠。界面是冷紫灰（--bg #151419，顶部辉光 #262330）配金色，
   游戏截图往往又亮又艳，直接铺上去会把文字压得看不清，也和界面打架。
   压饱和、压亮度、混一点冷紫，剩下的交给 app.css 里那层渐变。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageEnhance

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "client" / "sidecar" / "web" / "hero.webp"
# 源图跟脚本放一起。临时目录里的那张随时会被清掉，清掉就再也调不动色了。
DEFAULT_SOURCE = Path(__file__).resolve().parent / "hero-source.jpg"

# 给 1280x722 截图量出来的：
#   上 26   —— 避开左上角 FPS 文字
#   下 570  —— 护甲条从 y≈574 开始
#   右 1088 —— 同时避开右上小地图（到 y≈222）和右中任务追踪条（x≥1125）
DEFAULT_CROP = (0, 26, 1088, 570)

SATURATION = 0.62
# 亮度是和 app.css 里那层渐变叠在一起看的，单看这张图会觉得偏亮，属正常。
BRIGHTNESS = 0.78
CONTRAST = 1.05
TINT = (23, 22, 32)     # 介于 --bg #151419 和顶部辉光 #262330 之间
TINT_AMOUNT = 0.16
QUALITY = 84


def probe(image):
    """打印各行的高饱和像素数，用来量 HUD 边界。"""
    pixels = image.load()
    width, height = image.size
    x0, x1 = int(width * 0.33), int(width * 0.67)
    print("y     中央区域高饱和像素数（突变处即 HUD 边界）")
    for y in range(int(height * 0.75), height, 4):
        count = 0
        for x in range(x0, x1, 2):
            r, g, b = pixels[x, y]
            if max(r, g, b) > 110 and (max(r, g, b) - min(r, g, b)) > 70:
                count += 1
        print("%-5d %d" % (y, count))


def grade(image):
    out = ImageEnhance.Color(image).enhance(SATURATION)
    out = ImageEnhance.Brightness(out).enhance(BRIGHTNESS)
    out = ImageEnhance.Contrast(out).enhance(CONTRAST)
    return Image.blend(out, Image.new("RGB", out.size, TINT), TINT_AMOUNT)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, nargs="?", default=DEFAULT_SOURCE,
                        help="游戏截图；留空用 scripts/hero-source.jpg")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--crop", help="左,上,右,下；留空用默认值")
    parser.add_argument("--probe", action="store_true", help="只打印 HUD 边界探测结果")
    args = parser.parse_args()

    if not args.source.is_file():
        print("找不到截图：%s" % args.source, file=sys.stderr)
        return 2

    image = Image.open(args.source).convert("RGB")
    print("源图 %dx%d" % image.size)

    if args.probe:
        probe(image)
        return 0

    box = tuple(int(v) for v in args.crop.split(",")) if args.crop else DEFAULT_CROP
    if box[2] > image.size[0] or box[3] > image.size[1]:
        print("裁切框超出原图（%s vs %s），换分辨率的截图要重新量" % (box, image.size),
              file=sys.stderr)
        return 2

    result = grade(image.crop(box))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.save(args.out, "WEBP", quality=QUALITY, method=6)
    print("已写出 %s  %dx%d  %.1f KB" % (
        args.out, result.size[0], result.size[1], args.out.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())

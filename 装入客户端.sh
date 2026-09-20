#!/bin/bash
# 把 BMC5Pack 的内容装入 PCL 实例
# 用法: bash 装入客户端.sh "实例名"
SRC="/c/Projects/Batter MC Remake/BMC5Pack"
MC="/c/Projects/Batter MC Remake/Batter MC/.minecraft"
NAME="${1:-Batter MC BMC5}"
DST="$MC/versions/$NAME"
[ -d "$DST" ] || { echo "找不到实例: $DST"; echo "现有实例:"; ls "$MC/versions"; exit 1; }
echo "目标: $DST"
for d in mods config resourcepacks shaderpacks; do
  [ -d "$SRC/$d" ] || continue
  rm -rf "$DST/$d"; cp -r "$SRC/$d" "$DST/$d"
  echo "  $d: $(find "$DST/$d" -type f | wc -l) 个文件"
done
cp "$SRC/options.txt" "$DST/options.txt" 2>/dev/null && echo "  options.txt（含 Mandala 启用顺序）"
echo "完成。mods=$(ls "$DST/mods" | wc -l)"

#!/usr/bin/env python3
"""把 Minecraft 本体镜像到我们自己的 OSS。

客户端首装除了整合包那 1.5 G，还要从 Mojang / NeoForge 拉将近 900 M：
3911 个资源对象、144 个运行库、一个客户端 jar。实测这段在国内又慢又不稳，
有些玩家干脆连不上，卡在这一步进不去游戏。

镜像的对象键就是「主机名 + 上游路径」，例如

    https://resources.download.minecraft.net/ab/abcdef...
    -> bmc/mirror/resources.download.minecraft.net/ab/abcdef...

客户端按同样的规则改写地址（见 client/core/DownloadMirror.cs），镜像上没有的
会自动回落上游，所以这个脚本可以分几次跑完，跑到一半也不会让谁装不上。

用法：
    python scripts/mirror_minecraft.py            # 只下载到本地暂存目录
    python scripts/mirror_minecraft.py --upload   # 下载完再传到 OSS
    python scripts/mirror_minecraft.py --check    # 只统计缺什么，不动网络
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# 必须和 client/core/DownloadMirror.cs 里的白名单保持一致，
# 少一个主机就意味着那部分文件白镜像了——客户端根本不会去问。
MIRRORED_HOSTS = {
    "resources.download.minecraft.net",
    "libraries.minecraft.net",
    "piston-data.mojang.com",
    "piston-meta.mojang.com",
    "launchermeta.mojang.com",
    "launcher.mojang.com",
    "maven.neoforged.net",
}

REPO = Path(__file__).resolve().parent.parent
DEFAULT_VERSION_JSON = (
    REPO / "pack" / "source" / "Better MC Remake [FORGE]"
    / "versions" / "BatterMC5Remake" / "BatterMC5Remake.json"
)
DEFAULT_STAGE = REPO / "artifacts" / "mirror"
USER_AGENT = "BatterMC5Remake-Mirror/1.0"


class Item:
    __slots__ = ("url", "key", "sha1", "size", "label")

    def __init__(self, url, sha1="", size=0, label=""):
        self.url = url
        self.sha1 = (sha1 or "").lower()
        self.size = size or 0
        self.label = label or url.rsplit("/", 1)[-1]
        parsed = urlparse(url)
        self.key = parsed.netloc + parsed.path

    @property
    def mirrored(self):
        return urlparse(self.url).netloc in MIRRORED_HOSTS


def fetch(url, timeout=120):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def collect(version_json, stage):
    """把这个版本要用到的上游文件全列出来。"""
    data = json.loads(version_json.read_text(encoding="utf-8"))
    items = []

    client = data.get("downloads", {}).get("client")
    if client and client.get("url"):
        items.append(Item(client["url"], client.get("sha1", ""),
                          client.get("size", 0), "客户端 jar"))

    for lib in data.get("libraries", []):
        art = (lib.get("downloads") or {}).get("artifact")
        if art and art.get("url"):
            items.append(Item(art["url"], art.get("sha1", ""),
                              art.get("size", 0), lib.get("name", "")))

    index = data.get("assetIndex") or {}
    if not index.get("url"):
        print("版本 JSON 里没有 assetIndex，跳过资源对象", file=sys.stderr)
        return items

    items.append(Item(index["url"], index.get("sha1", ""), index.get("size", 0),
                      "资源索引 " + str(index.get("id"))))

    # 资源索引本身要先拿到手，才知道那几千个对象都是谁。
    # 已经下到暂存目录里就直接读，别为了列清单再跑一趟。
    staged_index = stage / Item(index["url"]).key
    if staged_index.is_file():
        index_bytes = staged_index.read_bytes()
    else:
        print("拉取资源索引 ...")
        index_bytes = fetch(index["url"])

    objects = json.loads(index_bytes.decode("utf-8")).get("objects", {})
    for name, meta in objects.items():
        digest = meta["hash"]
        items.append(Item(
            "https://resources.download.minecraft.net/%s/%s" % (digest[:2], digest),
            digest, meta.get("size", 0), name))
    return items


ADOPTIUM_API = ("https://api.adoptium.net/v3/assets/latest/%d/hotspot"
                "?architecture=x64&image_type=%s&os=windows&vendor=eclipse")


def java_item(major, image, stage):
    """把 Adoptium 的 JRE 压缩包也拉进镜像。

    Java 不走主机白名单那套：Adoptium 是带查询串的 API，按路径镜像会撞车。
    它走整合包清单 java 段里的 url 字段，所以这里单独放到 java/ 下，
    再把要填进 packspec.json 的几个值打印出来。
    """
    payload = json.loads(fetch(ADOPTIUM_API % (major, image)).decode("utf-8"))
    if not payload:
        raise RuntimeError("Adoptium 没有返回 Java %d 的 Windows x64 构建" % major)
    package = payload[0]["binary"]["package"]
    url = package["link"]
    name = url.rsplit("/", 1)[-1]
    item = Item(url, "", package.get("size", 0), "Java %d 运行时" % major)
    # 覆盖对象键：Adoptium 的真实下载在 GitHub 上，按主机名铺开没有意义
    item.key = "java/" + name
    item.sha1 = ""
    return item, package.get("checksum", ""), payload[0].get("version", {}).get("semver", "")


def sha1_of(path):
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_good(path, item):
    if not path.is_file():
        return False
    if item.size and path.stat().st_size != item.size:
        return False
    # 大文件逐个重算 sha1 太慢，大小对上就认；小文件顺手校验。
    if item.sha1 and item.size and item.size < 8 * 1024 * 1024:
        return sha1_of(path) == item.sha1
    return True


def download(item, stage):
    target = stage / item.key
    if is_good(target, item):
        return ("skip", item, "")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".part")
    try:
        payload = fetch(item.url)
        if item.size and len(payload) != item.size:
            return ("fail", item, "大小不符：期望 %d 实得 %d" % (item.size, len(payload)))
        if item.sha1:
            actual = hashlib.sha1(payload).hexdigest()
            if actual != item.sha1:
                return ("fail", item, "SHA-1 不符：期望 %s 实得 %s" % (item.sha1, actual))
        temp.write_bytes(payload)
        temp.replace(target)
        return ("done", item, "")
    except Exception as error:      # 逐个文件报告，不因为一个文件中断整批
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
        return ("fail", item, str(error))


def human(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "%.1f %s" % (value, unit)
        value /= 1024
    return str(size)


def upload(stage, bucket, prefix, region):
    ossutil = shutil.which("ossutil") or shutil.which("ossutil.exe")
    if not ossutil:
        print("找不到 ossutil，跳过上传", file=sys.stderr)
        return 2
    target = "oss://%s/%s" % (bucket, prefix.strip("/") + "/")
    command = [ossutil, "cp", "-r", "-u", str(stage) + os.sep, target]
    if region:
        command += ["--region", region]
    print("上传：%s -> %s" % (stage, target))
    return subprocess.call(command)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version-json", type=Path, default=DEFAULT_VERSION_JSON)
    parser.add_argument("--stage", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--check", action="store_true", help="只统计缺什么，不下载")
    parser.add_argument("--upload", action="store_true", help="下载完传到 OSS")
    parser.add_argument("--bucket", default=os.getenv("OSS_BUCKET", "muxigame-prod-static-cn"))
    parser.add_argument("--region", default=os.getenv("OSS_REGION", "cn-hangzhou"))
    parser.add_argument("--prefix", default="bmc/mirror")
    parser.add_argument("--with-java", action="store_true",
                        help="连 Adoptium 的 JRE 压缩包一起镜像")
    args = parser.parse_args()

    if not args.version_json.is_file():
        print("找不到版本 JSON：%s" % args.version_json, file=sys.stderr)
        return 2

    args.stage.mkdir(parents=True, exist_ok=True)
    items = collect(args.version_json, args.stage)

    outside = [i for i in items if not i.mirrored]
    if outside:
        hosts = sorted(set(urlparse(i.url).netloc for i in outside))
        print("这些主机不在客户端白名单里，镜像了也不会被用到：%s" % ", ".join(hosts),
              file=sys.stderr)
    items = [i for i in items if i.mirrored]

    java_meta = None
    if args.with_java:
        try:
            java, java_sha256, java_semver = java_item(21, "jre", args.stage)
            items.append(java)
            java_meta = (java, java_sha256, java_semver)
        except Exception as error:      # Adoptium 挂了不该拖垮整个镜像
            print("查询 Adoptium 失败，跳过 Java：%s" % error, file=sys.stderr)

    missing = [i for i in items if not is_good(args.stage / i.key, i)]
    print("清单 %d 个文件（%s），本地已有 %d 个，还缺 %d 个（%s）" % (
        len(items), human(sum(i.size for i in items)),
        len(items) - len(missing), len(missing), human(sum(i.size for i in missing))))
    if args.check:
        return 0

    failures = []
    done = 0
    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(download, item, args.stage) for item in missing]
            for future in concurrent.futures.as_completed(futures):
                status, item, detail = future.result()
                if status == "fail":
                    failures.append((item, detail))
                    print("  [失败] %s — %s" % (item.label, detail), file=sys.stderr)
                else:
                    done += 1
                    if done % 200 == 0 or done == len(missing):
                        print("  已下载 %d/%d" % (done, len(missing)))

    print("下载完成：成功 %d，失败 %d" % (done, len(failures)))
    if failures:
        print("有文件没下来，镜像不完整；客户端会对这些文件回落上游。", file=sys.stderr)

    if java_meta is not None:
        java, java_sha256, java_semver = java_meta
        staged = args.stage / java.key
        if staged.is_file():
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()
            if java_sha256 and digest != java_sha256.lower():
                print("Java 压缩包 SHA-256 不符，别往 packspec 里填", file=sys.stderr)
            else:
                print("")
                print("Java %s 已镜像。把这几个值填进 pack/packspec.json 的 java 段：" % java_semver)
                print('    "url": "%s/%s",' % (
                    os.getenv("BMC_MIRROR_BASE_URL",
                              "https://%s.oss-%s.aliyuncs.com/%s"
                              % (args.bucket, args.region, args.prefix.strip("/"))),
                    java.key))
                print('    "sha256": "%s",' % digest)
                print('    "size": %d' % staged.stat().st_size)

    if args.upload:
        code = upload(args.stage, args.bucket, args.prefix, args.region)
        if code != 0:
            print("上传失败，退出码 %d" % code, file=sys.stderr)
            return code
        print("镜像已上传到 oss://%s/%s" % (args.bucket, args.prefix))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

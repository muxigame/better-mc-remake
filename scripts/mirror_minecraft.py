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
但"回落上游"对国内玩家等于失败，所以镜像必须补全，跑完用 --verify-remote 核对。

NeoForge 安装器是个例外：它是客户端下载后另起一个 Java 进程跑的，安装器自己
还要联网拉 Mojang 的版本清单、映射表和一批库，这些请求根本不经过我们的镜像。
所以镜像上放的不是官方安装器，而是在同一路径上换成一个"离线版"：
    官方安装器 --generate-fat --fat-offline --fat-include-installer-libs --fat-include-minecraft
再删掉客户端用不到的部分（服务端 jar/映射、原版运行库、客户端 jar——客户端 jar
由启动器事先放进 versions/<mc>/<mc>.jar，见 NeoForgeInstaller.PrepareGameDir）。
离线版会自己进入离线模式，全程不联网，装出来的文件和联网安装逐个 SHA-1 一致。
**这个对象和上游不再逐字节相同**，旁边的 .sha1 也是离线版的。NeoForge 版本一变
就要重跑本脚本，否则新版本在镜像上是 404，国内玩家会卡在"安装 NeoForge"。

用法：
    python scripts/mirror_minecraft.py                  # 只下载到本地暂存目录
    python scripts/mirror_minecraft.py --upload         # 下载完再传到 OSS
    python scripts/mirror_minecraft.py --check          # 只统计缺什么，不动网络
    python scripts/mirror_minecraft.py --verify-remote  # 按客户端的实际地址逐个 HEAD 线上镜像
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
import urllib.error
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


NEOFORGE_INSTALLER = ("https://maven.neoforged.net/releases/net/neoforged/neoforge/"
                      "%s/neoforge-%s-installer.jar")


def neoforge_version(version_json):
    """版本 JSON 里 --fml.neoForgeVersion 后面那个值，和客户端 VersionJson 同一个来源。"""
    data = json.loads(version_json.read_text(encoding="utf-8"))
    args = [a for a in (data.get("arguments") or {}).get("game", []) if isinstance(a, str)]
    for flag in ("--fml.neoForgeVersion", "--fml.forgeVersion"):
        if flag in args and args.index(flag) + 1 < len(args):
            return args[args.index(flag) + 1]
    return None


def _maven_paths(libraries):
    out = set()
    for lib in libraries:
        downloads = lib.get("downloads") or {}
        artifact = downloads.get("artifact")
        if artifact and artifact.get("path"):
            out.add("maven/" + artifact["path"])
        for classifier in (downloads.get("classifiers") or {}).values():
            if classifier.get("path"):
                out.add("maven/" + classifier["path"])
    return out


def is_offline_installer(path):
    """--fat-include-minecraft 才会带 maven/minecraft/<mc>.json，官方安装器没有。"""
    import zipfile
    try:
        with zipfile.ZipFile(path) as jar:
            return any(n.startswith("maven/minecraft/") and n.endswith(".json")
                       for n in jar.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def build_offline_installer(nf, stage, java):
    """生成离线版 NeoForge 安装器，放到暂存目录里官方安装器的位置上。"""
    import tempfile
    import zipfile

    item = Item(NEOFORGE_INSTALLER % (nf, nf))
    target = stage / item.key
    if is_offline_installer(target) and target.with_name(target.name + ".sha1").is_file():
        return target, "skip"

    with tempfile.TemporaryDirectory(prefix="neoforge-offline-") as work:
        work = Path(work)
        official = work / "installer.jar"
        official.write_bytes(fetch(item.url, timeout=300))
        expected = fetch(item.url + ".sha1").decode("ascii", "replace").strip()[:40].lower()
        if sha1_of(official) != expected:
            raise RuntimeError("官方安装器 SHA-1 不符：期望 %s" % expected)

        # 生成胖安装器要联网拉 Mojang 和 NeoForge 的东西，得在能直连的机器上跑
        fat = work / "fat.jar"
        code = subprocess.call(
            [java, "-jar", str(official), "--generate-fat", str(fat), "--fat-offline",
             "--fat-include-installer-libs", "--fat-include-minecraft"],
            cwd=str(work), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if code != 0 or not fat.is_file():
            # 临时目录马上就删了，日志挪到暂存目录外面（放里面会被一起传上 OSS）
            log = stage.parent / ("neoforge-%s-generate-fat.log" % nf)
            try:
                shutil.copyfile(work / "installer.jar.log", log)
            except OSError:
                pass
            raise RuntimeError("生成胖安装器失败，退出码 %d，日志：%s" % (code, log))

        with zipfile.ZipFile(fat) as src:
            names = set(src.namelist())
            mc_json = next(n for n in names
                           if n.startswith("maven/minecraft/") and n.count("/") == 2 and n.endswith(".json"))
            mc = mc_json[len("maven/minecraft/"):-len(".json")]
            vanilla = _maven_paths(json.loads(src.read(mc_json)).get("libraries", []))
            needed = (_maven_paths(json.loads(src.read("install_profile.json")).get("libraries", []))
                      | _maven_paths(json.loads(src.read("version.json")).get("libraries", [])))
            # 原版运行库由启动器自己下；安装器处理器要用的那几个（和原版重叠的）必须留着，
            # 删多了离线安装会在"找不到 failureaccess"之类的地方失败。
            drop = (vanilla - needed) | {
                "maven/minecraft/%s/server.jar" % mc,
                "maven/minecraft/%s/server_mappings.txt" % mc,
                "maven/minecraft/%s/client.jar" % mc,
                "data/server.lzma",
            }
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + ".part")
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as out:
                for info in src.infolist():
                    if info.filename not in drop:
                        out.writestr(info, src.read(info.filename))
        temp.replace(target)

    target.with_name(target.name + ".sha1").write_text(sha1_of(target), encoding="ascii")
    return target, "done"


def find_java(explicit):
    if explicit:
        return explicit
    home = os.getenv("JAVA_HOME")
    if home:
        candidate = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.is_file():
            return str(candidate)
    return shutil.which("java")


def client_url(base, key):
    """和 DownloadMirror.Rewrite 拼出来的地址逐字一致：OSS 把路径里的 + 当空格，要转义。"""
    return "%s/%s" % (base.rstrip("/"), key.replace("+", "%2B"))


def verify_remote(items, base, workers):
    """按客户端真正会请求的地址 HEAD 一遍。

    以前只在本地核对过暂存目录，线上用 urllib 编码过的地址 HEAD 也全是 200，
    结果客户端用字面 + 请求 sponge-mixin 拿到 404、回落上游，国内直接装不上。
    所以这里的地址必须和客户端逐字一致；带 + 的键再按老客户端（≤1.1.33，不转义）
    的地址查一遍。
    """
    def head(url):
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, int(response.headers.get("Content-Length") or -1)
        except urllib.error.HTTPError as error:
            return error.code, -1
        except Exception:
            return 0, -1

    checks = []
    for item in items:
        checks.append((item, client_url(base, item.key)))
        if "+" in item.key:
            checks.append((item, "%s/%s" % (base.rstrip("/"), item.key)))
    bad = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for (item, url), (status, length) in zip(checks, pool.map(lambda c: head(c[1]), checks)):
            if status != 200 or (item.size and length != item.size):
                bad.append((url, status, length, item.size))
    for url, status, length, size in bad[:30]:
        print("  [不可用] %s — HTTP %s，大小 %s（期望 %s）" % (url, status, length, size), file=sys.stderr)
    print("线上核对：%d 个地址，%d 个不可用" % (len(checks), len(bad)))
    return 1 if bad else 0


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
    code = subprocess.call(command)
    if code != 0:
        return code
    # 客户端 ≤1.1.33 请求带 + 的对象时不转义，OSS 按空格解码。给它们在空格键名下
    # 留一份副本，否则 sponge-mixin 这种构件在老客户端上永远是 404、回落上游。
    for path in sorted(stage.rglob("*")):
        relative = path.relative_to(stage).as_posix()
        if "+" not in relative or not path.is_file() or path.name.endswith(".part"):
            continue
        legacy = "oss://%s/%s/%s" % (bucket, prefix.strip("/"), relative.replace("+", " "))
        command = [ossutil, "cp", "-f", str(path), legacy]
        if region:
            command += ["--region", region]
        code = subprocess.call(command)
        if code != 0:
            return code
    return 0


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
    parser.add_argument("--java", help="生成离线 NeoForge 安装器用的 java，默认 JAVA_HOME 或 PATH 里的")
    parser.add_argument("--skip-neoforge", action="store_true", help="不生成离线 NeoForge 安装器")
    parser.add_argument("--verify-remote", action="store_true",
                        help="按客户端的实际地址逐个 HEAD 线上镜像；和 --upload 一起用时在上传后核对")
    args = parser.parse_args()
    mirror_base = os.getenv("BMC_MIRROR_BASE_URL", "https://%s.oss-%s.aliyuncs.com/%s"
                            % (args.bucket, args.region, args.prefix.strip("/")))

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

    nf = None if args.skip_neoforge else neoforge_version(args.version_json)
    if nf:
        installer = Item(NEOFORGE_INSTALLER % (nf, nf))
        remote_items = items + [installer, Item(installer.url + ".sha1")]
    else:
        remote_items = items

    if args.verify_remote and not args.upload:
        return verify_remote(remote_items, mirror_base, args.workers)

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
    if nf:
        staged = args.stage / Item(NEOFORGE_INSTALLER % (nf, nf)).key
        print("离线 NeoForge %s 安装器：%s" % (nf, "已生成" if is_offline_installer(staged) else "还没生成"))
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

    if nf:
        java_exe = find_java(args.java)
        if not java_exe:
            failures.append((Item(NEOFORGE_INSTALLER % (nf, nf)), "找不到 java"))
            print("  [失败] 离线 NeoForge 安装器：找不到 java，用 --java 指定", file=sys.stderr)
        else:
            try:
                path, status = build_offline_installer(nf, args.stage, java_exe)
                print("离线 NeoForge %s 安装器：%s（%s）" % (
                    nf, "已是最新" if status == "skip" else "已生成", human(path.stat().st_size)))
            except Exception as error:      # 没有它国内装不上 NeoForge，必须算失败
                failures.append((Item(NEOFORGE_INSTALLER % (nf, nf)), str(error)))
                print("  [失败] 离线 NeoForge 安装器 — %s" % error, file=sys.stderr)

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
                print('    "url": "%s/%s",' % (mirror_base, java.key))
                print('    "sha256": "%s",' % digest)
                print('    "size": %d' % staged.stat().st_size)

    if args.upload:
        code = upload(args.stage, args.bucket, args.prefix, args.region)
        if code != 0:
            print("上传失败，退出码 %d" % code, file=sys.stderr)
            return code
        print("镜像已上传到 oss://%s/%s" % (args.bucket, args.prefix))
        if args.verify_remote:
            code = verify_remote(remote_items, mirror_base, args.workers)
            if code != 0:
                return code

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .manifest_builder import build_manifest, read_json, save_json
from .auth import AuthStore


SERVER_ROOT = Path(__file__).resolve().parent.parent


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{value:.0f} B"
        value /= 1024
    return str(size)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batter MC Remake 官网与发布工具")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="生成整合包清单和发布目录")
    build.add_argument("--spec", type=Path, default=SERVER_ROOT / "packspec.json")
    build.add_argument("--publish", type=Path)
    build.add_argument("--out", type=Path)
    build.add_argument("--link", action="store_true")
    serve = commands.add_parser("serve", help="启动官网和 API")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8099)
    serve.add_argument("--reload", action="store_true")
    promote = commands.add_parser("promote-admin", help="把已验证账号提升为站点管理员")
    promote.add_argument("email")
    promote.add_argument("--database", type=Path)
    args = parser.parse_args()

    if args.command == "serve":
        uvicorn.run("app.main:app", app_dir=str(SERVER_ROOT), host=args.host, port=args.port, reload=args.reload)
        return

    if args.command == "promote-admin":
        database = args.database or Path(os.getenv("BMC_DATABASE_PATH", "server/data/battermc.db"))
        if not database.is_absolute():
            database = SERVER_ROOT.parent / database
        account = AuthStore(database).promote_admin(args.email)
        if account is None:
            raise SystemExit("没有找到这个账号，请先完成注册")
        print(f"已将 {account.email} 提升为管理员")
        return

    spec_path = args.spec.resolve()
    publish = args.publish.resolve() if args.publish else None
    output = (args.out or ((publish or Path.cwd()) / "manifest.json")).resolve()
    manifest, stats = build_manifest(read_json(spec_path), spec_path, publish, args.link)
    save_json(output, manifest)
    print(f'\n整合包 {manifest["pack"]["name"]} {manifest["pack"]["version"]}')
    print(f'  文件  {stats["files"]}（{human_size(stats["bytes"])}）')
    print(f'  哈希  实算 {stats["hashed"]}，命中缓存 {stats["cached"]}')
    print(f'  用时  {stats["seconds"]:.1f} 秒')
    print(f"  清单  {output}")


if __name__ == "__main__":
    main()

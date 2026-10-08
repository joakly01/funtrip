import argparse
from pathlib import Path
import os
import shutil
import sqlite3


def main():
    parser = argparse.ArgumentParser(description="个人交易伙伴网页应用")
    parser.add_argument("command", nargs="?", choices=["serve", "import-data", "backup"], default="serve")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    from .config import Config, ROOT
    from dotenv import dotenv_values
    env_path = args.env_file or ROOT / "config/app.env"
    if env_path.is_file():
        # 只加载个人应用配置；不会读取原引擎.env或改动系统环境项。
        for key, value in dotenv_values(env_path, interpolate=False).items():
            if key.startswith("TRADING_") and value is not None:
                os.environ.setdefault(key, value)
    config = Config.from_env()
    if args.command == "backup":
        from datetime import datetime
        from zoneinfo import ZoneInfo
        if not any(p.is_file() for p in (config.data_dir / "knowledge.sqlite", config.runtime_dir / "runtime.sqlite")):
            parser.error("没有可备份的数据库，请先启动应用或导入资料。")
        folder = config.runtime_dir.parent / "backups" / datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
        folder.mkdir(parents=True)
        for source, name in [(config.data_dir / "knowledge.sqlite", "knowledge.sqlite"),
                             (config.runtime_dir / "library.sqlite", "library.sqlite"),
                             (config.runtime_dir / "chart-annotations.sqlite", "chart-annotations.sqlite"),
                             (config.runtime_dir / "runtime.sqlite", "runtime.sqlite")]:
            if source.is_file():
                with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(folder / name) as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("备份完整性检查失败")
        if (config.runtime_dir / "settings.json").is_file():
            shutil.copy2(config.runtime_dir / "settings.json", folder / "settings.json")
        if (config.data_dir / "exports").is_dir():
            shutil.copytree(config.data_dir / "exports", folder / "exports")
        if (config.runtime_dir / "attachments").is_dir():
            shutil.copytree(config.runtime_dir / "attachments", folder / "attachments")
        print("备份完成：" + str(folder))
        return
    if args.command == "import-data":
        if args.source is None:
            parser.error("import-data需要--source指定原交易资料目录")
        source = (args.source / "knowledge.sqlite").resolve()
        destination = config.data_dir / "knowledge.sqlite"
        if destination.exists():
            parser.error("目标数据库已存在，停止导入以保留现有资料。")
        if not source.is_file():
            parser.error("原目录没有knowledge.sqlite。")
        config.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(destination) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("导入数据完整性检查失败")
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        exports = args.source / "exports"
        if exports.is_dir():
            shutil.copytree(exports, config.data_dir / "exports", dirs_exist_ok=True)
        print("交易资料已复制到云端数据目录；原数据库未修改。")
        return
    if not config.password:
        parser.error("请先设置TRADING_PASSWORD个人登录密码。")
    import uvicorn
    from .app import create_app
    uvicorn.run(create_app(config), host=args.host or os.getenv("TRADING_HOST", "127.0.0.1"),
                port=args.port or int(os.getenv("TRADING_PORT", "8097")))


if __name__ == "__main__":
    main()

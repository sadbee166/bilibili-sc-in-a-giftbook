"""Run the unified GiftBook process with ``python -m giftbook_bridge``."""

import asyncio
import argparse
from pathlib import Path
from typing import Optional, Sequence

from .processor import GiftBookProcessor
from .web_server import BridgeConfig


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="启动 GiftBook 实时醒目留言桥接")
    parser.add_argument(
        "--config",
        type=Path,
        help="启动前读取的 JSON 配置文件；环境变量可以覆盖其中的值",
    )
    args = parser.parse_args(argv)
    config = BridgeConfig.from_env(config_path=args.config)
    try:
        asyncio.run(GiftBookProcessor(config).run_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

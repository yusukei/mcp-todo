"""ブックマーク削除時のリソースクリーンアップ"""

import logging
import shutil
from pathlib import Path

from ..core.config import settings
from .bookmark_search import deindex_bookmark

logger = logging.getLogger(__name__)


async def cleanup_bookmark_assets(bookmark_id: str) -> None:
    """ブックマーク関連のアセットと検索インデックスを削除する。

    - BOOKMARK_ASSETS_DIR/<bookmark_id>/ ディレクトリを丸ごと削除
    - 検索インデックスからエントリを除去
    """
    asset_dir = Path(settings.BOOKMARK_ASSETS_DIR) / str(bookmark_id)

    # アセットディレクトリ削除
    if asset_dir.is_dir():
        try:
            shutil.rmtree(asset_dir)
            logger.info("Removed asset directory for bookmark %s", bookmark_id)
        except OSError as e:
            logger.warning(
                "Failed to remove asset directory for bookmark %s: %s",
                bookmark_id, e,
            )

    # 検索インデックスから除去
    await deindex_bookmark(bookmark_id)

# Claude Todo

Claude Code向けTodo MCPサーバ。

## 起動方法

```bash
cp .env.example .env
# .envを編集してSECRET_KEY, MCP_INTERNAL_SECRETなどを設定

docker compose up -d
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000/docs
- MCP Server: http://localhost:8001

## Claude Code設定

`~/.claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "todo": {
      "url": "http://localhost:8001/mcp",
      "headers": {
        "X-API-Key": "mtodo_xxxx"
      }
    }
  }
}
```

## 初期管理者作成

```bash
docker compose exec backend uv run python -c "
import asyncio
from app.core.database import connect
from app.models import User
from app.models.user import AuthType
from app.core.security import hash_password

async def main():
    await connect()
    user = User(
        email='admin@example.com',
        name='Admin',
        auth_type=AuthType.admin,
        password_hash=hash_password('changeme'),
        is_admin=True,
    )
    await user.insert()
    print('Admin created:', user.email)

asyncio.run(main())
"
```

## フェーズ計画

- **Phase 1（現在）**: コアCRUD + MCP tools + React基本UI
- **Phase 2**: Google OAuth + SSE + カンバン/リスト + コメント + タグ
- **Phase 3**: 管理者UI（ユーザ管理・MCPキー管理）

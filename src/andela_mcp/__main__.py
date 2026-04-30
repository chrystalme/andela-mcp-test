from __future__ import annotations

import uvicorn

from andela_mcp.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "andela_mcp.server:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()

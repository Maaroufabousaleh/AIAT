"""Reviewed local workspace MCP server with read-only tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .tools.adapters import RepoReadTool, RepoSearchTool

mcp = FastMCP("AIAT reviewed workspace", json_response=True)


@mcp.tool()
async def workspace_read(path: str, project_id: str = "") -> dict:
    """Read one UTF-8 file inside the bounded AIAT workspace."""
    return await RepoReadTool().execute(path=path, project_id=project_id)


@mcp.tool()
async def workspace_search(
    query: str,
    project_id: str = "",
    path: str = ".",
    max_results: int = 50,
) -> dict:
    """Search literal text inside the bounded AIAT workspace."""
    return await RepoSearchTool().execute(
        query=query,
        project_id=project_id,
        path=path,
        max_results=max_results,
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

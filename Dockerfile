# Stdio Model Context Protocol (MCP) Server Dockerfile for Glama / Docker MCP Toolkit introspection.
# (For the VPS HTTP gateway & reverse proxy container, see deploy/Dockerfile.)
FROM python:3.12-slim

WORKDIR /app
COPY packages/cli /app/packages/cli

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/app/packages/cli/mcp_server.py"]

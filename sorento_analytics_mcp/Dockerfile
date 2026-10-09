# Build from the REPO ROOT (the image needs sorento_crm_mcp as a library):
#   docker build -f sorento_analytics_mcp/Dockerfile -t sorento-analytics-mcp .
FROM python:3.12-slim

WORKDIR /app
COPY sorento_crm_mcp/pyproject.toml sorento_crm_mcp/README.md ./sorento_crm_mcp/
COPY sorento_crm_mcp/sorento_crm_mcp ./sorento_crm_mcp/sorento_crm_mcp
COPY sorento_analytics_mcp/pyproject.toml sorento_analytics_mcp/README.md ./sorento_analytics_mcp/
COPY sorento_analytics_mcp/sorento_analytics_mcp ./sorento_analytics_mcp/sorento_analytics_mcp
RUN pip install --no-cache-dir ./sorento_crm_mcp ./sorento_analytics_mcp

# No EXTERNAL_API_KEY and no database credentials, on purpose.
ENV ANALYTICS_MCP_HOST=0.0.0.0
ENV ANALYTICS_MCP_PORT=8780

EXPOSE 8780

CMD ["python", "-m", "sorento_analytics_mcp"]

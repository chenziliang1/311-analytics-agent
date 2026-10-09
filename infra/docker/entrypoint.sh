#!/bin/sh
# Pick what this container serves. AgentCore Runtime has no per-runtime
# command override, so the role comes from an environment variable instead.
set -e

case "${AGENT_APP_ROLE:-agent}" in
  agent)
    exec agent-app agentcore-app --host 0.0.0.0 --port 8080
    ;;
  mcp)
    exec agent-app mcp-server --transport streamable-http --host 0.0.0.0 --port 8000
    ;;
  *)
    echo "unknown AGENT_APP_ROLE=${AGENT_APP_ROLE}; expected agent or mcp" >&2
    exit 64
    ;;
esac

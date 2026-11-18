#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${USAGE_DASHBOARD_PORT:-8797}"
SKIP_PLUGIN="${SKIP_PLUGIN:-0}"

PY_BIN="$(command -v python3 || command -v python || true)"
if [[ -z "${PY_BIN}" ]]; then
  echo "未找到 python3，请先安装 Python 3.10+。" >&2
  exit 1
fi

echo "==> [1/3] 使用 Python: ${PY_BIN}"

if [[ "${SKIP_PLUGIN}" != "1" ]]; then
  echo "==> [2/3] 安装 Antigravity 全局模型锁插件 (carbon-high-lock)..."
  PLUGIN_DST="${HOME}/.gemini/config/plugins/carbon-high-lock"
  mkdir -p "${PLUGIN_DST}"
  cp -R "${REPO_ROOT}/plugins/carbon-high-lock/." "${PLUGIN_DST}/"
  if ! command -v python >/dev/null 2>&1; then
    # macOS / Linux 下若仅有 python3，自动将 hooks.json 中的 python 替换为 python3
    sed -i.bak 's/"python -X utf8/"python3 -X utf8/g' "${PLUGIN_DST}/hooks.json" && rm -f "${PLUGIN_DST}/hooks.json.bak"
  fi
  echo "    已部署到: ${PLUGIN_DST}"
fi

echo "==> [3/3] 生成初始统计并启动后台监控服务 (端口 ${PORT})..."
"${PY_BIN}" -X utf8 "${REPO_ROOT}/agg.py" --no-git
pkill -f "serve.py" 2>/dev/null || true
nohup env USAGE_DASHBOARD_PORT="${PORT}" "${PY_BIN}" -X utf8 "${REPO_ROOT}/serve.py" >/dev/null 2>&1 &

echo "✅ 部署完成！请打开浏览器访问: http://127.0.0.1:${PORT}/"

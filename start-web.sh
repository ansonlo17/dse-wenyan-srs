#!/usr/bin/env bash
# 本機以「網站」方式啟動（區網手機可連）
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -r requirements.txt
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# 顯示本機區網 IP（方便手機連）
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "你的電腦IP")
echo ""
echo " monologue 文言精華網站已啟動"
echo "  本機：  http://localhost:8501"
echo "  手機：  http://${IP}:8501   （需同一 Wi‑Fi）"
echo "  結束：  Ctrl+C"
echo ""

exec streamlit run app.py --server.address 0.0.0.0 --server.port 8501

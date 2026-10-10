#!/usr/bin/env bash
# Offline regression: temporary data and native substitutes only; no credentials, no Coding tasks.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
fi
"$PY" -m unittest test_workflow test_workflow_workspace test_workflow_library test_pipeline \
  test_reload_gateway test_workflow_review test_workflow_sessions -q
"$PY" build_workflow_ui.py --check
node --test tests/workflow-editor-store.test.js
for script in ui/workflow.js ui/workflow-progress.js ui/workflow-view.js ui/crew-entry.js ui/live.js octopus/ui/*.js; do
  node --check "$script"
done
"$PY" -m unittest discover -s tests -p 'test_*.py' -q
echo "regress: OK"

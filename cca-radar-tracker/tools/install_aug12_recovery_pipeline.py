#!/usr/bin/env python3
"""Install one-time Zero G Aug 12 history recovery into the normal radar workflow."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT.parent / ".github" / "workflows" / "update-radar.yml"
text = WORKFLOW.read_text(encoding="utf-8")
marker = "Recover field-observed Zero G Aug 12 event"
if marker in text:
    print("Aug 12 recovery pipeline already installed")
    raise SystemExit(0)

needle = '          python cca-radar-tracker/tools/select_status.py "${args[@]}"\n\n      - name: Analyze and reconcile radar frames\n'
replacement = '''          python cca-radar-tracker/tools/select_status.py "${args[@]}"

      - name: Recover field-observed Zero G Aug 12 event
        run: |
          set -euo pipefail
          if python - <<'PY'
          import json
          from pathlib import Path
          status = json.loads(Path("cca-radar-tracker/docs/data/status.json").read_text())
          recovered = status.get("field_history_recovery", {}).get("zerog_aug12_2026", {})
          raise SystemExit(0 if recovered.get("ok") else 1)
          PY
          then
            echo "Zero G Aug 12 field event already recovered."
          else
            python cca-radar-tracker/tools/backfill_zerog_aug12.py \
              --status cca-radar-tracker/docs/data/status.json
          fi

      - name: Analyze and reconcile radar frames
'''
if needle not in text:
    raise SystemExit("Restore-state workflow marker not found")
WORKFLOW.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
print("Installed one-time Aug 12 recovery into update-radar workflow")

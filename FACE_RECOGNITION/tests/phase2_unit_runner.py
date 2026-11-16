"""Run final business and regression tests; persist counts, never test data."""
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
modules=['tests.test_attendance','tests.test_management','tests.test_camera_api','tests.test_student_import','tests.test_camera_preview']
suite=unittest.defaultTestLoader.loadTestsFromNames(modules)
result=unittest.TextTestRunner(verbosity=1).run(suite)
report=dict(tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),status='passed' if result.wasSuccessful() else 'failed',modules=modules)
output=ROOT/'outputs/phase2';output.mkdir(exist_ok=True)
(output/'unit_tests.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
raise SystemExit(0 if result.wasSuccessful() else 1)

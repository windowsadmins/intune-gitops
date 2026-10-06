"""Put the engine, enrollment and webhook import roots on sys.path."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
FIXTURES = TESTS / "fixtures"

for path in (ROOT / "engine", ROOT / "enrollment",
             ROOT / "enrollment" / "triggers" / "generic-webhook", TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

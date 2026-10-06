"""A gitops repo's own consumers/ must load beside the shared ones."""
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

ENROLLMENT = pathlib.Path(__file__).resolve().parents[1] / "enrollment"


class ConsumerNamespaceTest(unittest.TestCase):
    def test_caller_consumer_loads_beside_shared(self):
        with tempfile.TemporaryDirectory() as caller:
            pkg = pathlib.Path(caller) / "consumers"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("")
            (pkg / "demo.py").write_text("def converge(csv_text):\n    return 0\n")
            code = textwrap.dedent(
                """
                import consumers.demo, consumers.intune
                assert consumers.demo.converge('') == 0
                """
            )
            subprocess.run(
                [sys.executable, "-c", code],
                check=True,
                env={"PYTHONPATH": f"{ENROLLMENT}:{caller}", "PATH": ""},
            )


if __name__ == "__main__":
    unittest.main()

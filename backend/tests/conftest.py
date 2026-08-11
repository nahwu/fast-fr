from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="fastfr-tests-"))
os.environ["FASTFR_DATA_DIR"] = str(TEST_DATA_DIR)


def pytest_sessionfinish(session, exitstatus):  # type: ignore[no-untyped-def]
    del session, exitstatus
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)

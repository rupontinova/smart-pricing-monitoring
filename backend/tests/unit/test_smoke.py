import re

import spm


def test_version_is_semver_string_when_imported() -> None:
    assert isinstance(spm.__version__, str)
    assert re.fullmatch(r"\d+\.\d+\.\d+", spm.__version__)

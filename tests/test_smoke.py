import campusnet


def test_package_imports_and_has_version() -> None:
    assert isinstance(campusnet.__version__, str)

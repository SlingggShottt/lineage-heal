import lineageheal


def test_package_exposes_a_version() -> None:
    assert lineageheal.__version__.count(".") == 2

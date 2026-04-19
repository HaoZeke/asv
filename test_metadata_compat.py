from pathlib import Path
import importlib.util


ROOT = Path(__file__).resolve().parent


def test_metadata_helper_exists_and_loads():
    helper_path = ROOT / "asv" / "_metadata.py"
    assert helper_path.exists()

    spec = importlib.util.spec_from_file_location("asv__metadata", helper_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert callable(module.get_version)


def test_modules_use_metadata_helper():
    files = [
        ROOT / "asv" / "__init__.py",
        ROOT / "asv" / "commands" / "common_args.py",
        ROOT / "asv" / "commands" / "publish.py",
    ]

    for path in files:
        text = path.read_text()
        assert "importlib_metadata" not in text
        assert "_metadata import get_version" in text


def test_pyproject_declares_python_315_support():
    text = (ROOT / "pyproject.toml").read_text()
    assert 'Programming Language :: Python :: 3.15' in text


def test_ci_includes_python_315_dev():
    for relpath in [
        ".github/workflows/ci.yml",
        ".github/workflows/triggered.yml",
    ]:
        text = (ROOT / relpath).read_text()
        assert '"3.15-dev"' in text

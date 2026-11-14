# Licensed under a 3-clause BSD style license - see LICENSE.rst

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from asv import config, util
from asv.commands.compare import COMPARE_SCHEMA_VERSION, Compare
from asv.commands.show import Show
from asv.results import Results

from . import tools

GOLDEN = Path(__file__).resolve().parent / "golden" / "latex"

PARAMS = {
    "machine": "cheetah",
    "arch": "x86_64",
    "cpu": "cpu",
    "os": "Linux",
    "ram": "1G",
}


def _save(result):
    result.save(result._filename and os.path.join(os.getcwd(), "results"))


def _results(commit, env_name="py3"):
    return Results(PARAMS, {}, commit, 1, "3.12", env_name, {})


def _put(res, name, values, params, unit_params=None):
    if not isinstance(values, list):
        values = [values]
    res._results[name] = None if all(value is None for value in values) else list(values)
    res._stats[name] = None
    res._samples[name] = None
    res._benchmark_params[name] = params
    res._benchmark_version[name] = "1"


def _bench(name, unit="seconds", params=None, param_names=None):
    return {
        "name": name,
        "params": params or [],
        "param_names": param_names or [],
        "unit": unit,
        "version": "1",
    }


def _conf(tmpdir, benches):
    results_dir = os.path.join(str(tmpdir), "results")
    machine_dir = os.path.join(results_dir, "cheetah")
    os.makedirs(machine_dir)
    util.write_json(os.path.join(machine_dir, "machine.json"), PARAMS, api_version=1)
    util.write_json(os.path.join(results_dir, "benchmarks.json"), benches, api_version=2)
    os.chdir(str(tmpdir))
    return (
        config.Config.from_json(
            {
                "results_dir": results_dir,
                "repo": tools.generate_test_repo(str(tmpdir)).path,
                "project": "asv",
                "environment_type": "virtualenv",
            }
        ),
        results_dir,
    )


def _store(results_dir, *results):
    for result in results:
        result.save(results_dir)


CASES = ("escape", "units", "failed", "params")


def _build(tmpdir, case, kind):
    if case == "escape":
        special = "cost_&%#${}~^\\"
        param = "_&%#${}~^\\\nline"
        benches = {
            special: _bench(special),
            "group.time_param": _bench(
                "group.time_param", params=[[param, "plain"]], param_names=["p"]
            ),
        }
        conf, results_dir = _conf(tmpdir, benches)
        before = _results("a" * 40)
        after = _results("b" * 40)
        _put(before, special, 1.0, [])
        _put(after, special, 1.0, [])
        _put(before, "group.time_param", [1.0, 2.0], [[param, "plain"]])
        _put(after, "group.time_param", [1.0, 2.0], [[param, "plain"]])
    elif case == "units":
        benches = {
            "time_ns": _bench("time_ns"),
            "time_us": _bench("time_us"),
            "time_ms": _bench("time_ms"),
            "time_s": _bench("time_s"),
            "time_min": _bench("time_min"),
            "time_hour": _bench("time_hour"),
            "time_day": _bench("time_day"),
            "time_week": _bench("time_week"),
            "mem_bytes": _bench("mem_bytes", unit="bytes"),
            "mem_kb": _bench("mem_kb", unit="bytes"),
            "custom_flop": _bench("custom_flop", unit="flop"),
        }
        conf, results_dir = _conf(tmpdir, benches)
        before = _results("a" * 40)
        after = _results("b" * 40)
        samples = {
            "time_ns": 1e-9,
            "time_us": 1.1e-6,
            "time_ms": 1.12e-3,
            "time_s": 1.123,
            "time_min": 60,
            "time_hour": 2 * 3600,
            "time_day": 6 * 86400,
            "time_week": 7 * 86400,
            "mem_bytes": 1,
            "mem_kb": 2000,
            "custom_flop": 1.5,
        }
        for name, value in samples.items():
            _put(before, name, value, [])
            _put(after, name, value, [])
    elif case == "failed":
        benches = {
            "both_failed": _bench("both_failed"),
            "introduced": _bench("introduced"),
            "fixed_failure": _bench("fixed_failure"),
            "skipped": _bench("skipped"),
            "only_after": _bench("only_after", unit="bytes"),
        }
        conf, results_dir = _conf(tmpdir, benches)
        before = _results("a" * 40)
        after = _results("b" * 40)
        _put(before, "both_failed", None, [])
        _put(after, "both_failed", None, [])
        _put(before, "introduced", 1.0, [])
        _put(after, "introduced", None, [])
        _put(before, "fixed_failure", None, [])
        _put(after, "fixed_failure", 1.0, [])
        _put(before, "skipped", float("nan"), [])
        _put(after, "skipped", float("nan"), [])
        _put(after, "only_after", 2000, [])
    elif case == "params":
        benches = {
            "group.time_grid": _bench(
                "group.time_grid",
                params=[["left", "right"], ["1", "2"]],
                param_names=["side", "n"],
            )
        }
        conf, results_dir = _conf(tmpdir, benches)
        before = _results("a" * 40)
        after = _results("b" * 40)
        grid = [["left", "right"], ["1", "2"]]
        _put(before, "group.time_grid", [1.0, 2.0, None, 4.0], grid)
        _put(after, "group.time_grid", [1.0, None, 3.0, 8.0], grid)
    else:
        raise AssertionError(case)
    _store(results_dir, before, after)
    return conf


def _render(tmpdir, case, kind):
    conf = _build(tmpdir, case, kind)
    if kind == "compare":
        Compare.print_table(
            conf,
            "a" * 40,
            "b" * 40,
            factor=1.1,
            split=False,
            machine="cheetah",
            only_changed=False,
            table_format="latex",
        )
    else:
        Show.run(conf, commit="a" * 40, machine="cheetah", table_format="latex")


def _compiles(tex, tmpdir):
    if shutil.which("pdflatex") is None:
        pytest.skip("pdflatex is not installed")
    work = Path(str(tmpdir)) / "latex"
    work.mkdir()
    document = (
        "\\documentclass{article}\n"
        "\\usepackage{booktabs,siunitx}\n"
        "\\begin{document}\n" + tex + "\n\\end{document}\n"
    )
    (work / "table.tex").write_text(document)
    result = subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "table.tex"],
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout
    assert (work / "table.pdf").is_file()


def test_compare_json_schema_matches_table_verdicts(capsys, tmpdir):
    conf = _build(tmpdir.mkdir("failed"), "failed", "compare")
    worsened, improved = Compare.print_table(
        conf,
        "a" * 40,
        "b" * 40,
        factor=1.1,
        split=False,
        machine="cheetah",
        only_changed=False,
        table_format="json",
    )
    text, _err = capsys.readouterr()
    payload = json.loads(text)
    assert payload["schema_version"] == COMPARE_SCHEMA_VERSION
    verdicts = {record["name"]: record["verdict"] for record in payload["records"]}
    assert verdicts["both_failed"] == "failed"
    assert verdicts["introduced"] == "failed"
    assert verdicts["fixed_failure"] == "failed"
    assert verdicts["skipped"] == "failed"
    assert verdicts["only_after"] == "failed"
    assert all(
        record["ratio_ci"] is None or len(record["ratio_ci"]) == 3 for record in payload["records"]
    )
    assert worsened or improved

    conf = _build(tmpdir.mkdir("params"), "params", "compare")
    Compare.print_table(
        conf,
        "a" * 40,
        "b" * 40,
        factor=1.1,
        split=False,
        machine="cheetah",
        only_changed=False,
        table_format="json",
    )
    text, _err = capsys.readouterr()
    payload = json.loads(text)
    grid = {
        (record["name"], tuple(record["params"])): record["verdict"]
        for record in payload["records"]
    }
    assert grid[("group.time_grid", ("left", "1"))] == "unchanged"
    assert grid[("group.time_grid", ("left", "2"))] == "failed"
    assert grid[("group.time_grid", ("right", "2"))] == "slower"

    Compare.print_table(
        conf,
        "a" * 40,
        "b" * 40,
        factor=1.1,
        split=False,
        machine="cheetah",
        only_changed=False,
        table_format="latex",
    )
    tex, _err = capsys.readouterr()
    # The LaTeX row splits the same name and parameter list the JSON record uses.
    assert r"group.time\_grid & left, 1 &" in tex
    assert r"group.time\_grid & right, 2 &" in tex
    assert r"\num{2.00} & + \\" in tex
    _compiles(tex, tmpdir.mkdir("params-tex"))


def test_compare_latex_scales_memory_and_time(capsys, tmpdir):
    conf = _build(tmpdir, "units", "compare")
    Compare.print_table(
        conf,
        "a" * 40,
        "b" * 40,
        factor=1.1,
        split=False,
        machine="cheetah",
        only_changed=False,
        table_format="json",
    )
    raw, _err = capsys.readouterr()
    payload = json.loads(raw)
    units = {record["name"]: record["before"]["unit"] for record in payload["records"]}
    assert units["mem_kb"] == "bytes"
    assert units["time_us"] == "seconds"
    Compare.print_table(
        conf,
        "a" * 40,
        "b" * 40,
        factor=1.1,
        split=False,
        machine="cheetah",
        only_changed=False,
        table_format="latex",
    )
    tex, _err = capsys.readouterr()
    assert r"mem\_kb &  & \qty{2}{\kilo\byte}" in tex
    assert r"mem\_bytes &  & \qty{1}{\byte}" in tex
    assert r"time\_us &  & \qty{1.10}{\micro\second}" in tex
    _compiles(tex, tmpdir)


@pytest.mark.parametrize("kind", ["compare", "show"])
@pytest.mark.parametrize("case", CASES)
def test_latex_golden_case(capsys, tmpdir, kind, case):
    _render(tmpdir, case, kind)
    text, _err = capsys.readouterr()
    path = GOLDEN / f"{kind}_{case}.tex"
    actual = text.strip() + "\n"
    assert actual == path.read_text()
    _compiles(text, tmpdir)

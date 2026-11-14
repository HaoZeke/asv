# Licensed under a 3-clause BSD style license - see LICENSE.rst

from pathlib import Path

import yaml


def test_pull_request_benchmark_workflow_parses():
    path = Path(__file__).resolve().parents[1] / "docs/source/examples/benchmark-pull-request.yml"
    document = yaml.safe_load(path.read_text())
    trigger = document["on"]
    assert "pull_request" in trigger
    jobs = document["jobs"]
    assert list(jobs) == ["benchmark"]
    job = jobs["benchmark"]
    assert isinstance(job["runs-on"], str)
    script = "\n".join(step.get("run", "") for step in job["steps"])
    assert "asv continuous" in script
    assert "--interleave-rounds" in script
    assert "--min-rounds" in script
    assert "--aa-band" in script
    assert "asv compare" in script
    assert "GITHUB_STEP_SUMMARY" in script
    assert "--quick" not in script
    assert "--format=latex" not in script

"""Scoring: Jaccard matching, and an end-to-end fixture regression."""

import random

import generate  # data/synthetic/generate.py (see conftest.py)
from src.ingest import load_ledger
from src.issues import compute_coverage, discover_issues
from src.profile import compute_profile
from src.score import format_report, jaccard, load_annotations, score


def test_jaccard():
    assert jaccard({"a"}, {"a"}) == 1.0
    assert jaccard({"a", "b"}, {"a"}) == 0.5
    assert jaccard({"a"}, {"b"}) == 0.0
    assert jaccard(set(), set()) == 0.0


def _build_fixture(tmp_path):
    rng = random.Random(generate.SEED)
    boring = generate.generate_boring(rng)
    seeded, concerns, fixtures = generate.generate_seeded(len(boring))
    ledger_path = tmp_path / "ledger.csv"
    ann_path = tmp_path / "ground_truth.csv"
    manifest_path = tmp_path / "fixture_manifest.csv"
    generate.serialize_ledger(boring + seeded, str(ledger_path))
    generate.serialize_concerns(concerns, str(ann_path))
    generate.serialize_manifest(fixtures, str(manifest_path))
    return str(ledger_path), str(ann_path), str(manifest_path)


def test_fixture_end_to_end(tmp_path):
    ledger_path, ann_path, manifest_path = _build_fixture(tmp_path)
    ledger = load_ledger(ledger_path)
    issues = discover_issues(ledger, compute_profile(ledger))
    questions = load_annotations(ann_path)
    result = score(issues, questions, threshold=0.5)

    # The baseline is a control, not perfect. It should recover most concerns
    # with high precision, and the harness must still report the seeded
    # imperfections (one borderline miss; benign extras) so it isn't vacuous.
    assert result.recall >= 0.9
    assert result.precision >= 0.8
    assert len(result.missed) >= 1  # borderline Utilities outlier
    assert len(result.false_positives) >= 1  # benign round-dollar extras

    cov = compute_coverage(ledger, issues)
    assert cov.fraction == 1.0  # baseline assesses everything


def test_fixture_manifest_all_behave_as_designed(tmp_path):
    from src.score import check_fixtures, load_fixture_manifest

    ledger_path, ann_path, manifest_path = _build_fixture(tmp_path)
    ledger = load_ledger(ledger_path)
    issues = discover_issues(ledger, compute_profile(ledger))
    questions = load_annotations(ann_path)
    result = score(issues, questions)

    manifest = load_fixture_manifest(manifest_path)
    checks = check_fixtures(manifest, issues, result.false_positives)
    assert len(checks) == 3
    # Every seeded imperfection must still behave as its id says — a DRIFT here
    # means the harness (or a detector threshold) changed under us.
    for fx, ok, observed in checks:
        assert ok, f"{fx['fixture_id']} drifted: {observed}"


def test_fixture_report_is_stamped(tmp_path):
    ledger_path, ann_path, manifest_path = _build_fixture(tmp_path)
    ledger = load_ledger(ledger_path)
    issues = discover_issues(ledger, compute_profile(ledger))
    questions = load_annotations(ann_path)
    result = score(issues, questions)

    report = format_report(result, "fixture", len(issues))
    assert "FIXTURE" in report
    assert "not the product" in report
    # A validation report must NOT carry the fixture disclaimer.
    val = format_report(result, "validation", len(issues))
    assert "not the product" not in val

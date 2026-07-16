"""abkit._read_version()/_format_version() (item 8 + 8-Б, audit-details+
package): version is sourced from a `git describe --tags --always --long`
snapshot baked in by docker/Dockerfile's `version` build stage — never a
hardcoded string that goes stale across releases."""

from __future__ import annotations

from abkit import _format_version, _read_version


def test_exact_tag_has_zero_distance():
    assert _format_version("v2.5.0-0-g1863360") == "v2.5.0"


def test_commits_past_tag_shows_distance_and_sha():
    assert _format_version("v2.5.0-3-gabc1234") == "v2.5.0+3 (abc1234)"


def test_no_tag_reachable_falls_back_to_dev_with_bare_sha():
    # `--always`'s fallback when there's no tag in history at all: a bare
    # abbreviated sha, no "-N-g" pattern to parse.
    assert _format_version("2684699") == "dev (2684699)"


def test_empty_describe_output_falls_back_to_plain_dev():
    assert _format_version("") == "dev"
    assert _format_version("   ") == "dev"


def test_read_version_uses_describe_file_when_present(tmp_path):
    describe_file = tmp_path / "VERSION_DESCRIBE"
    describe_file.write_text("v2.5.0-0-g1863360\n")
    assert _read_version(describe_file=describe_file) == "v2.5.0"


def test_read_version_falls_back_to_dev_when_no_file(tmp_path):
    assert _read_version(describe_file=tmp_path / "VERSION_DESCRIBE") == "dev"


def test_read_version_falls_back_to_dev_when_file_is_empty(tmp_path):
    describe_file = tmp_path / "VERSION_DESCRIBE"
    describe_file.write_text("")
    assert _read_version(describe_file=describe_file) == "dev"

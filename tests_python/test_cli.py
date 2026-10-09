# -*- coding: utf-8 -*-

import pytest

from agent_app import cli


class TestAsWordList:
    """
    fire hands `--retired` over in three different shapes, so normalizing it is
    the one piece of parsing this module still does by hand.
    """

    def test_empty_string_is_no_words(self):
        assert cli._as_word_list("") == []

    def test_a_single_word_arrives_as_a_string(self):
        assert cli._as_word_list("widget") == ["widget"]

    def test_a_comma_list_arrives_as_a_tuple(self):
        """`--retired "widget,part"` is split by fire before we ever see it."""
        assert cli._as_word_list(("widget", "part")) == ["widget", "part"]

    def test_a_comma_string_is_split_too(self):
        """Called directly rather than through fire, it is still one string."""
        assert cli._as_word_list("widget,part") == ["widget", "part"]

    def test_blanks_and_empties_are_dropped(self):
        assert cli._as_word_list("widget, part,, ") == ["widget", "part"]


class TestCheckContent:
    def test_passes_the_words_through(self, monkeypatch):
        seen = {}

        def fake_run(retired=None, skip_seed=False):
            seen["retired"] = retired
            return 0

        monkeypatch.setattr("agent_app.content_check.run", fake_run)
        with pytest.raises(SystemExit) as exc:
            cli.Cli().check_content()
        assert exc.value.code == 0
        assert seen["retired"] == []

    def test_splits_and_strips(self, monkeypatch):
        seen = {}

        def fake_run(retired=None, skip_seed=False):
            seen["retired"] = retired
            return 0

        monkeypatch.setattr("agent_app.content_check.run", fake_run)
        with pytest.raises(SystemExit):
            cli.Cli().check_content(retired="widget, part,, ")
        assert seen["retired"] == ["widget", "part"]

    def test_a_failed_check_exits_nonzero(self, monkeypatch):
        """
        The whole reason these commands raise instead of returning: fire prints
        a returned value and exits 0, which would make a failure look green.
        """
        monkeypatch.setattr("agent_app.content_check.run", lambda retired=None, skip_seed=False: 1)
        with pytest.raises(SystemExit) as exc:
            cli.Cli().check_content()
        assert exc.value.code == 1


class TestTestDbConn:
    """
    No network here. What is worth pinning is the exit code on each path, since
    that is what a task or a deploy gate reads.
    """

    def test_a_reachable_database_exits_zero(self, monkeypatch):
        monkeypatch.setattr(
            "agent_app.one.api.one.remote_postgres_engine",
            _FakeEngine(answer=1),
            raising=False,
        )
        with pytest.raises(SystemExit) as exc:
            cli.Cli().test_db_conn()
        assert exc.value.code == 0

    def test_an_unreachable_database_exits_one(self, monkeypatch):
        monkeypatch.setattr(
            "agent_app.one.api.one.remote_postgres_engine",
            _FakeEngine(error=OSError("no route to host")),
            raising=False,
        )
        with pytest.raises(SystemExit) as exc:
            cli.Cli().test_db_conn()
        assert exc.value.code == 1


class _FakeConnection:
    def __init__(self, answer=None, error=None):
        self._answer = answer
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, statement):
        if self._error is not None:
            raise self._error
        return self

    def scalar_one(self):
        return self._answer


class _FakeEngine:
    def __init__(self, answer=None, error=None):
        self._answer = answer
        self._error = error

    def connect(self):
        return _FakeConnection(answer=self._answer, error=self._error)


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.cli",
        preview=False,
    )

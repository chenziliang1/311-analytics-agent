# -*- coding: utf-8 -*-

import pytest

from agent_app.switches import NO_SWITCHES, Switches, SwitchReader, upsert_sql


class TestSwitches:
    def test_nothing_thrown(self):
        assert Switches.from_rows([]) == NO_SWITCHES
        assert Switches.from_rows([("kill", "off", None), ("suspend:x", "0", "")]) == NO_SWITCHES

    def test_kill_with_reason(self):
        s = Switches.from_rows([("KILL", "ON", "load failed at 03:00")])
        assert s.killed == "load failed at 03:00"
        assert not s.suspended

    def test_suspend_one_metric(self):
        s = Switches.from_rows([("suspend:sla_breach_rate", "true", "definition under review"), ("kill", "off", "")])
        assert s.killed is None
        assert s.suspended == {"sla_breach_rate": "definition under review"}

    def test_a_thrown_switch_without_a_reason_still_says_something(self):
        assert Switches.from_rows([("kill", "1", None)]).killed == "no reason given"

    def test_unknown_names_and_a_bare_suspend_are_ignored(self):
        assert Switches.from_rows([("pause", "on", ""), ("suspend:", "on", "")]) == NO_SWITCHES


class TestSwitchReader:
    def test_reads_once_per_ttl(self):
        clock = [100.0]
        reads = []

        def source():
            reads.append(clock[0])
            return [("kill", "on", "because")]

        reader = SwitchReader(source, ttl_seconds=30, clock=lambda: clock[0])
        assert reader().killed == "because"
        clock[0] = 110
        reader()
        assert len(reads) == 1
        clock[0] = 130
        reader()
        assert len(reads) == 2

    def test_a_failed_read_keeps_the_last_good_value_and_reports(self):
        clock = [0.0]
        rows = [[("suspend:reopen_rate", "on", "wrong")]]
        errors = []

        def source():
            if not rows:
                raise RuntimeError("warehouse down")
            return rows.pop()

        reader = SwitchReader(source, ttl_seconds=1, clock=lambda: clock[0], on_error=errors.append)
        assert reader().suspended == {"reopen_rate": "wrong"}
        clock[0] = 5
        assert reader().suspended == {"reopen_rate": "wrong"}
        assert len(errors) == 1 and "warehouse down" in str(errors[0])

    def test_before_any_good_read_nothing_is_thrown(self):
        def source():
            raise RuntimeError("no table")

        assert SwitchReader(source)() == NO_SWITCHES

    def test_refresh_forces_the_next_read(self):
        values = iter([[("kill", "on", "a")], [("kill", "off", "")]])
        reader = SwitchReader(lambda: next(values), ttl_seconds=3600)
        assert reader().killed == "a"
        reader.refresh()
        assert reader().killed is None


def test_upsert_is_one_merge_with_named_parameters():
    text = upsert_sql("CITYPULSE_311.CITYPULSE_AUDIT.SWITCHES")
    assert text.startswith("MERGE INTO CITYPULSE_311.CITYPULSE_AUDIT.SWITCHES")
    for p in ("%(name)s", "%(value)s", "%(reason)s", "%(changed_by)s"):
        assert p in text
    assert "WHEN MATCHED" in text and "WHEN NOT MATCHED" in text


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(__file__, "agent_app.switches", preview=False)

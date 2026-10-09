# -*- coding: utf-8 -*-

"""
Tests for the debug printer.

``chat()`` is not here: it drives a live agent, which is the one thing this
module exists to do and the one thing a test cannot do cheaply. What is left is
formatting, and formatting is worth checking because it is what a person reads
when something has gone wrong.
"""

from agent_app.agent_debugger import print_summary


class TestPrintSummary:
    def test_one_line_per_turn(self, capsys):
        print_summary([("Query", "thinking here", "the answer")])
        out = capsys.readouterr().out
        assert "SUMMARY" in out
        assert "[Query]" in out
        assert f"Thinking: {len('thinking here')} chars" in out
        assert f"Answer: {len('the answer')} chars" in out

    def test_several_turns_keep_their_order(self, capsys):
        print_summary([("First", "a", "b"), ("Second", "c", "d")])
        out = capsys.readouterr().out
        assert out.index("[First]") < out.index("[Second]")

    def test_no_turns_still_prints_a_header(self, capsys):
        """An empty run should look like an empty run, not like a crash."""
        print_summary([])
        assert "SUMMARY" in capsys.readouterr().out

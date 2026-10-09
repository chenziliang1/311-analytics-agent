# -*- coding: utf-8 -*-

if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.semantic_layer",
        is_folder=True,
        preview=False,
    )

"""Non-vacuous checks of leastness, ownership, exact transport and negative sinks."""
import unittest

from .fact_solver import (compile_graph, partition, solve_centralized,
                         solve_independent_reference, solve_distributed, witnesses)


def fixture():
    return {
        "nodes": [dict(id="source", component="client", seed=["input"]),
                  dict(id="request", component="server"),
                  dict(id="reply", component="server"),
                  dict(id="sink", component="client", sink_id="positive"),
                  dict(id="constant", component="client", sink_id="negative")],
        "edges": [dict(id="q", src="source", dst="request", kind="rpc_request"),
                  dict(id="v", src="request", dst="reply", kind="concat"),
                  dict(id="r", src="reply", dst="sink", kind="rpc_response"),
                  dict(id="cycle", src="reply", dst="request", kind="local")],
    }


class SolverTests(unittest.TestCase):
    def test_independent_reference_and_negative_sink(self):
        c = compile_graph(fixture())
        states, _ = solve_centralized(c)
        self.assertEqual(states, solve_independent_reference(c))
        self.assertEqual(states[c["index"]["constant"]], 0)
        path = witnesses(c, states)[0]
        self.assertTrue({"rpc_request", "rpc_response"} <= {e["kind"] for e in path["edges"]})

    def test_real_processes_exact_with_duplicates(self):
        c = compile_graph(fixture())
        reference, _ = solve_centralized(c)
        placement = partition(c, "Forced-rpc-cut", 4, 0)
        result, metrics = solve_distributed(c, placement, schedule_seed=37, duplicate=True)
        self.assertEqual(reference, result)
        self.assertGreater(metrics["records"], 0)
        self.assertGreater(metrics["duplicate_records"], 0)
        self.assertEqual(metrics["residual_equations"], 0)

    def test_missing_response_is_observable(self):
        graph = fixture()
        graph["edges"] = [e for e in graph["edges"] if e["kind"] != "rpc_response"]
        c = compile_graph(graph)
        states, _ = solve_centralized(c)
        self.assertEqual(states[c["index"]["sink"]], 0)

    def test_missing_source_rejected(self):
        graph = fixture()
        graph["nodes"][0]["seed"] = []
        with self.assertRaises(ValueError):
            compile_graph(graph)


if __name__ == "__main__":
    unittest.main()

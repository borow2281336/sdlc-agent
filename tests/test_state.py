from sdlc_agent.state import get_iteration, iter_labels


def test_get_iteration():
    labels = ["bug", "agent:iter-1", "agent:iter-3", "agent:managed"]
    assert get_iteration(labels) == 3
    assert set(iter_labels(labels)) == {"agent:iter-1", "agent:iter-3"}

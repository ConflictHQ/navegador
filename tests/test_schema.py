from navegador.graph.schema import EdgeType, NodeLabel


def test_node_labels():
    assert NodeLabel.File == "File"
    assert NodeLabel.Function == "Function"
    assert NodeLabel.Class == "Class"


def test_edge_types():
    assert EdgeType.CALLS == "CALLS"
    assert EdgeType.IMPORTS == "IMPORTS"
    assert EdgeType.CONTAINS == "CONTAINS"
    assert EdgeType.INHERITS == "INHERITS"

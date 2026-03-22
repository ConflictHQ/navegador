"""
Graph schema — node labels and edge types for the navegador property graph.

Node properties vary by label but all share: name, file_path, line_start, line_end.
"""

from enum import StrEnum


class NodeLabel(StrEnum):
    Repository = "Repository"
    File = "File"
    Module = "Module"
    Class = "Class"
    Function = "Function"
    Method = "Method"
    Variable = "Variable"
    Import = "Import"
    Decorator = "Decorator"


class EdgeType(StrEnum):
    # structural
    CONTAINS = "CONTAINS"       # File -CONTAINS-> Function/Class/Variable
    DEFINES = "DEFINES"         # Module -DEFINES-> Class/Function
    # dependencies
    IMPORTS = "IMPORTS"         # File -IMPORTS-> Module/File
    DEPENDS_ON = "DEPENDS_ON"   # Module/Package level dependency
    # code relationships
    CALLS = "CALLS"             # Function -CALLS-> Function
    REFERENCES = "REFERENCES"   # Function/Class -REFERENCES-> Variable/Class
    INHERITS = "INHERITS"       # Class -INHERITS-> Class
    IMPLEMENTS = "IMPLEMENTS"   # Class -IMPLEMENTS-> Class (for interfaces/ABCs)
    DECORATES = "DECORATES"     # Decorator -DECORATES-> Function/Class


# Common property keys per node label
NODE_PROPS = {
    NodeLabel.Repository: ["name", "path", "language", "description"],
    NodeLabel.File: ["name", "path", "language", "size", "line_count"],
    NodeLabel.Module: ["name", "file_path", "docstring"],
    NodeLabel.Class: ["name", "file_path", "line_start", "line_end", "docstring", "source"],
    NodeLabel.Function: [
        "name", "file_path", "line_start", "line_end", "docstring", "source", "signature",
    ],
    NodeLabel.Method: [
        "name", "file_path", "line_start", "line_end",
        "docstring", "source", "signature", "class_name",
    ],
    NodeLabel.Variable: ["name", "file_path", "line_start", "type_annotation"],
    NodeLabel.Import: ["name", "file_path", "line_start", "module", "alias"],
    NodeLabel.Decorator: ["name", "file_path", "line_start"],
}

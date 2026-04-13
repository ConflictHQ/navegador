"""
Graph schema — node labels and edge types for the navegador property graph.

Navegador maintains two complementary layers in one graph:

  CODE layer — AST-derived structure (files, functions, classes, calls, imports)
  KNOWLEDGE layer — business context (concepts, rules, decisions, wiki, people)

The two layers are connected by IMPLEMENTS, DOCUMENTS, GOVERNS, and ANNOTATES
edges, so agents can traverse from a function straight to the business rule it
enforces, or from a wiki page down to the exact code that implements it.
"""

from enum import StrEnum


class NodeLabel(StrEnum):
    # ── Code layer ────────────────────────────────────────────────────────────
    Repository = "Repository"
    File = "File"
    Module = "Module"
    Class = "Class"
    Function = "Function"
    Method = "Method"
    Variable = "Variable"
    Import = "Import"
    Decorator = "Decorator"

    # ── Knowledge layer ───────────────────────────────────────────────────────
    Domain = "Domain"  # logical grouping (auth, billing, notifications…)
    Concept = "Concept"  # a named business entity or idea
    Rule = "Rule"  # a constraint, invariant, or business rule
    Decision = "Decision"  # an architectural or product decision + rationale
    WikiPage = "WikiPage"  # a page from the project wiki (GitHub, Confluence…)
    Person = "Person"  # a contributor, owner, or stakeholder
    Document = "Document"  # a markdown documentation file (README, bootstrap, CLAUDE.md…)

    # ── History layer ─────────────────────────────────────────────────────────
    Snapshot = "Snapshot"  # a captured graph state at a specific git ref


class EdgeType(StrEnum):
    # ── Code structural ───────────────────────────────────────────────────────
    CONTAINS = "CONTAINS"  # File/Class -CONTAINS-> Function/Class/Variable
    DEFINES = "DEFINES"  # Module -DEFINES-> Class/Function
    IMPORTS = "IMPORTS"  # File -IMPORTS-> Module/File
    DEPENDS_ON = "DEPENDS_ON"  # module/package-level dependency
    CALLS = "CALLS"  # Function -CALLS-> Function
    REFERENCES = "REFERENCES"  # Function/Class -REFERENCES-> Variable/Class
    INHERITS = "INHERITS"  # Class -INHERITS-> Class
    IMPLEMENTS = "IMPLEMENTS"  # Class/Function -IMPLEMENTS-> Concept/Rule
    DECORATES = "DECORATES"  # Decorator -DECORATES-> Function/Class

    # ── Knowledge structural ──────────────────────────────────────────────────
    BELONGS_TO = "BELONGS_TO"  # any node -BELONGS_TO-> Domain
    RELATED_TO = "RELATED_TO"  # Concept -RELATED_TO-> Concept (bidirectional intent)
    GOVERNS = "GOVERNS"  # Rule -GOVERNS-> Concept/Function/Class
    DOCUMENTS = "DOCUMENTS"  # WikiPage/Decision -DOCUMENTS-> any node
    ANNOTATES = "ANNOTATES"  # Concept/Rule -ANNOTATES-> code node (lightweight link)
    ASSIGNED_TO = "ASSIGNED_TO"  # any node -ASSIGNED_TO-> Person (ownership)
    DECIDED_BY = "DECIDED_BY"  # Decision -DECIDED_BY-> Person

    # ── History layer ─────────────────────────────────────────────────────────
    SNAPSHOT_OF = "SNAPSHOT_OF"  # Snapshot -SNAPSHOT_OF-> Function/Class/Method


# ── Property keys per node label ──────────────────────────────────────────────

NODE_PROPS = {
    # Code layer
    NodeLabel.Repository: ["name", "path", "language", "description"],
    NodeLabel.File: ["name", "path", "language", "size", "line_count", "content_hash"],
    NodeLabel.Module: ["name", "file_path", "docstring"],
    NodeLabel.Class: ["name", "file_path", "line_start", "line_end", "docstring", "source"],
    NodeLabel.Function: [
        "name",
        "file_path",
        "line_start",
        "line_end",
        "docstring",
        "source",
        "signature",
    ],
    NodeLabel.Method: [
        "name",
        "file_path",
        "line_start",
        "line_end",
        "docstring",
        "source",
        "signature",
        "class_name",
    ],
    NodeLabel.Variable: ["name", "file_path", "line_start", "type_annotation"],
    NodeLabel.Import: ["name", "file_path", "line_start", "module", "alias"],
    NodeLabel.Decorator: ["name", "file_path", "line_start"],
    # Knowledge layer
    NodeLabel.Domain: ["name", "description"],
    NodeLabel.Concept: [
        "name",
        "description",
        "domain",
        "status",
        "rules",
        "examples",
        "wiki_refs",
    ],
    NodeLabel.Rule: [
        "name",
        "description",
        "domain",
        "severity",  # info|warning|critical
        "rationale",
        "examples",
    ],
    NodeLabel.Decision: [
        "name",
        "description",
        "domain",
        "status",  # proposed|accepted|deprecated
        "rationale",
        "alternatives",
        "date",
    ],
    NodeLabel.WikiPage: [
        "name",
        "url",
        "source",  # github|confluence|notion|local
        "content",
        "updated_at",
    ],
    NodeLabel.Person: [
        "name",
        "email",
        "role",
        "team",
    ],
    NodeLabel.Document: [
        "name",
        "path",
        "title",
        "content",
    ],
    NodeLabel.Snapshot: [
        "ref",
        "commit_sha",
        "committed_at",
        "symbol_count",
    ],
}

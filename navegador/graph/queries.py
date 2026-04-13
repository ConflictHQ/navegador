"""
Cypher query templates for navegador.

Parameters passed as $name are substituted by FalkorDB at query time.
Optional file_path filtering: when file_path is "" the WHERE clause is omitted
so callers/callees/references work across the whole graph by name alone.
"""

# ── Code: file contents ───────────────────────────────────────────────────────

FILE_CONTENTS = """
MATCH (f:File {path: $path})
CALL {
  WITH f
  MATCH (f)-[:CONTAINS]->(n)
  RETURN labels(n)[0] AS type, n.name AS name, n.line_start AS line,
         n.docstring AS docstring, n.signature AS signature
  UNION
  WITH f
  MATCH (f)-[:IMPORTS]->(i:Import)
  RETURN labels(i)[0] AS type, i.name AS name, i.line_start AS line,
         null AS docstring, null AS signature
}
RETURN type, name, line, docstring, signature
ORDER BY line
"""

DIRECT_IMPORTS = """
MATCH (n {file_path: $file_path, name: $name})-[:IMPORTS]->(dep)
RETURN labels(dep)[0] AS type, dep.name AS name, dep.file_path AS file_path
"""

# ── Code: call graph ──────────────────────────────────────────────────────────

# file_path is optional — if empty, match by name only across all files
CALLERS = """
MATCH (caller)-[:CALLS*1..$depth]->(fn)
WHERE fn.name = $name AND ($file_path = '' OR fn.file_path = $file_path)
RETURN DISTINCT labels(caller)[0] AS type, caller.name AS name,
       caller.file_path AS file_path, caller.line_start AS line
"""

CALLEES = """
MATCH (fn)-[:CALLS*1..$depth]->(callee)
WHERE fn.name = $name AND ($file_path = '' OR fn.file_path = $file_path)
RETURN DISTINCT labels(callee)[0] AS type, callee.name AS name,
       callee.file_path AS file_path, callee.line_start AS line
"""

# ── Code: class hierarchy ─────────────────────────────────────────────────────

CLASS_HIERARCHY = """
MATCH (c:Class)-[:INHERITS*]->(parent)
WHERE c.name = $name AND ($file_path = '' OR c.file_path = $file_path)
RETURN parent.name AS name, parent.file_path AS file_path
"""

SUBCLASSES = """
MATCH (child:Class)-[:INHERITS*]->(c:Class)
WHERE c.name = $name AND ($file_path = '' OR c.file_path = $file_path)
RETURN child.name AS name, child.file_path AS file_path
"""

# ── Code: decorators ─────────────────────────────────────────────────────────

# All functions/methods carrying a given decorator
DECORATED_BY = """
MATCH (d:Decorator {name: $decorator_name})-[:DECORATES]->(n)
RETURN labels(n)[0] AS type, n.name AS name, n.file_path AS file_path,
       n.line_start AS line
"""

# All decorators on a given function/method
DECORATORS_FOR = """
MATCH (d:Decorator)-[:DECORATES]->(n)
WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path)
RETURN d.name AS decorator, d.file_path AS file_path, d.line_start AS line
"""

# ── Code: references ─────────────────────────────────────────────────────────

REFERENCES_TO = """
MATCH (src)-[:REFERENCES]->(tgt)
WHERE tgt.name = $name AND ($file_path = '' OR tgt.file_path = $file_path)
RETURN DISTINCT labels(src)[0] AS type, src.name AS name,
       src.file_path AS file_path, src.line_start AS line
"""

# ── Universal: neighbor traversal ────────────────────────────────────────────

# All nodes reachable from a named node within N hops (any edge type)
NEIGHBORS = """
MATCH (root)
WHERE root.name = $name AND ($file_path = '' OR root.file_path = $file_path)
OPTIONAL MATCH (root)-[r*1..$depth]-(neighbor)
RETURN DISTINCT
    labels(root)[0] AS root_type,
    root.name AS root_name,
    labels(neighbor)[0] AS neighbor_type,
    neighbor.name AS neighbor_name,
    neighbor.file_path AS neighbor_file_path
"""

# ── Universal: search ─────────────────────────────────────────────────────────

# Search code symbols by name substring
SYMBOL_SEARCH = """
MATCH (n)
WHERE (n:Function OR n:Class OR n:Method) AND n.name CONTAINS $query
RETURN labels(n)[0] AS type, n.name AS name, n.file_path AS file_path,
       n.line_start AS line, n.docstring AS docstring
LIMIT $limit
"""

# Search code symbols by docstring content
DOCSTRING_SEARCH = """
MATCH (n)
WHERE (n:Function OR n:Class OR n:Method)
  AND n.docstring IS NOT NULL
  AND toLower(n.docstring) CONTAINS toLower($query)
RETURN labels(n)[0] AS type, n.name AS name, n.file_path AS file_path,
       n.line_start AS line, n.docstring AS docstring
LIMIT $limit
"""

# Search knowledge layer (concepts, rules, decisions, wiki) by name or description
KNOWLEDGE_SEARCH = """
MATCH (n)
WHERE (n:Concept OR n:Rule OR n:Decision OR n:WikiPage OR n:Document)
  AND (toLower(n.name) CONTAINS toLower($query)
       OR (n.description IS NOT NULL AND toLower(n.description) CONTAINS toLower($query))
       OR (n.content IS NOT NULL AND toLower(n.content) CONTAINS toLower($query)))
RETURN labels(n)[0] AS type, n.name AS name, n.description AS description,
       n.domain AS domain, n.status AS status
LIMIT $limit
"""

# Search everything — code + knowledge — in one query
GLOBAL_SEARCH = """
MATCH (n)
WHERE (n:Function OR n:Class OR n:Method OR n:Concept OR n:Rule
       OR n:Decision OR n:WikiPage OR n:Document)
  AND (toLower(n.name) CONTAINS toLower($query)
       OR (n.docstring IS NOT NULL AND toLower(n.docstring) CONTAINS toLower($query))
       OR (n.description IS NOT NULL AND toLower(n.description) CONTAINS toLower($query))
       OR (n.content IS NOT NULL AND toLower(n.content) CONTAINS toLower($query)))
RETURN labels(n)[0] AS type, n.name AS name,
       coalesce(n.file_path, n.path, '') AS file_path,
       coalesce(n.docstring, n.description, n.content, '') AS summary,
       n.line_start AS line
LIMIT $limit
"""

# ── Knowledge: domain ─────────────────────────────────────────────────────────

DOMAIN_CONTENTS = """
MATCH (n)-[:BELONGS_TO]->(d:Domain {name: $domain})
RETURN labels(n)[0] AS type, n.name AS name,
       coalesce(n.file_path, '') AS file_path,
       coalesce(n.docstring, n.description, '') AS summary
ORDER BY labels(n)[0], n.name
"""

# ── Knowledge: concept context ───────────────────────────────────────────────

CONCEPT_CONTEXT = """
MATCH (c:Concept {name: $name})
OPTIONAL MATCH (c)-[:RELATED_TO]-(related:Concept)
OPTIONAL MATCH (rule:Rule)-[:GOVERNS]->(c)
OPTIONAL MATCH (wiki:WikiPage)-[:DOCUMENTS]->(c)
OPTIONAL MATCH (impl)-[:IMPLEMENTS]->(c)
OPTIONAL MATCH (c)-[:BELONGS_TO]->(domain:Domain)
RETURN
    c.name AS name, c.description AS description,
    c.status AS status, c.domain AS domain,
    collect(DISTINCT related.name) AS related_concepts,
    collect(DISTINCT rule.name) AS governing_rules,
    collect(DISTINCT wiki.name) AS wiki_pages,
    collect(DISTINCT impl.name) AS implemented_by,
    collect(DISTINCT domain.name) AS domains
"""

# ── Explain: full picture for any named node ──────────────────────────────────

# All outbound relationships from a node
OUTBOUND = """
MATCH (n)-[r]->(neighbor)
WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path)
RETURN type(r) AS rel, labels(neighbor)[0] AS neighbor_type,
       neighbor.name AS neighbor_name,
       coalesce(neighbor.file_path, '') AS neighbor_file_path
ORDER BY rel, neighbor_name
"""

# All inbound relationships to a node
INBOUND = """
MATCH (neighbor)-[r]->(n)
WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path)
RETURN type(r) AS rel, labels(neighbor)[0] AS neighbor_type,
       neighbor.name AS neighbor_name,
       coalesce(neighbor.file_path, '') AS neighbor_file_path
ORDER BY rel, neighbor_name
"""

# ── Knowledge: decision rationale ─────────────────────────────────────────────

DECISION_RATIONALE = """
MATCH (d:Decision {name: $name})
OPTIONAL MATCH (d)-[:DOCUMENTS]->(target)
OPTIONAL MATCH (d)-[:DECIDED_BY]->(person:Person)
OPTIONAL MATCH (d)-[:BELONGS_TO]->(domain:Domain)
RETURN
    d.name AS name, d.description AS description,
    d.rationale AS rationale, d.alternatives AS alternatives,
    d.status AS status, d.date AS date, d.domain AS domain,
    collect(DISTINCT target.name) AS documents,
    collect(DISTINCT person.name) AS decided_by,
    collect(DISTINCT domain.name) AS domains
"""

# ── Knowledge: find owners (ASSIGNED_TO → Person) ────────────────────────────

FIND_OWNERS = """
MATCH (n)-[:ASSIGNED_TO]->(p:Person)
WHERE n.name = $name AND ($file_path = '' OR n.file_path = $file_path)
RETURN labels(n)[0] AS node_type, n.name AS node_name,
       p.name AS owner, p.email AS email, p.role AS role, p.team AS team
"""

# ── Incremental ingestion ─────────────────────────────────────────────────────

FILE_HASH = """
MATCH (f:File {path: $path})
RETURN f.content_hash AS hash
"""

DELETE_FILE_SUBGRAPH = """
MATCH (f:File {path: $path})-[:CONTAINS]->(child)
DETACH DELETE child
"""

DOCUMENT_HASH = """
MATCH (d:Document {path: $path})
RETURN d.content_hash AS hash
"""

DELETE_DOCUMENT = """
MATCH (d:Document {path: $path})
DETACH DELETE d
"""

# ── Memory: CONFLICT-format knowledge nodes ──────────────────────────────────

MEMORY_LIST = """
MATCH (n)
WHERE n.memory_type IS NOT NULL
  AND ($type = '' OR n.memory_type = $type)
  AND ($scope = 'workspace' OR $repo = '' OR n.repo = $repo)
RETURN labels(n)[0] AS label, n.name AS name, n.description AS description,
       n.memory_type AS memory_type, n.repo AS repo,
       coalesce(n.rationale, n.content, n.description, '') AS content
ORDER BY n.memory_type, n.name
LIMIT $limit
"""

MEMORY_GET = """
MATCH (n {name: $name})
WHERE n.memory_type IS NOT NULL
  AND ($repo = '' OR n.repo = $repo)
RETURN labels(n)[0] AS label, n.name AS name, n.description AS description,
       n.memory_type AS memory_type, n.repo AS repo,
       coalesce(n.rationale, n.content, n.description, '') AS content
"""

MEMORY_FOR_FILE = """
MATCH (f:File {path: $path})-[:CONTAINS]->(sym)
MATCH (mem)-[:GOVERNS|ANNOTATES|DOCUMENTS]->(sym)
WHERE mem.memory_type IS NOT NULL
RETURN DISTINCT labels(mem)[0] AS label, mem.name AS name,
       mem.description AS description, mem.memory_type AS memory_type,
       mem.repo AS repo,
       coalesce(mem.rationale, mem.content, mem.description, '') AS content
"""

# ── Stats ─────────────────────────────────────────────────────────────────────

NODE_TYPE_COUNTS = """
MATCH (n)
RETURN labels(n)[0] AS type, count(n) AS count
ORDER BY count DESC
"""

EDGE_TYPE_COUNTS = """
MATCH ()-[r]->()
RETURN type(r) AS type, count(r) AS count
ORDER BY count DESC
"""

"""
Common Cypher query templates for navegador context loading.
"""

# Find all nodes contained in a file
FILE_CONTENTS = """
MATCH (f:File {path: $path})-[:CONTAINS]->(n)
RETURN labels(n)[0] AS type, n.name AS name, n.line_start AS line,
       n.docstring AS docstring, n.signature AS signature
ORDER BY n.line_start
"""

# Find everything a function/file directly imports
DIRECT_IMPORTS = """
MATCH (n {file_path: $file_path, name: $name})-[:IMPORTS]->(dep)
RETURN labels(dep)[0] AS type, dep.name AS name, dep.file_path AS file_path
"""

# Find callers of a function (up to N hops)
CALLERS = """
MATCH (caller)-[:CALLS*1..$depth]->(fn {name: $name, file_path: $file_path})
RETURN DISTINCT labels(caller)[0] AS type, caller.name AS name,
       caller.file_path AS file_path, caller.line_start AS line
"""

# Find callees of a function (what it calls, up to N hops)
CALLEES = """
MATCH (fn {name: $name, file_path: $file_path})-[:CALLS*1..$depth]->(callee)
RETURN DISTINCT labels(callee)[0] AS type, callee.name AS name,
       callee.file_path AS file_path, callee.line_start AS line
"""

# Class hierarchy
CLASS_HIERARCHY = """
MATCH (c:Class {name: $name})-[:INHERITS*]->(parent)
RETURN parent.name AS name, parent.file_path AS file_path
"""

# All subclasses
SUBCLASSES = """
MATCH (child:Class)-[:INHERITS*]->(c:Class {name: $name})
RETURN child.name AS name, child.file_path AS file_path
"""

# Context bundle: a file + everything 1-2 hops out
CONTEXT_BUNDLE = """
MATCH (root {file_path: $file_path})
OPTIONAL MATCH (root)-[r1:CALLS|IMPORTS|CONTAINS|INHERITS*1..2]-(neighbor)
RETURN root, collect(DISTINCT neighbor) AS neighbors,
       collect(DISTINCT type(r1)) AS edge_types
"""

# Symbol search
SYMBOL_SEARCH = """
MATCH (n)
WHERE (n:Function OR n:Class OR n:Method) AND n.name CONTAINS $query
RETURN labels(n)[0] AS type, n.name AS name, n.file_path AS file_path,
       n.line_start AS line, n.docstring AS docstring
LIMIT $limit
"""

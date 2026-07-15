from neo4j import GraphDatabase
import sys
sys.path.insert(0, '/app')
from app.core.config import settings
d = GraphDatabase.driver(settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD))
with d.session() as s:
    r = s.run("MATCH (n:WorkOrder) WHERE n.id STARTS WITH 'WORK-' RETURN count(n)").single()
    print(f"Found: {r[0]}")
    s.run("MATCH (n:WorkOrder) WHERE n.id STARTS WITH 'WORK-' DETACH DELETE n")
    print("Cleared")
d.close()

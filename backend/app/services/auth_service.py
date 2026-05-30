"""Auth Service — RBAC permission check with role hierarchy.

Uses Neo4j for user→role lookup and role hierarchy traversal.
"""

from app.core.logger import log


class AuthService:
    """Checks whether a user has the required roles for an action or rule."""

    def __init__(self):
        self._role_hierarchy: dict[str, set[str]] | None = None

    async def _build_hierarchy(self) -> dict[str, set[str]]:
        """Build role→ancestors map from Neo4j Role data nodes.

        Role nodes created by push_data have properties: id, name, parentRole, description.
        Returns dict of {role_name: set(all_ancestor_names_including_self)}.
        """
        from app.services.neo4j_service import neo4j_service

        if not neo4j_service.connected:
            log.warning("[Auth] Neo4j not connected, cannot build role hierarchy")
            return {}

        try:
            records = await neo4j_service.execute_read(
                "MATCH (r:Role) WHERE r.parentRole IS NOT NULL "
                "RETURN r.name AS name, r.parentRole AS parent"
            )
        except Exception as e:
            log.warning(f"[Auth] Neo4j role hierarchy query failed: {e}")
            return {}

        parent_map: dict[str, str] = {}
        for r in records:
            name = r.get("name", "")
            parent = r.get("parent", "")
            if name and parent:
                parent_map[name] = parent

        if not parent_map:
            log.warning("[Auth] Role hierarchy not configured (no parentRole values in Neo4j)")
            return {}

        # parentRole = org-chart superior (who I report to).
        # RBAC: a senior role inherits permissions from ALL subordinates (transitively).
        # Build "subordinate closure" for each role.
        all_roles = set(parent_map.keys()) | set(parent_map.values())

        # Map each role → its immediate subordinates
        subordinates: dict[str, set[str]] = {r: set() for r in all_roles}
        for role, superior in parent_map.items():
            if superior in subordinates:
                subordinates[superior].add(role)

        # Transitive closure: for each role, add its subordinates' subordinates, etc.
        def get_all_subordinates(role: str, visited: set[str]) -> set[str]:
            if role in visited:
                return set()
            visited.add(role)
            result: set[str] = set()
            for sub in subordinates.get(role, set()):
                result.add(sub)
                result.update(get_all_subordinates(sub, visited))
            return result

        hierarchy: dict[str, set[str]] = {}
        for role in all_roles:
            hierarchy[role] = {role} | get_all_subordinates(role, set())

        log.info(f"[Auth] role hierarchy built: {len(hierarchy)} roles from Neo4j")
        return hierarchy

    async def _get_hierarchy(self) -> dict[str, set[str]]:
        if self._role_hierarchy is None:
            self._role_hierarchy = await self._build_hierarchy()
        return self._role_hierarchy

    async def get_effective_roles(self, user_id: str) -> set[str]:
        """Return all effective roles for a user (direct + inherited)."""
        direct_roles = await self._get_direct_roles_from_neo4j(user_id)

        if not direct_roles:
            # Fallback: try ontology metadata
            direct_roles = self._get_direct_roles_from_ontology(user_id)

        hierarchy = await self._get_hierarchy()

        effective: set[str] = set()
        for role in direct_roles:
            effective.update(hierarchy.get(role, {role}))

        return effective

    def _get_direct_roles_from_ontology(self, user_id: str) -> set[str]:
        """Get user's direct role labels from ontology individuals (sync fallback)."""
        from app.services.ontology_service import ontology_service

        concepts = ontology_service.get_concepts()
        emp_concept = next((c for c in concepts if c["name"] == "Employee"), None)
        if not emp_concept:
            return set()

        for ind in emp_concept.get("individuals", []):
            if ind.get("name") == user_id:
                for pv in ind.get("values", []):
                    if pv.get("propertyName") == "role":
                        return {pv.get("value", "")}
        return set()

    async def _get_direct_roles_from_neo4j(self, user_id: str) -> set[str]:
        """Get user's direct role labels from Neo4j business data."""
        from app.services.neo4j_service import neo4j_service

        if not neo4j_service.connected:
            return set()

        try:
            records = await neo4j_service.execute_read(
                "MATCH (e:Employee {id: $id})-[:担任角色]->(r:Role) RETURN r.name AS name",
                {"id": user_id},
            )
            return {r["name"] for r in records}
        except Exception as e:
            log.warning(f"[Auth] Neo4j role query failed: {e}")
            return set()

    async def check(self, user_id: str, required_roles: list[str]) -> bool:
        """Check if user has any of the required roles (including inheritance).

        Args:
            user_id: Employee individual name (e.g. 'emp_001')
            required_roles: List of role labels required (e.g. ['操作工', '班长'])

        Returns:
            True if user's effective roles overlap with required_roles.
            If required_roles is empty, returns True (no restriction).
        """
        if not required_roles:
            return True

        effective = await self.get_effective_roles(user_id)
        if not effective:
            return False

        allowed = bool(effective & set(required_roles))
        if not allowed:
            log.info(
                f"[Auth] DENIED: user={user_id}, "
                f"effective={effective}, required={required_roles}"
            )
        return allowed

    async def get_user_property(self, user_id: str, property_name: str):
        """Query an Employee node's property value from Neo4j."""
        from app.services.neo4j_service import neo4j_service

        if not neo4j_service.connected or not user_id or not property_name:
            return None

        try:
            records = await neo4j_service.execute_read(
                "MATCH (e:Employee {id: $id}) RETURN e[$prop] AS val",
                {"id": user_id, "prop": property_name},
            )
            if records:
                return records[0].get("val")
        except Exception as e:
            log.warning(f"[Auth] get_user_property failed for {user_id}.{property_name}: {e}")
        return None

    def reset(self):
        """Clear cached hierarchy (call after ontology reload)."""
        self._role_hierarchy = None


auth_service = AuthService()

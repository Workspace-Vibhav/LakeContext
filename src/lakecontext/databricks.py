"""Read Unity Catalog metadata through Databricks unified authentication."""

from lakecontext.models import Column, Snapshot, Table


def collect(workspace, catalogs: list[str], schemas: list[str] | None = None) -> Snapshot:
    if not catalogs:
        raise ValueError("Specify at least one catalog")
    identity = workspace.current_user.me()
    if not identity.id:
        raise ValueError("Databricks did not return an identity ID")
    tables = []
    schema_filter = set(schemas or [])
    for catalog in sorted(set(catalogs)):
        for schema in workspace.schemas.list(catalog_name=catalog):
            if not schema.name or schema.name == "information_schema":
                continue
            if schema_filter and schema.name not in schema_filter:
                continue
            # Fail the whole collection on an API error. Never replace a good
            # snapshot with a partially fetched one after a permission/API failure.
            for item in workspace.tables.list(catalog_name=catalog, schema_name=schema.name):
                if not item.full_name:
                    raise ValueError("Databricks returned a table without a fully qualified name")
                detail = workspace.tables.get(full_name=item.full_name)
                if detail.columns is None:
                    raise ValueError(f"Column metadata unavailable for {item.full_name}")
                table_type = getattr(detail.table_type, "value", detail.table_type) or "TABLE"
                tables.append(
                    Table(
                        name=item.full_name,
                        description=detail.comment or "",
                        owner=detail.owner or "",
                        table_type=table_type,
                        columns=[
                            Column(
                                name=col.name,
                                data_type=col.type_text or "UNKNOWN",
                                description=col.comment or "",
                            )
                            for col in detail.columns
                        ],
                    )
                )
    scope = ",".join(sorted(set(catalogs))) + "/" + ",".join(sorted(schema_filter))
    return Snapshot(
        source=f"{workspace.config.host.rstrip('/')}#scope={scope}",
        principal=str(identity.id),
        tables=tables,
    )

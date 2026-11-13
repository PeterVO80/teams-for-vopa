"""Relations (customers and suppliers) instead of typed names

Revision ID: 0005
Revises: 0004

Projects, articles, purchase orders and supplier catalogues stored the name
of their customer/supplier as free text. This migration:

1. creates the ``companies`` table;
2. creates one company per distinct name already in use - names that only
   differ in capitals or surrounding spaces become one company - marked as
   customer (from projects) and/or supplier (from articles, orders,
   catalogues);
3. links the existing records to those companies;
4. drops the old text columns.

The downgrade restores the text columns from the company names.
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

#: (table, old text column, new foreign key column, role of the company)
LINKS = [
    ("projects", "customer", "customer_id", "is_customer"),
    ("articles", "supplier", "supplier_id", "is_supplier"),
    ("purchase_orders", "supplier", "supplier_id", "is_supplier"),
    ("supplier_catalogs", "supplier", "supplier_id", "is_supplier"),
]


def upgrade():
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_customer", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_supplier", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("vat_number", sa.String(length=30), nullable=True),
        sa.Column("street", sa.String(length=200), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("country", sa.String(length=80), nullable=True),
        sa.Column("contact_person", sa.String(length=120), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=120), nullable=True),
        sa.Column("website", sa.String(length=200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
    )
    op.create_index(op.f("ix_companies_name"), "companies", ["name"], unique=True)

    for table, _old, new, _role in LINKS:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column(new, sa.Integer(), nullable=True))
            batch.create_index(op.f(f"ix_{table}_{new}"), [new], unique=False)
            batch.create_foreign_key(op.f(f"fk_{table}_{new}_companies"), "companies", [new], ["id"])

    # One company per distinct name (case/space-insensitive); the spelling
    # that occurs first alphabetically is kept as the company name.
    union = " UNION ALL ".join(
        f"SELECT trim({old}) AS name, {'true' if role == 'is_customer' else 'false'} AS c, "
        f"{'true' if role == 'is_supplier' else 'false'} AS s FROM {table} "
        f"WHERE {old} IS NOT NULL AND trim({old}) <> ''"
        for table, old, _new, role in LINKS
    )
    op.execute(f"""
        INSERT INTO companies (name, is_customer, is_supplier, country, active, created_at, updated_at)
        SELECT min(name), bool_or(c), bool_or(s), 'België', true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM ({union}) AS names
        GROUP BY lower(name)
    """)
    for table, old, new, _role in LINKS:
        op.execute(f"""
            UPDATE {table} SET {new} = companies.id FROM companies
            WHERE lower(companies.name) = lower(trim({table}.{old}))
        """)
    # Orders and catalogues always had a supplier: keep that rule.
    for table in ("purchase_orders", "supplier_catalogs"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("supplier_id", existing_type=sa.Integer(), nullable=False)

    for table, old, _new, _role in LINKS:
        with op.batch_alter_table(table) as batch:
            if table == "supplier_catalogs":
                batch.drop_index("ix_supplier_catalogs_supplier")
            batch.drop_column(old)


def downgrade():
    for table, old, new, _role in LINKS:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column(old, sa.String(length=200), nullable=True))
        op.execute(f"UPDATE {table} SET {old} = companies.name FROM companies WHERE companies.id = {table}.{new}")
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(op.f(f"fk_{table}_{new}_companies"), type_="foreignkey")
            batch.drop_index(op.f(f"ix_{table}_{new}"))
            batch.drop_column(new)
    op.create_index("ix_supplier_catalogs_supplier", "supplier_catalogs", ["supplier"], unique=False)
    op.drop_index(op.f("ix_companies_name"), table_name="companies")
    op.drop_table("companies")

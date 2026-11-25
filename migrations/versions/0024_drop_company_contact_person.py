"""Relations: remove the old free-text field "contact_person" (replaced by contacts).

Nothing is lost: a filled-in value is first added at the top of the
relation's notes as "Contactpersoon (oud veld): ...". A downgrade adds the
column back, empty (the text stays in the notes).

Revision ID: 0024
Revises: 0023

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None

PREFIX = "Contactpersoon (oud veld): "


def upgrade():
    companies = sa.table("companies", sa.column("id", sa.Integer), sa.column("contact_person", sa.String),
                         sa.column("notes", sa.Text))
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(companies.c.id, companies.c.contact_person, companies.c.notes)
        .where(companies.c.contact_person.is_not(None), companies.c.contact_person != "")
    ).all()
    for company_id, person, notes in rows:
        text = PREFIX + person.strip() + (f"\n\n{notes}" if notes and notes.strip() else "")
        connection.execute(companies.update().where(companies.c.id == company_id).values(notes=text))

    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.drop_column('contact_person')


def downgrade():
    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('contact_person', sa.String(length=120), nullable=True))

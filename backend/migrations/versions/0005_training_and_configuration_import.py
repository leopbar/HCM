"""Training exercises and reviewed configuration imports.

Revision ID: 0005_training_and_configuration_import
Revises: 0004_environment_publication
"""
from alembic import op
from app.models import Base

revision = "0005_training_import"
down_revision = "0004_environment_publication"
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade():
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, tables=[
        Base.metadata.tables["training_exercise_progress"],
        Base.metadata.tables["configuration_import_previews"],
    ])

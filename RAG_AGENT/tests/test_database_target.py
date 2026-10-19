import pytest

from app.database_target import validate_target


def test_accepts_project_database_and_restricted_role():
    validate_target('zhanggui_dev', 'zhanggui_app', False)


@pytest.mark.parametrize('database,user,superuser', [
    ('erp_agent', 'zhanggui_app', False),
    ('zhanggui_dev', 'postgres', True),
    ('zhanggui_dev', 'zhanggui_app', True),
])
def test_rejects_wrong_database_or_privileged_role(database, user, superuser):
    with pytest.raises(RuntimeError, match='target'):
        validate_target(database, user, superuser)

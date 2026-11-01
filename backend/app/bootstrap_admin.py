"""Create (or reset) the single system administrator and deactivate every other account.

    docker compose exec backend python -m app.bootstrap_admin --email admin@empresa.com.br

The generated password is printed once and never stored in plain text. The administrator
must replace it at the first sign-in and then creates the accounts for everyone else.
Other accounts are deactivated, not deleted, so approvals and audit history keep their author.
Pass --keep-others to only (re)create the administrator.
"""
import argparse
from datetime import datetime, timezone
from sqlalchemy import func, select, update
from app.database import SessionLocal
from app.models import Activity, User, UserSession
from app.security import generate_password, hash_password


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="Administrador do Sistema")
    parser.add_argument("--keep-others", action="store_true")
    args = parser.parse_args()
    email = args.email.strip().lower()
    password = generate_password()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(func.lower(User.email) == email))
        if admin is None:
            admin = User(email=email, full_name=args.name, role="system_admin", password_hash="")
            db.add(admin)
        admin.full_name, admin.role, admin.active = args.name, "system_admin", True
        admin.company_id = admin.employee_id = admin.access_group_id = None
        admin.password_hash, admin.must_change_password, admin.password_changed_at = hash_password(password), True, now
        admin.failed_login_attempts, admin.locked_until = 0, None
        db.flush()
        deactivated = 0
        if not args.keep_others:
            deactivated = db.execute(update(User).where(User.id != admin.id, User.active.is_(True)).values(active=False)).rowcount
        db.execute(update(UserSession).where(UserSession.revoked_at.is_(None)).values(revoked_at=now))
        db.add(Activity(company_id=None, actor="HCM Lab", message=f"Administrador {email} (re)criado; {deactivated} outra(s) conta(s) desativada(s)."))
        db.commit()
    print("Administrador pronto.")
    print(f"  E-mail: {email}")
    print(f"  Senha temporária: {password}")
    print("  A senha deverá ser trocada no primeiro acesso. Ela não fica registrada em nenhum arquivo.")
    print(f"  Contas desativadas: {deactivated}")


if __name__ == "__main__":
    main()

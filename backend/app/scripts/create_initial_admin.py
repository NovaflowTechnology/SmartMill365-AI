"""Create the first local Admin account.

Run from the backend directory:
    python -m app.scripts.create_initial_admin
"""

from sqlalchemy import select

from app.auth.database import db_session, init_auth_tables
from app.auth.models import User
from app.auth.service import create_user_account


def main():
    init_auth_tables()
    print("Initial Admin Account Setup")
    print("===========================")

    with db_session() as db:
        existing_admin = db.scalar(select(User).where(User.role == "admin").limit(1))
        if existing_admin:
            print("An Admin account already exists. Create additional accounts from Account Management.")
            return

        full_name = input("Full name: ").strip()
        email = input("Email: ").strip()
        if not full_name or not email:
            print("Full name and email are required.")
            return

        user, temporary_password = create_user_account(
            db,
            actor=None,
            email=email,
            full_name=full_name,
            role="admin",
            ip_address="local-setup",
        )
        print("\nAdmin account created successfully.")
        print(f"Email: {user.email}")
        print(f"Temporary password: {temporary_password}")
        print("\nThis password is shown only now. Sign in and set a private password immediately.")


if __name__ == "__main__":
    main()

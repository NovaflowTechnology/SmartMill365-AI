"""Create local authentication tables without changing other application storage."""

from app.auth.database import init_auth_tables


def main():
    init_auth_tables()
    print("Authentication tables are ready.")


if __name__ == "__main__":
    main()

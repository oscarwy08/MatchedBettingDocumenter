from pathlib import Path

from flask import Flask, g

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
EXCEL_PATH = DATA_DIR / "matched_betting.xlsx"


def create_app() -> Flask:
    from decimal import Decimal

    from app.db import init_db
    from app.excel import sync_workbook
    from app.routes import bp
    from app.seed import seed_accounts

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = "matched-betting-documenter-local"
    app.config["DB_PATH"] = str(DB_PATH)
    app.config["EXCEL_PATH"] = str(EXCEL_PATH)
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

    Session = init_db(DB_PATH)

    @app.teardown_appcontext
    def close_db(_exc=None):
        session = g.pop("db", None)
        if session is not None:
            session.close()

    @app.template_filter("gbp")
    def gbp(value):
        if value is None or value == "":
            return "–"
        amount = Decimal(str(value))
        sign = "−" if amount < 0 else ""
        return f"{sign}£{abs(amount):,.2f}"

    @app.template_filter("pnl")
    def pnl(value):
        if value is None or value == "":
            return "pnl-zero"
        amount = Decimal(str(value))
        if amount > 0:
            return "pnl-pos"
        if amount < 0:
            return "pnl-neg"
        return "pnl-zero"

    @app.template_filter("labelize")
    def labelize(value):
        return str(value).replace("_", " ").title()

    @app.template_filter("ukdate")
    def ukdate(value):
        from app.dates import format_uk
        return format_uk(value)

    @app.context_processor
    def inject_globals():
        from app.version import VERSION

        return {"app_version": VERSION}

    boot = Session()
    try:
        seed_accounts(boot)
        sync_workbook(boot)
    finally:
        boot.close()

    app.register_blueprint(bp)
    return app

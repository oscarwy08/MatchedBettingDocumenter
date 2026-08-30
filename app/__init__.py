import os
from pathlib import Path

from flask import Flask, g

_env_root = (os.environ.get("MBD_ROOT") or "").strip()
ROOT_DIR = Path(_env_root).expanduser().resolve() if _env_root else Path(__file__).resolve().parent.parent
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

    def _decimal(value):
        from decimal import InvalidOperation

        if value is None:
            return None
        try:
            text = str(value).strip()
        except Exception:  # noqa: BLE001
            return None
        if not text:
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None

    @app.template_filter("gbp")
    def gbp(value):
        amount = _decimal(value)
        if amount is None:
            return "–"
        sign = "−" if amount < 0 else ""
        return f"{sign}£{abs(amount):,.2f}"

    @app.template_filter("pnl")
    def pnl(value):
        amount = _decimal(value)
        if amount is None or amount == 0:
            return "pnl-zero"
        if amount > 0:
            return "pnl-pos"
        return "pnl-neg"

    @app.template_filter("labelize")
    def labelize(value):
        return str(value).replace("_", " ").title()

    @app.template_filter("ukdate")
    def ukdate(value):
        from app.dates import format_uk
        return format_uk(value)

    @app.template_filter("uktime")
    def uktime(value):
        from app.dates import format_uk_time
        return format_uk_time(value)

    @app.template_filter("ukdatetime")
    def ukdatetime(value):
        from app.dates import format_uk_datetime
        return format_uk_datetime(value)

    @app.template_filter("isodate")
    def isodate(value):
        from app.dates import format_iso_date
        return format_iso_date(value)

    @app.template_filter("isodatetime")
    def isodatetime(value):
        from app.dates import format_iso_datetime
        return format_iso_datetime(value)

    @app.context_processor
    def inject_globals():
        from app.settings import get as setting
        from app.version import VERSION

        from app.phone import phone_context

        return {"app_version": VERSION, "app_port": setting("port"), **phone_context()}

    boot = Session()
    try:
        seed_accounts(boot)
        from app.settings import get as setting

        if setting("excel_sync"):
            sync_workbook(boot)
    finally:
        boot.close()

    app.register_blueprint(bp)
    return app

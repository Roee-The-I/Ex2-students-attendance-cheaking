from flask import Flask

from .config import DB_PATH
from .database import close_db, init_db
from .routes import bp


def create_app(test_config=None):
    app = Flask(__name__, template_folder="../templates")
    app.config["DB_PATH"] = DB_PATH
    if test_config:
        app.config.update(test_config)
    app.teardown_appcontext(close_db)
    init_db(app.config["DB_PATH"])
    app.register_blueprint(bp)
    return app

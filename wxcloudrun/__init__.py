from flask import Flask

import config


app = Flask(__name__, instance_relative_config=True)
app.config["DEBUG"] = config.DEBUG
app.config.from_object("config")

from wxcloudrun import views  # noqa: E402,F401

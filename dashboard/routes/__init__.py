"""Dashboard route modules."""
from routes import desktop
from routes import logs
from routes import knowledge
from routes import recording


def setup(app):
    desktop.setup(app)
    logs.setup(app)
    knowledge.setup(app)
    recording.setup(app)

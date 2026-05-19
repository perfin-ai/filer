from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "Filer"
APP_AUTHOR = "Filer"


def app_data_dir() -> Path:
    p = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return app_data_dir() / "filer.db"


def db_url() -> str:
    return f"sqlite:///{db_path()}"


def celery_broker_dir() -> Path:
    d = app_data_dir() / "celery"
    (d / "in").mkdir(parents=True, exist_ok=True)
    (d / "processed").mkdir(parents=True, exist_ok=True)
    (d / "control").mkdir(parents=True, exist_ok=True)
    return d


def celery_broker_transport_options() -> dict[str, str]:
    d = celery_broker_dir()
    return {
        "data_folder_in": str(d / "in"),
        "data_folder_out": str(d / "in"),
        "data_folder_processed": str(d / "processed"),
        "control_folder": str(d / "control"),
    }

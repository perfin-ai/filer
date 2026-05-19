from celery import Celery

from filer_backend.config import celery_broker_transport_options

celery_app = Celery(
    "filer",
    broker="filesystem://",
    include=["filer_backend.indexing.tasks"],
)

celery_app.conf.update(
    broker_transport_options=celery_broker_transport_options(),
    task_ignore_result=True,
    task_track_started=True,
    worker_send_task_events=False,
    task_acks_late=True,
)

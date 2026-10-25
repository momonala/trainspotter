from spyglass import MetricsCollector

from .config import PROJECT_NAME
from .config import SPYGLASS_HOST

metrics = MetricsCollector(host=SPYGLASS_HOST, project=PROJECT_NAME)

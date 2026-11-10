"""Background queue that fills missing medicine fields from DrugSetu.

Lookups are slow by design (about one request every two seconds), so they never
run inside a request. Viewing an incomplete medicine queues it; the page renders
with the current data and the next view shows the filled fields.
"""

import queue
import threading

from backend.services.drugsetu_service import enrich_medicine, is_configured

_queue = queue.Queue(maxsize=100)
_pending = set()
_lock = threading.Lock()
_thread = None


def request_enrichment(medicine_id):
    """Queue a medicine for enrichment; returns False when it cannot be queued."""
    global _thread
    if not is_configured():
        return False
    with _lock:
        if medicine_id in _pending or _queue.full():
            return False
        _pending.add(medicine_id)
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_drain, name="drugsetu-enrichment", daemon=True)
            _thread.start()
    _queue.put(medicine_id)
    return True


def _drain():
    while True:
        medicine_id = _queue.get()
        try:
            enrich_medicine(medicine_id)
        except Exception as exc:
            print(
                f"DrugSetu enrichment failed for medicine {medicine_id}: "
                f"{type(exc).__name__}: {exc}"
            )
        finally:
            with _lock:
                _pending.discard(medicine_id)
            _queue.task_done()

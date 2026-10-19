import copy
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from . import apify
from .store import now

TERMINAL = {"complete", "failed", "cancelled", "needs_check"}


class Collector:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="collector")
        self.shutdown_event = threading.Event()
        self.stop_ids = set()
        # A restart must not silently submit more paid work.
        for job in self.store.jobs():
            if job["status"] in {"running", "queued"}:
                job["status"] = "interrupted"
                self.store.job(job)

    def plan(self, request):
        if request.budget_usd > self.settings.max_run_usd:
            raise ValueError(f"Budget exceeds server limit (${self.settings.max_run_usd:g})")
        per_job = min(Decimal(str(self.settings.max_job_usd)), Decimal(str(request.budget_usd)))
        allowed = min(
            request.max_jobs,
            self.settings.max_jobs,
            int(Decimal(str(request.budget_usd)) / per_job),
        )
        payloads = []
        if request.listing_ids:
            payloads.append(
                {
                    "label": f"Refresh {len(request.listing_ids)} listings",
                    "input": {
                        "urls": [
                            f"https://www.facebook.com/marketplace/item/{i}/"
                            for i in request.listing_ids
                        ],
                        "getListingDetails": True,
                        "getAllListingPhotos": False,
                        "onlyNewListings": False,
                        "maxPagesPerUrl": 1,
                    },
                }
            )
        else:
            if not request.search.search_centers or not request.search.terms():
                raise ValueError(
                    "Enter a make, model, or search term and add a search city before collecting"
                )
            for center in request.search.search_centers:
                for term in request.search.terms():
                    payloads.append(
                        {
                            "label": f"{term} · {center.name}",
                            "input": {
                                "urls": [apify.cars_search_url(center.model_dump(), term)],
                                "getListingDetails": True,
                                "getAllListingPhotos": False,
                                "onlyNewListings": False,
                                "strictFiltering": True,
                                "maxPagesPerUrl": request.max_pages,
                            },
                        }
                    )
        tasks = [
            {**p, "status": "waiting", "run_id": None, "cost_usd": 0, "notices": []}
            for p in payloads[:allowed]
        ]
        return {
            "tasks": tasks,
            "available_searches": len(payloads),
            "search_count": len(tasks),
            "per_job_usd": float(per_job),
            "maximum_charge_usd": float(per_job * len(tasks)),
            "truncated": len(tasks) < len(payloads),
        }

    def start(self, request):
        with self.lock:
            existing = self.store.job_by_request(request.request_id)
            if existing:
                return existing
            if not os.getenv("APIFY_TOKEN"):
                raise ValueError(
                    "Add APIFY_TOKEN to .env and restart the server to collect live listings"
                )
            if any(j["status"] in {"running", "queued", "interrupted"} for j in self.store.jobs()):
                raise ValueError(
                    "Finish, resume, or stop the current collection before starting another"
                )
            plan = self.plan(request)
            job = {
                **plan,
                "id": uuid.uuid4().hex,
                "request_id": request.request_id,
                "name": request.search.name,
                "search": request.search.model_dump(),
                "status": "queued",
                "started_at": now(),
                "updated_at": now(),
                "reported_cost_usd": 0,
            }
            self.store.job(job)
            self.executor.submit(self._run, job)
            return copy.deepcopy(job)

    def resume(self, identifier):
        with self.lock:
            job = next((j for j in self.store.jobs() if j["id"] == identifier), None)
            if not job or job["status"] != "interrupted":
                raise ValueError("Only interrupted collections can resume")
            if not os.getenv("APIFY_TOKEN"):
                raise ValueError("APIFY_TOKEN is missing")
            if any(j["status"] in {"running", "queued"} for j in self.store.jobs()):
                raise ValueError("Another collection is active")
            # A reserved POST without a returned ID has an unknown outcome.
            for index, task in enumerate(job["tasks"]):
                if task["status"] in {"starting", "waiting"}:
                    path = self.store.directory / "runs" / f"{job['id']}-{index}.json"
                    if path.exists():
                        import json

                        journal = json.loads(path.read_text())
                        if journal.get("run_id"):
                            task.update(run_id=journal["run_id"], status="active")
                        else:
                            task["status"] = "unknown"
            if any(t["status"] == "unknown" for t in job["tasks"]):
                job["status"] = "needs_check"
                self.store.job(job)
                raise ValueError(
                    "An Apify start has an unknown outcome. Check its run journal and Apify Console before collecting again."
                )
            job["status"] = "queued"
            self.store.job(job)
            self.executor.submit(self._run, job)
            return job

    def stop(self, identifier):
        job = next((j for j in self.store.jobs() if j["id"] == identifier), None)
        if not job:
            raise ValueError("Collection not found")
        self.stop_ids.add(identifier)
        if job["status"] == "interrupted":
            # Do not disguise potentially running provider jobs as cancelled.
            job["status"] = (
                "needs_check"
                if any(t["status"] in {"active", "starting"} for t in job["tasks"])
                else "cancelled"
            )
            self.store.job(job)
        return {"message": "Queued searches will stop. Active provider runs can finish and import."}

    def _publish(self, job):
        job["updated_at"] = now()
        job["reported_cost_usd"] = round(sum(t.get("cost_usd", 0) or 0 for t in job["tasks"]), 6)
        job["completed"] = sum(t["status"] == "complete" for t in job["tasks"])
        self.store.job(job)

    def _run(self, job):
        job["status"] = "running"
        deadline = time.monotonic() + 3600
        try:
            while time.monotonic() < deadline and not self.shutdown_event.is_set():
                for task in job["tasks"]:
                    if task["status"] != "active":
                        continue
                    try:
                        run = apify.request(f"/actor-runs/{task['run_id']}")["data"]
                        task["provider_status"] = run["status"]
                        task["cost_usd"] = run.get("usageTotalUsd", 0)
                        if run["status"] == "SUCCEEDED":
                            records, metadata = apify.fetch(task["run_id"])
                            task["notices"] = metadata.get("dataset_notices", [])
                            task["imported"] = self.store.import_records(
                                records, "apify:" + task["run_id"], run.get("finishedAt")
                            )
                            task["status"] = "complete"
                        elif run["status"] in {"FAILED", "ABORTED", "TIMED-OUT"}:
                            task["status"] = "failed"
                    except (RuntimeError, ValueError, KeyError, TypeError):
                        task["status"] = "needs_check"
                        task["error"] = (
                            "Could not verify or import this provider run. Check Apify Console; no retry was submitted."
                        )
                blocked = job["id"] in self.stop_ids or any(
                    t["status"] in {"unknown", "needs_check"} for t in job["tasks"]
                )
                for index, task in enumerate(job["tasks"]):
                    if task["status"] != "waiting":
                        continue
                    if blocked:
                        task["status"] = "cancelled"
                        continue
                    if sum(t["status"] == "active" for t in job["tasks"]) >= 4:
                        break
                    task["status"] = "starting"
                    self._publish(job)
                    try:
                        entry = apify.start(
                            task["input"],
                            f"{job['id']}-{index}",
                            self.store.directory / "runs",
                            job["per_job_usd"],
                        )
                        task.update(run_id=entry["run_id"], status="active")
                    except (RuntimeError, ValueError, KeyError, OSError):
                        task["status"] = "unknown"
                        task["error"] = (
                            "Run start was not confirmed. Check Apify Console before retrying."
                        )
                        blocked = True
                if not any(t["status"] in {"waiting", "active", "starting"} for t in job["tasks"]):
                    job["status"] = (
                        "needs_check"
                        if any(
                            t["status"] in {"unknown", "needs_check", "failed"}
                            for t in job["tasks"]
                        )
                        else "cancelled"
                        if job["id"] in self.stop_ids
                        else "complete"
                    )
                    self._publish(job)
                    return
                self._publish(job)
                self.shutdown_event.wait(10)
            job["status"] = "interrupted"
        except Exception:
            job["status"] = "interrupted"
            job["error"] = (
                "Collection stopped unexpectedly. Saved run IDs are available for recovery."
            )
        finally:
            self._publish(job)

    def close(self):
        self.shutdown_event.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

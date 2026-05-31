"""FastAPI entry point for the local pipeline console."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .jobs import PipelineJobManager, RunAlreadyActiveError
from .status import RunStatusService


ROOT = Path.cwd()
WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))
ALLOWED_FILE_ROOTS = (
    ROOT / "outputs",
    ROOT / "logs",
    ROOT / "state" / "runs",
    ROOT / "data",
    ROOT / "memory",
)

status_service = RunStatusService(ROOT)
job_manager = PipelineJobManager(root=ROOT, status_service=status_service)
app = FastAPI(title="Daily AI Insight Engine Console")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.get("/")
def index(request: Request):
    """Render the local console."""

    latest_run_id = status_service.latest_run_id()
    latest_status = (
        status_service.run_status(latest_run_id) if latest_run_id is not None else None
    )
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "latest_run_id": latest_run_id,
            "latest_status": latest_status,
        },
    )


@app.post("/api/runs/start")
def start_run() -> dict[str, str]:
    """Start one fresh run from the web console."""

    try:
        return job_manager.start_fresh_run()
    except RunAlreadyActiveError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "A pipeline run is already active.",
                "run_id": error.run_id,
                "status_url": f"/api/runs/{error.run_id}/status",
            },
        ) from error


@app.get("/api/runs/{run_id}/status")
def run_status(run_id: str) -> dict[str, object]:
    """Return UI-friendly status for a run."""

    return status_service.run_status(run_id)


@app.get("/files")
def file_response(path: str = Query(..., min_length=1)) -> FileResponse:
    """Serve a known local artifact path under the project root."""

    resolved = _safe_project_path(path)
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(resolved)


def _safe_project_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (ROOT / candidate).resolve()
    allowed_roots = [allowed.resolve() for allowed in ALLOWED_FILE_ROOTS]
    if not any(resolved == allowed or allowed in resolved.parents for allowed in allowed_roots):
        raise HTTPException(status_code=400, detail="path must stay within the project")
    return resolved


def main() -> None:
    """Run the local console server."""

    uvicorn.run("src.web.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()

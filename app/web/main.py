"""FastAPI app: one server-rendered page plus the small JSON API it polls.

Start:  uvicorn app.web.main:create_app_from_env --factory
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app import approvals, authorization, db, views
from app.restock import RestockError
from app.runner import Busy, RateLimited, RateLimiter, Runner

HERE = Path(__file__).resolve().parent


class StockUpdate(BaseModel):
    stock: int = Field(ge=0)


class ApprovalAnswer(BaseModel):
    approve: bool


def create_app(runner: Runner, *, llm_label: str) -> FastAPI:
    app = FastAPI(title="Restocking Agents with Spending Guardrails")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    rules = runner.rules

    def conn():
        return db.connect(runner.db_path)

    def policy_json() -> dict:
        return {"version": rules.version, **rules.snapshot()}

    def runner_state() -> dict:
        s = runner.state
        return {"busy": s.busy, "job": s.job, "last_error": s.last_error, "log": s.log_lines[-30:]}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        scenarios = [{"key": k, "title": v.get("title", k)} for k, v in runner.seed_data["scenarios"].items()]
        return templates.TemplateResponse(request, "index.html", {
            "policy": policy_json(), "scenarios": scenarios, "llm_label": llm_label,
        })

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/inventory")
    def get_inventory():
        c = conn()
        try:
            return views.inventory(c)
        finally:
            c.close()

    @app.patch("/inventory/{item}")
    def patch_inventory(item: str, body: StockUpdate):
        c = conn()
        try:
            row = c.execute("SELECT * FROM inventory WHERE item = ?", (item,)).fetchone()
            if row is None:
                raise HTTPException(404, f"No item {item}")
            c.execute("UPDATE inventory SET stock = ? WHERE item = ?", (body.stock, item))
            low = body.stock < row["reorder_level"]
        finally:
            c.close()
        started, note = False, None
        if low:
            try:
                runner.start_item_restock(item)
                started = True
            except (Busy, RestockError, RateLimited) as e:
                note = str(e)
        return {"item": item, "stock": body.stock, "low": low, "run_started": started, "note": note}

    @app.get("/runs/latest")
    def latest_run():
        c = conn()
        try:
            run_id = views.latest_run_id(c)
            detail = views.run_detail(c, run_id) if run_id else None
        finally:
            c.close()
        return {"runner": runner_state(), "detail": detail}

    @app.get("/runs")
    def list_runs(limit: int = 10):
        c = conn()
        try:
            return views.recent_runs(c, max(1, min(limit, 50)))
        finally:
            c.close()

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
        c = conn()
        try:
            detail = views.run_detail(c, run_id)
        finally:
            c.close()
        if detail is None:
            raise HTTPException(404, f"No run {run_id}")
        return detail

    @app.get("/runs/{run_id}/authorization")
    def get_authorization(run_id: str):
        c = conn()
        try:
            record = authorization.build(c, run_id)
        except authorization.NotFound as e:
            raise HTTPException(404, str(e))
        finally:
            c.close()
        return JSONResponse(record, headers={
            "Content-Disposition": f'inline; filename="authorization_{run_id}.json"'})

    @app.get("/approvals")
    def get_approvals(status: str | None = None):
        c = conn()
        try:
            approvals.expire_stale(c)
            return views.approvals(c, status)
        finally:
            c.close()

    @app.post("/approvals/{approval_id}", status_code=202)
    def answer_approval(approval_id: int, body: ApprovalAnswer):
        try:
            runner.approve(approval_id, body.approve)
        except approvals.ApprovalError as e:
            raise HTTPException(404 if "No approval" in str(e) else 409, str(e))
        except Busy as e:
            raise HTTPException(409, str(e))
        return {"accepted": True, "approve": body.approve}

    @app.get("/audit")
    def get_audit():
        c = conn()
        try:
            return views.audit(c)
        finally:
            c.close()

    @app.get("/policy")
    def get_policy():
        return policy_json()

    @app.post("/scenarios/{name}", status_code=202)
    def start_scenario(name: str):
        try:
            runner.start_scenario(name.lower())
        except KeyError:
            raise HTTPException(404, f"No scenario {name}")
        except Busy as e:
            raise HTTPException(409, str(e))
        except RateLimited as e:
            raise HTTPException(429, str(e))
        return {"started": name.lower()}

    @app.post("/reset", status_code=202)
    def reset():
        try:
            runner.reset()
        except Busy as e:
            raise HTTPException(409, str(e))
        return {"reset": True}

    return app


def create_app_from_env() -> FastAPI:
    """Production wiring: real PayPal gateway, Gemini if LLM_API_KEY is set, else fixed deals."""
    from dotenv import load_dotenv

    from app import seed
    from app.config import load_settings
    from app.llm import GeminiLLM
    from app.paypal.gateway import PayPalGateway
    from app.policy.rules import load_rules

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = load_settings()
    rules = load_rules()
    seed_data = seed.load_seed_file()
    db_path = db.db_path_from_env()

    try:
        llm = GeminiLLM.from_env()
        llm_label = f"Gemini · {', '.join(llm.models)}"
    except Exception as e:  # scenarios still run on their fixed deals; say why in the log and on the page
        llm = None
        missing_key = not os.getenv("LLM_API_KEY", "").strip()
        llm_label = "fixed deals (no LLM_API_KEY)" if missing_key else "fixed deals (LLM setup failed: see server log)"
        logging.getLogger("app").warning("LLM disabled, using fixed deals: %s: %s", type(e).__name__, e)

    c = db.connect(db_path)
    try:
        if c.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 0:
            seed.reset(c, seed_data)  # first boot: load the seed
    finally:
        c.close()

    # Public demo guard: restocks per rolling hour across the whole site (0 turns it off).
    max_runs = int(os.getenv("DEMO_MAX_RUNS_PER_HOUR", "20"))
    runner = Runner(db_path=db_path, rules=rules, seed_data=seed_data, shop_email=settings.shop.email,
                    gateway_factory=lambda: PayPalGateway(settings), llm=llm, limiter=RateLimiter(max_runs))
    logging.getLogger("app").info("demo limit: %s restocks per hour", max_runs or "no")
    return create_app(runner, llm_label=llm_label)

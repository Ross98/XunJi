from fastapi import FastAPI


def register_routes(app: FastAPI):
    from routes import (
        dashboard,
        training,
        diet,
        body,
        plan,
        health,
    )
    app.include_router(dashboard.router)
    app.include_router(training.router)
    app.include_router(diet.router)
    app.include_router(body.router)
    app.include_router(plan.router)
    app.include_router(health.router)

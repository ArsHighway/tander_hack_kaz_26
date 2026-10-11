from fastapi import APIRouter, Depends, Request, Response

from app.health import Check, Readiness, build_checks, check_all

router = APIRouter(prefix="/health", tags=["health"])


def get_checks(request: Request) -> list[Check]:
    return build_checks(request.app.state.resources)


def get_timeout(request: Request) -> float:
    return request.app.state.resources.settings.health_timeout_s


@router.get("")
async def liveness() -> dict[str, str]:
    """Процесс жив. Зависимости не трогает."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(
    response: Response,
    checks: list[Check] = Depends(get_checks),
    timeout: float = Depends(get_timeout),
) -> Readiness:
    """Готовность зависимостей: 200 — обязательные в порядке, 503 — одна из обязательных лежит."""
    result = await check_all(checks, timeout)
    if result.status == "fail":
        response.status_code = 503
    return result

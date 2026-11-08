"""The unified error body `{code, message, details}` (api.md section 1.2)."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import ApiError

HTTP_ERRORS = {
    404: ("not_found", "接口不存在"),
    405: ("method_not_allowed", "不支持这个请求方法"),
}


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return body


def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status_code)


def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    details = {"errors": jsonable_encoder(exc.errors(), custom_encoder={Exception: str})}
    return JSONResponse(error_body("validation_error", "请求参数不合法", details), status_code=422)


def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code, message = HTTP_ERRORS.get(exc.status_code, ("http_error", str(exc.detail)))
    return JSONResponse(error_body(code, message), status_code=exc.status_code)


def database_error_handler(_request: Request, _exc: OperationalError) -> JSONResponse:
    return JSONResponse(
        error_body("dependency_unavailable", "数据库暂时不可用，请稍后重试"), status_code=503
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(StarletteHTTPException, http_error_handler)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(OperationalError, database_error_handler)  # ty: ignore[invalid-argument-type]

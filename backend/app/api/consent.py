"""Unauthenticated member confirmation links."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.domains.consent import service

router = APIRouter(tags=["member confirmation"])


class DecisionBody(BaseModel):
    otp: str = Field(pattern=r"^[0-9]{6}$")
    decision: str = Field(pattern="^(confirm|reject)$")


def _ip(request: Request) -> str:
    return request.client.host if request.client else "127.0.0.1"


@router.get("/confirm/{coop_slug}/{token}")
async def get_confirmation(coop_slug: str, token: str) -> dict[str, object]:
    return await service.view(coop_slug, token)


@router.post("/confirm/{coop_slug}/{token}/otp", status_code=202)
async def request_otp(coop_slug: str, token: str, request: Request) -> dict[str, str]:
    await service.send_otp(coop_slug, token, _ip(request), service.ConsoleOtpSender())
    return {"status": "sent"}


@router.post("/confirm/{coop_slug}/{token}/decision", status_code=202)
async def post_decision(
    coop_slug: str, token: str, body: DecisionBody, request: Request,
    user_agent: Annotated[str, Header(alias="User-Agent")] = "",
) -> dict[str, str]:
    result = await service.decide(coop_slug, token, body.otp, body.decision, _ip(request), user_agent)
    if result == "terms_changed":
        raise HTTPException(status_code=409, detail="terms changed")
    if result != "recorded":
        raise HTTPException(status_code=400, detail="Confirmation unavailable")
    return {"status": "recorded"}

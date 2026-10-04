from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    # Legacy field from the retired "Remember me" box, kept to document the old
    # contract and ignored: every sign-in is the 30-day sliding session. (Unknown
    # fields are dropped anyway - this model sets no `extra` config.)
    remember_me: Optional[bool] = None


class LoginResponse(BaseModel):
    # Opaque staff session token - the FE stores it in the NextAuth cookie and
    # sends it as `Authorization: Bearer <token>` to every /api/v1/* call.
    token: str
    id: str
    # Optional since identity S0: a phone-only user has no email (S1 phone sign-in).
    email: EmailStr | None = None
    name: str | None = None
    avatar: str | None = None
    status: str
    role_id: str
    role_name: str | None = None
    role_ids: list[str] = []
    # Where this user lands after signing in (AC-28): a salesperson's portal
    # home, the CRM home for anyone with a permission, else the portal home.
    home_path: str | None = None


class PhoneRequestCodeRequest(BaseModel):
    """POST /api/v1/auth/phone/request-code (identity S1, AC-21)."""

    phone: str


class PhoneRequestCodeResponse(BaseModel):
    # Same shape for every number, known or not (no enumeration).
    sent_to: str
    expires_in_seconds: int
    resend_in_seconds: int


class PhoneVerifyRequest(BaseModel):
    """POST /api/v1/auth/phone/verify (identity S1, AC-24)."""

    phone: str
    code: str = Field(..., pattern=r"^\d{6}$")


class SetPasswordRequest(BaseModel):
    """POST /api/v1/auth/password (identity S1, plan 5.3)."""

    current_password: str | None = None
    new_password: str = Field(..., min_length=8)

    @field_validator("new_password")
    @classmethod
    def _new_password_fits_bcrypt(cls, v: str) -> str:
        # Note fix (security round, #1280): bcrypt truncates/raises past 72
        # BYTES - without this, a longer password 500s instead of a clean 422.
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 bytes or fewer.")
        return v


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str


class SignupResponse(BaseModel):
    id: str
    email: EmailStr
    name: str
    message: str


class ResetPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordResponse(BaseModel):
    message: str


class ChangePasswordRequest(BaseModel):
    token: str
    new_password: str


class ChangePasswordResponse(BaseModel):
    message: str


class VerifyEmailRequest(BaseModel):
    token: str


class VerifyEmailResponse(BaseModel):
    message: str


class VerifyResetTokenRequest(BaseModel):
    token: str


class VerifyResetTokenResponse(BaseModel):
    valid: bool = True
    email: str | None = None  # optional masked email for display


class SessionInfo(BaseModel):
    """One active staff session for the "your devices" UI. No raw token/UUID shown."""
    id: str
    device_label: str
    ip_address: str | None = None
    last_seen_at: str | None = None
    created_at: str | None = None
    current: bool = False


class MessageResponse(BaseModel):
    message: str
    count: int | None = None


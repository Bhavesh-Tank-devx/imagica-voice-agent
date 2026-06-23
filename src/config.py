"""Application configuration via ``pydantic-settings``.

Every environment variable the app reads is declared here, grouped by domain.
Each group is a ``BaseSettings`` subclass with a module-level singleton, so
code does ``from src.config import twilio_settings`` rather than calling
``os.getenv`` ad hoc. Values are read from the process environment and a local
``.env`` file (the latter is optional in production).
"""
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_CONFIG = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class AppSettings(BaseSettings):
    """Global application settings (timezone, calling hours, public URL)."""

    model_config = _ENV_CONFIG

    BASE_URL: str = "https://redressable-spectrochemical-aarav.ngrok-free.dev"
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "Asia/Kolkata"

    # TRAI-compliant calling window, in local (TIMEZONE) hours [start, end).
    # NOTE: CALLING_HOURS_END defaults to 23 for development — set to 21 for production.
    CALLING_HOURS_START: int = 9
    CALLING_HOURS_END: int = 23


class TwilioSettings(BaseSettings):
    """Twilio credentials for outbound dialing and SMS fallback."""

    model_config = _ENV_CONFIG

    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""


class ElevenLabsSettings(BaseSettings):
    """ElevenLabs Conversational AI credentials and agent IDs."""

    model_config = _ENV_CONFIG

    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_AGENT_ID: str = ""
    ELEVENLABS_KAYA_AGENT_ID: str = ""
    ELEVENLABS_PHONE_NUMBER_ID: str = ""
    ELEVENLABS_WEBHOOK_SECRET: str = ""
    # ASR keyword boosting must first be enabled in the ElevenLabs dashboard
    # (Agent -> Security/Overrides -> asr.keywords) or session init is rejected.
    KAYA_ASR_KEYWORDS: bool = False

    @property
    def kaya_agent_id(self) -> str:
        """Kaya agent ID, falling back to the default agent ID when unset."""
        return self.ELEVENLABS_KAYA_AGENT_ID or self.ELEVENLABS_AGENT_ID


class SMSSettings(BaseSettings):
    """SMS provider credentials (MSG91 preferred, Twilio fallback)."""

    model_config = _ENV_CONFIG

    MSG91_AUTH_KEY: str = ""
    MSG91_TEMPLATE_ID: str = ""
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = "+13185043576"


class LiveKitSettings(BaseSettings):
    """LiveKit + Gemini settings for the legacy realtime agent worker."""

    model_config = _ENV_CONFIG

    LIVEKIT_URL: str = ""
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""
    LIVEKIT_SIP_TRUNK_ID: str = ""
    CCT_DEMO_PHONE: str = ""  # Imagica CCT queue number for live handoff

    GOOGLE_CLOUD_PROJECT: str = ""
    GOOGLE_CLOUD_LOCATION: str = "us-central1"
    GEMINI_MODEL: str = "gemini-live-2.5-flash-native-audio"


app_settings = AppSettings()
twilio_settings = TwilioSettings()
elevenlabs_settings = ElevenLabsSettings()
sms_settings = SMSSettings()
livekit_settings = LiveKitSettings()

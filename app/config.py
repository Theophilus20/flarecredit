"""Application settings loaded from .env."""
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    coston2_rpc: str = "https://coston2-api.flare.network/ext/C/rpc"

    identity_registry: str = "0x0000000000000000000000000000000000000000"
    credit_registry: str = "0x0000000000000000000000000000000000000000"
    lending_pool: str = "0x0000000000000000000000000000000000000000"
    fxrp_token: str = "0x0000000000000000000000000000000000000000"
    instruction_sender: str = "0x0000000000000000000000000000000000000000"

    fdc_verifier_url: str = "https://fdc-verifiers-testnet.flare.network"
    fdc_verifier_api_key: str = "00000000-0000-0000-0000-000000000000"
    da_layer_url: str = "https://ctn2-data-availability.flare.network"

    enclave_url: str = ""
    expected_code_hash: str = "0x" + "00" * 32

    backend_private_key: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()

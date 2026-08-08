"""Infrastructure adapters for unstable upstream Gemini Web contracts."""

from .rpc_contracts import (
    RPC_CONTRACTS,
    WEB_FEATURE_PROBE_CONTRACTS,
    WEB_FEATURE_PROBE_KEYS,
    WEB_FEATURE_PROBES,
    RPCContract,
    RawRPCData,
    execute_contract,
    get_contract,
    get_probe,
)

__all__ = [
    "RPC_CONTRACTS",
    "WEB_FEATURE_PROBE_CONTRACTS",
    "WEB_FEATURE_PROBE_KEYS",
    "WEB_FEATURE_PROBES",
    "RPCContract",
    "RawRPCData",
    "execute_contract",
    "get_contract",
    "get_probe",
]

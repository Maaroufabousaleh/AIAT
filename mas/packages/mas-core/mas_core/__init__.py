"""
mas-core — shared library for the AIAT Multi-Agent System.

Submodules
----------
protocols   Canonical MessageEnvelope, domain models, enums.
policy      Role-based communication & tool-access policy engine.
llm_gateway Async LLM client targeting an OpenAI-compatible provider.
agent_runtime BaseAgent, WorkerAgent, AdminAgent, ExecutiveAgent, CSuiteAgent, SubAgent.
workflow    Deterministic workflow controller (state machine + watchdog).
memory      AgentStorage (Postgres), BlobClient (MinIO), checkpoint helpers.
util        Structured logging, budget tracking, LRU idempotency set.
"""

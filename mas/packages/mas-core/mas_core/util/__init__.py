"""
util — Cross-cutting utilities.

Exports (Phase 4 + general)
----------------------------
get_logger(name)    Returns a structlog logger bound with agent_id / team_id
                    context (JSON output in production, coloured in dev).
BudgetTracker       Tracks remaining LLM calls, tool calls, subtasks, cost.
                    Raises BudgetExhaustedError when any cap is hit.
LRUIdempotencySet   Thread-safe LRU set (cachetools.LRUCache) of processed
                    message_id values; size-1000 default.
configure_logging   Call once at startup; reads LOG_LEVEL / LOG_FORMAT env vars.
new_message_id      Returns a new ULID string suitable for MessageEnvelope.message_id.
utcnow              Returns timezone-aware datetime.now(UTC) (replaces naive utcnow()).
"""

# Populated incrementally across phases.
